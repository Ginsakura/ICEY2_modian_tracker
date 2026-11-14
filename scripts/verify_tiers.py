#!/usr/bin/env python3
"""档位列价格身份、旧列名兼容与 append_tiers 的回归测试。

所有文件都写在临时目录，不触碰真实 data.csv。
"""
from __future__ import annotations

import csv
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import modian_tracker as mt  # noqa: E402
import tier_columns as tc  # noqa: E402

BOM = b"\xef\xbb\xbf"
PASS: list[str] = []
FAIL: list[str] = []


def ok(label: str) -> None:
    PASS.append(label)
    print(f"PASS {label}")


def bad(label: str, detail: str) -> None:
    FAIL.append(label)
    print(f"FAIL {label}: {detail}")


def tier(price: str, title: str, count: int) -> tc.Tier:
    return tc.Tier(price, Decimal(price), title, count, False)


def free(count: int = 0) -> tc.Tier:
    return tc.Tier(None, None, tc.FREE_COLUMN, count, True)


def rows_of(path: Path) -> list[list[str]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.reader(f) if r]


def main() -> int:
    try:
        assert tc.parse_price("Lv.1 早鸟 数字版（￥42）") == Decimal("42")
        assert tc.parse_price("Lv1（￥45.6）") == Decimal("45.6")
        assert tc.parse_price("Lv.2 标题里有 ￥88 但不是价格（￥99）") == Decimal("99")
        assert tc.parse_price("Lv.2 标题里有 ￥88") is None
        assert tc.parse_lv_no("Lv.3 标题（￥88）") == 3
        assert tc.parse_lv_no("Lv3（￥88）") == 3
        assert tc.strip_lv_prefix("LV1 早鸟 数字版") == "早鸟 数字版"
        assert tc.strip_lv_prefix("Lv.2 · 数字标准版") == "数字标准版"
        ok("价格/档位号/标题前缀解析")
    except AssertionError as exc:
        bad("价格/档位号/标题前缀解析", str(exc))

    with tempfile.TemporaryDirectory(prefix="verify_tiers_") as tmpdir:
        tmp = Path(tmpdir)
        try:
            # 1) 新文件：免费档 + 5 个付费档，标题空时省略标题
            path = tmp / "linglan.csv"
            mt.append_tiers(
                [free(0), tier("17", "早一点来找灵兰", 100), tier("21", "来找灵兰玩呀", 25),
                 tier("38", "吧唧也想贴贴", 3), tier("78", "小可爱都带上", 9),
                 tier("148", "今天也想抱抱", 5)],
                128, 3781.0, path,
            )
            rows = rows_of(path)
            assert rows[0] == [
                "timestamp", "总人数", "总金额", "无偿支持",
                "Lv.1 早一点来找灵兰（￥17）", "Lv.2 来找灵兰玩呀（￥21）",
                "Lv.3 吧唧也想贴贴（￥38）", "Lv.4 小可爱都带上（￥78）",
                "Lv.5 今天也想抱抱（￥148）",
            ], rows[0]
            assert rows[1][3:] == ["0", "100", "25", "3", "9", "5"], rows[1]
            ok("append_tiers 首建 9 列与份数")

            # 2) 标题变化沿用旧列，不新增列
            before_header = rows[0]
            mt.append_tiers(
                [free(1), tier("17", "改过的标题", 101), tier("21", "来找灵兰玩呀", 26),
                 tier("38", "吧唧也想贴贴", 4), tier("78", "小可爱都带上", 10),
                 tier("148", "今天也想抱抱", 6)],
                130, 3900.0, path,
            )
            rows = rows_of(path)
            assert rows[0] == before_header, "标题变化不应改表头"
            assert len(rows) == 3, f"行数 {len(rows)}"
            assert rows[2][4] == "101", rows[2]
            ok("标题变化沿用旧价格列")

            # 3) 同价两个档位：按页面出现顺序绑定两个列
            dup = tmp / "dup.csv"
            mt.append_tiers(
                [tier("38", "A 款", 1), tier("38", "B 款", 2), tier("88", "C 款", 3)],
                6, 202.0, dup,
            )
            rows = rows_of(dup)
            assert rows[0][3:] == ["Lv.1 A 款（￥38）", "Lv.2 B 款（￥38）", "Lv.3 C 款（￥88）"], rows[0]
            mt.append_tiers(
                [tier("38", "A 改名", 4), tier("38", "B 改名", 5), tier("88", "C 改名", 6)],
                15, 500.0, dup,
            )
            rows = rows_of(dup)
            assert rows[0][3:] == ["Lv.1 A 款（￥38）", "Lv.2 B 款（￥38）", "Lv.3 C 款（￥88）"], rows[0]
            assert rows[2][3:] == ["4", "5", "6"], rows[2]
            ok("同价多档按出现顺序绑定")

            # 4) 旧列名 Lv1（￥42）仍按价格绑定，不改表头
            old = tmp / "old.csv"
            old.write_bytes(
                BOM
                + "timestamp,总人数,总金额,无偿支持,Lv1（￥42）,Lv2（￥45.6）\r\n"
                  "2026-10-01 00:00:00,1,100.0,0,1,1\r\n".encode("utf-8")
            )
            mt.append_tiers(
                [free(0), tier("42", "早鸟 数字版", 2), tier("45.6", "数字标准版", 3)],
                6, 300.0, old,
            )
            rows = rows_of(old)
            assert rows[0][3:] == ["无偿支持", "Lv1（￥42）", "Lv2（￥45.6）"], rows[0]
            assert rows[2][3:] == ["0", "2", "3"], rows[2]
            ok("旧列名 Lv1（￥42）兼容且不改表头")

            # 5) 新价格才新增列，并原子重写补空
            mt.append_tiers(
                [free(0), tier("42", "早鸟 数字版", 3), tier("45.6", "数字标准版", 4), tier("88", "新档", 7)],
                14, 900.0, old,
            )
            rows = rows_of(old)
            assert rows[0][-1] == "Lv.3 新档（￥88）", rows[0]
            assert all(len(r) == len(rows[0]) for r in rows), "历史行列数未补齐"
            assert rows[2][-1] == "" and rows[3][-1] == "7", rows
            ok("缺价格时新增列并补空")

            # 6) 空档位拒绝
            try:
                mt.append_tiers([], 0, 0.0, tmp / "empty.csv")
            except ValueError:
                ok("空档位被拒绝")
            else:
                bad("空档位被拒绝", "没有抛 ValueError")
        except Exception as exc:  # noqa: BLE001
            bad("append_tiers 用例", f"{type(exc).__name__}: {exc}")

    print(f"\n{len(PASS)} PASS / {len(FAIL)} FAIL")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
