#!/usr/bin/env python3
"""内容排期表生成（零依赖）。

把内容池按平台节奏与发布窗口排进日历，输出可导入日历的 CSV / Markdown 排期表。
触发与执行分离：本脚本只负责"排"，实际发布由人工或平台的定时能力完成。

v2 修订（2026-09）
------------------
v1 的三处缺陷：
  1. **文档与实现打架**：docstring 里的示例写了 `--cadence 每平台每日1条`，
     但 argparse 里**根本没有 --cadence**，照文档敲会直接报
     「unrecognized arguments」；而"节奏"逻辑本身也不存在（写死每平台每日 1 条）。
  2. **静默丢内容**：内容池条数超过 `days × 每平台每日条数` 时，多出来的条目
     被直接丢弃且**不报警**，排期表看起来"已排完"。
  3. `SLOTS` 是写死的"各平台发布时段"，无来源无版本，而使用它的工作流要求
     「引用规则前先校验时效」。
v2 起：
  - 实现 `--cadence`（支持 `每平台每日N条` / `每平台每周N条`），并保留
    `--per-day N` 简写；
  - 未被排进日历的条目逐条列出并计入 `unscheduled`，退出码 3 提示需要处理；
  - `SLOTS` 带版本与来源说明，可用 `--slots` 覆盖；未收录平台给出明确告警；
  - 4. **输入键名兼容**：v2 只认 `{"items": [...]}`，而技能自带示例文件写的是
    `{"pool": [...]}`（与 `--pool` 参数名一致），两者打架的直接后果是
    **示例一跑就报「内容池为空」**——把可用示例变成废示例。现改为一并接受
    `items` / `pool` / 裸数组，并在输出里回显实际用的键名，便于对照。

用法:
    python3 schedule_plan.py --pool pool.json --start 2026-09-28 --days 7
    python3 schedule_plan.py --pool pool.json --cadence 每平台每日2条
    python3 schedule_plan.py --pool pool.json --csv out.csv
    python3 schedule_plan.py --selftest

输入 pool.json（三种等价写法都接受，见下方 v2 第 4 条）：
    {"items":[{"title":"...","platform":"小红书","type":"图文","priority":1}]}
    {"pool":[...]}     # 与 --pool 参数名一致，示例文件用的是这种
    [...]              # 裸数组
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

# 各平台建议发布时段（内部基线，非平台官方规定；改动时同步升版本）
SLOTS_VERSION = "2026.09-baseline"
SLOTS_SOURCE = "内部基线（按公开运营经验整理，未逐条核验官方数据）；请按自己账号的实际流量时段覆盖"
SLOTS = {"小红书": "19:00", "抖音": "12:00", "公众号": "08:00", "知乎": "21:00",
         "微博": "10:00", "B站": "18:00", "视频号": "20:00", "淘宝": "10:00",
         "快手": "18:00", "今日头条": "08:00", "百家号": "09:00", "拼多多": "10:00"}

_CADENCE_RE = re.compile(r"每平台每(日|天|周)\s*(\d+)\s*条")


def parse_cadence(spec: str) -> tuple[int, int]:
    """把 `每平台每日N条` / `每平台每周N条` 解析为 (每平台的每日条数, 天粒度)。

    返回 (per_day, week_days)：周节奏折算成「每 7 天 N 条」→ per_day 向上取整，
    并额外返回 week_days 供调用方说明口径。
    """
    m = _CADENCE_RE.search(spec or "")
    if not m:
        raise ValueError(f"无法解析节奏：{spec!r}；应为「每平台每日N条」或「每平台每周N条」")
    unit, n = m.group(1), int(m.group(2))
    if n <= 0:
        raise ValueError("节奏条数必须为正整数")
    if unit == "周":
        return max(1, -(-n // 7)), 7  # 向上取整
    return n, 1


def plan(pool: list[dict], start: date, days: int,
         per_day: int = 1) -> tuple[list[dict], list[dict], list[str]]:
    """返回 (排期行, 未排入的条目, 未收录平台的告警)。"""
    by_platform: dict[str, list] = {}
    for it in pool:
        by_platform.setdefault(it.get("platform", "通用"), []).append(it)
    for v in by_platform.values():
        v.sort(key=lambda x: x.get("priority", 99))

    warns = [f"平台「{p}」未在时段基线表中，已按 19:00 兜底，请人工确认"
             for p in by_platform if p not in SLOTS]

    rows: list[dict] = []
    idx = {p: 0 for p in by_platform}
    for d in range(days):
        day = start + timedelta(days=d)
        for plat, items in by_platform.items():
            for slot_i in range(per_day):
                pool_i = idx[plat]
                if pool_i >= len(items):
                    break
                it = items[pool_i]
                idx[plat] += 1
                rows.append({"date": day.isoformat(), "weekday": "一二三四五六日"[day.weekday()],
                             "time": SLOTS.get(plat, "19:00"), "platform": plat,
                             "seq": slot_i + 1,
                             "title": it.get("title", "未命名"), "type": it.get("type", "图文")})

    unscheduled = [it for plat, items in by_platform.items() for it in items[idx[plat]:]]
    return rows, unscheduled, warns


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="内容排期表生成")
    ap.add_argument("--pool", required=True, help="内容池 JSON")
    ap.add_argument("--start", default=date.today().isoformat())
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--cadence", default="每平台每日1条",
                    help="发布节奏，如「每平台每日1条」「每平台每周3条」")
    ap.add_argument("--per-day", type=int, help="简写：每平台每日条数（覆盖 --cadence）")
    ap.add_argument("--slots", help="覆盖发布时段 JSON，如 '{\"小红书\":\"20:00\"}'")
    ap.add_argument("--csv", help="导出 CSV 路径")
    return ap


def _selftest() -> int:
    """内嵌回归自测：核心断言是「文档承诺的参数真的存在」「不静默丢内容」。"""
    checks: list[tuple[str, bool, str]] = []

    def ck(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))

    # 1) 文档里出现的参数必须真的在 argparse 里（v1 的 --cadence 只在 docstring 里存在）
    opts: set[str] = set()
    for a in build_parser()._actions:
        opts.update(a.option_strings)
    for flag in ("--pool", "--start", "--days", "--cadence", "--per-day", "--slots", "--csv"):
        ck(f"argparse 里真的存在 {flag}", flag in opts, f"实际 {sorted(opts)}")
    # docstring 里的示例参数也要能被解析器接受（--selftest 是脚本内置开关，不算）
    doc = (__doc__ or "")
    for m in re.finditer(r"--([a-z][a-z-]*)", doc):
        if m.group(1) == "selftest":
            continue
        ck(f"docstring 提到的 --{m.group(1)} 已实现", f"--{m.group(1)}" in opts)

    # 2) 节奏解析
    ck("每日1条解析", parse_cadence("每平台每日1条") == (1, 1))
    ck("每日3条解析", parse_cadence("每平台每日3条") == (3, 1))
    ck("每周7条解析为每日1条", parse_cadence("每平台每周7条") == (1, 7))
    ck("每周3条向上取整为每日1条", parse_cadence("每平台每周3条") == (1, 7))
    try:
        parse_cadence("随便写")
        ck("非法节奏报错", False, "未抛异常")
    except ValueError:
        ck("非法节奏报错", True)

    # 3) 不静默丢内容：池子大于容量时，多出的条目必须被列出
    pool = [{"title": f"t{i}", "platform": "小红书"} for i in range(10)]
    rows, unsched, _ = plan(pool, date(2026, 9, 28), 7, per_day=1)
    ck("排期行数=days×per_day×平台数", len(rows) == 7, f"实际 {len(rows)}")
    ck("未排入条目被列出", len(unsched) == 3, f"实际 {len(unsched)}")
    ck("未排入条目内容正确", [u["title"] for u in unsched] == ["t7", "t8", "t9"])

    # 4) 容量足够时不得有未排入项
    rows2, unsched2, _ = plan(pool[:5], date(2026, 9, 28), 7, per_day=1)
    ck("容量足够时无未排入项", not unsched2, f"实际 {unsched2}")

    # 5) per_day>1 时同一天可有多个时段，且 seq 递增
    rows3, _, _ = plan(pool, date(2026, 9, 28), 2, per_day=3)
    ck("per_day=3 时 2 天排 6 条", len(rows3) == 6, f"实际 {len(rows3)}")
    day1 = [r for r in rows3 if r["date"] == "2026-09-28"]
    ck("同日多时段 seq 递增", [r["seq"] for r in day1] == [1, 2, 3])

    # 6) 未收录平台要告警
    _, _, warns = plan([{"title": "x", "platform": "某小众平台"}], date(2026, 9, 28), 1)
    ck("未收录平台告警", any("未在时段基线表" in w for w in warns))

    # 7) weekday 映射正确（2026-09-28 是周一）
    three = [{"title": f"x{i}", "platform": "小红书"} for i in range(3)]
    rows4, _, _ = plan(three, date(2026, 9, 28), 3)
    ck("weekday 映射正确", [r["weekday"] for r in rows4] == ["一", "二", "三"],
       f"实际 {[r['weekday'] for r in rows4]}")

    # 8) 时段表带版本与来源
    ck("SLOTS 带版本", bool(SLOTS_VERSION))
    ck("SLOTS 带来源说明", bool(SLOTS_SOURCE) and "内部基线" in SLOTS_SOURCE)

    # 9) 输入键名兼容（v1/v2 曾只认 items，而示例文件写 pool → 示例必判空池）
    one = [{"title": "a", "platform": "小红书"}]
    ck("items 键被识别", load_pool({"items": one})[0] == one)
    ck("pool 键被识别", load_pool({"pool": one})[0] == one)
    ck("裸数组被识别", load_pool(one)[0] == one)
    ck("未知键但值为列表时仍识别", load_pool({"whatever": one})[0] == one)
    ck("回显实际键名", load_pool({"pool": one})[1] == "pool")
    ck("空对象不误报有内容", load_pool({})[0] == [])
    ck("非字典条目被过滤", load_pool({"items": [1, "x", {"title": "ok"}]})[0]
       == [{"title": "ok"}])

    # 10) 技能自带的示例输入必须真的能被读成非空池（本次缺陷的现场断言）
    example = (Path(__file__).resolve().parent.parent.parent / "skills"
               / "scheduled-dispatch" / "examples" / "input" / "pool.json")
    if example.exists():
        ex_pool, _ = load_pool(json.loads(example.read_text(encoding="utf-8")))
        ck("自带示例 pool.json 解析非空", bool(ex_pool), f"实际 {len(ex_pool)} 条")
    else:
        ck("自带示例 pool.json 存在", True, "（副本位置不同，跳过）")

    fails = [(n, d) for n, ok, d in checks if not ok]
    for n, ok, d in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {n}" + (f" —— {d}" if not ok and d else ""))
    print(f"schedule_plan 自测：{len(checks) - len(fails)}/{len(checks)} 通过")
    return 1 if fails else 0


POOL_KEYS = ("items", "pool", "list", "contents")


def load_pool(raw: object) -> tuple[list[dict], str]:
    """把内容池 JSON 归一成 (条目列表, 实际使用的键名)。

    接受 `{"items": [...]}` / `{"pool": [...]}` / 裸数组三种写法。
    键名不在已知集合里但值是列表时，取第一个列表值并回显其键名——
    宁可让调用方看到真实的键名，也不要静默判空。
    """
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)], "(裸数组)"
    if isinstance(raw, dict):
        for k in POOL_KEYS:
            v = raw.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)], k
        for k, v in raw.items():
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                return list(v), k
    return [], "(未识别)"


def main() -> int:
    if "--selftest" in sys.argv:
        return _selftest()

    args = build_parser().parse_args()

    try:
        raw = json.loads(Path(args.pool).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"错误：无法读取内容池 —— {exc}", file=sys.stderr)
        return 2
    pool, pool_key = load_pool(raw)
    if not pool:
        print(f"错误：内容池为空（已识别的键名：{pool_key}；"
              f"支持 items / pool / 裸数组，见脚本 docstring）", file=sys.stderr)
        return 1

    slots_note = ""
    if args.slots:
        try:
            SLOTS.update(json.loads(args.slots))
        except json.JSONDecodeError as exc:
            print(f"错误：--slots 不是合法 JSON —— {exc}", file=sys.stderr)
            return 2
        slots_note = "（已用 --slots 覆盖）"

    if args.per_day is not None:
        per_day = args.per_day
        cadence_txt = f"每平台每日{per_day}条（--per-day 覆盖）"
    else:
        try:
            per_day, _week_days = parse_cadence(args.cadence)
        except ValueError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 2
        cadence_txt = args.cadence
    if per_day <= 0:
        print("错误：每平台每日条数必须为正整数", file=sys.stderr)
        return 2

    rows, unscheduled, warns = plan(pool, date.fromisoformat(args.start), args.days, per_day)

    if args.csv:
        with open(args.csv, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["date", "weekday", "time", "platform",
                                              "seq", "title", "type"])
            w.writeheader()
            w.writerows(rows)
        print(f"已导出 {args.csv}（{len(rows)} 条）")
    else:
        print(f"节奏：{cadence_txt}　时段基线：{SLOTS_VERSION}　池键名：{pool_key}{slots_note}")
        print("| 日期 | 周 | 时间 | 平台 | 标题 | 类型 |")
        print("|---|---|---|---|---|---|")
        for r in rows:
            seq = f"（第{r['seq']}条）" if per_day > 1 else ""
            print(f"| {r['date']} | {r['weekday']} | {r['time']} | {r['platform']} | "
                  f"{r['title']}{seq} | {r['type']} |")
        print(f"\n共排 {len(rows)} 条，跨度 {args.days} 天")

    for w in warns:
        print(f"  ⚠ {w}", file=sys.stderr)
    if unscheduled:
        print(f"\n⚠ 有 {len(unscheduled)} 条内容**未能排入**本排期窗口"
              f"（超出 {args.days} 天 × 每平台每日 {per_day} 条的容量）：", file=sys.stderr)
        for it in unscheduled[:20]:
            print(f"    - [{it.get('platform', '通用')}] {it.get('title', '未命名')}", file=sys.stderr)
        if len(unscheduled) > 20:
            print(f"    …… 另有 {len(unscheduled) - 20} 条", file=sys.stderr)
        print("    请拉长 --days、提高 --per-day，或拆分多轮排期。", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
