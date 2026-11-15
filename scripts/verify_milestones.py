#!/usr/bin/env python3
"""里程碑步长回归：1K/10K、1M/10M 与最高档价格下限。

不读取真实数据文件；小额项目用临时 CSV 构造。
用法(Windows venv):
    .venv/Scripts/python.exe scripts/verify_milestones.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("TRACKER_LOG_PATH", os.path.join(tempfile.gettempdir(), "verify_milestones.log"))

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import matplotlib

matplotlib.use("Agg")
import charts  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def ok(label: str) -> None:
    PASS.append(label)
    print(f"PASS {label}")


def bad(label: str, detail: str) -> None:
    FAIL.append(label)
    print(f"FAIL {label}: {detail}")


def check_function() -> None:
    cases = [
        (4110.0, 148.0, 1_000),
        (42806068.9, 598.0, 1_000_000),
        (40000.0, 598.0, 2_500),
        (100000.0, 0.0, 2_500),
        (0.0, 0.0, 1_000),
    ]
    for total, max_tier, expected in cases:
        actual = charts.choose_milestone_step(total, max_tier)
        if actual != expected:
            bad("步长计算", f"total={total} max_tier={max_tier} -> {actual}, 期望 {expected}")
            return
    ok("步长计算: 1K/10K、1M/10M、最高档下限")


def check_small_project() -> None:
    with tempfile.TemporaryDirectory(prefix="verify_milestones_") as tmpdir:
        path = Path(tmpdir) / "linglan.csv"
        path.write_text(
            "timestamp,总人数,总金额,无偿支持,Lv.1 早一点来找灵兰（￥17）,"
            "Lv.2 来找灵兰玩呀（￥21）,Lv.3 吧唧也想贴贴（￥38）,"
            "Lv.4 小可爱都带上（￥78）,Lv.5 今天也想抱抱（￥148）\r\n"
            "2026-11-15 00:00:00,129,3781.0,0,100,25,3,9,5\r\n"
            "2026-11-15 12:25:00,130,4110.0,0,100,25,3,9,6\r\n",
            encoding="utf-8",
        )
        ch = charts.BaseChart(csv_file=str(path))
        if not ch.load(quiet=True):
            bad("小额项目加载", "load() 返回 False")
            return
        if (ch.milestone_step, ch.major_step) != (1_000, 10_000):
            bad("小额项目步长", f"{ch.milestone_step}/{ch.major_step}, 期望 1000/10000")
            return
        marks = ch.milestones()
        if [m["value"] for m in marks] != [4_000.0]:
            bad("小额项目里程碑", f"{[m['value'] for m in marks]}, 期望 [4000.0]")
            return
        ok("小额项目: 1000/10000，数据起点后的首个关口为 4,000")


def main() -> int:
    check_function()
    check_small_project()
    print(f"\n{len(PASS)} PASS / {len(FAIL)} FAIL")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
