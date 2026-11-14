"""配置驱动追加写入的回归验证（仅标准库，只操作临时目录）。

覆盖：首建 BOM/CRLF、常规追加、价格变化新增列、半行修复、LF 归一化、
原子替换失败时保留原文件。用法:
    .venv/Scripts/python.exe scripts/verify_append.py
"""
import csv
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import modian_tracker as mt
from tier_columns import Tier

BOM = b"\xef\xbb\xbf"
RESULTS: list[tuple[str, bool, str]] = []


def rows_of(path: Path) -> list[list[str]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.reader(f) if r]


def tiers(*pairs: tuple[str, int], free: int = 0) -> list[Tier]:
    result = [Tier(None, None, "无偿支持", free, True)] if free else []
    result.extend(Tier(price, Decimal(price), "", count, False) for price, count in pairs)
    return result


def case(name):
    def deco(fn):
        try:
            fn()
        except AssertionError as exc:
            RESULTS.append((name, False, str(exc)))
        except Exception as exc:  # noqa: BLE001
            RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))
        else:
            RESULTS.append((name, True, ""))
        return fn
    return deco


def main() -> int:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        path = tmp / "data" / "icey2.csv"

        @case("首建文件：BOM + 表头 + 首行")
        def _first():
            mt.append_tiers(tiers(("42", 5), ("45.6", 3)), 41, 346.8, path)
            raw = path.read_bytes()
            assert raw.startswith(BOM), "缺少 BOM"
            assert raw.endswith(b"\r\n"), "末行不是 CRLF"
            assert raw.count(BOM) == 1, "BOM 出现多次"
            rows = rows_of(path)
            assert rows[0] == ["timestamp", "总人数", "总金额", "Lv.1（￥42）", "Lv.2（￥45.6）"], rows[0]
            assert rows[1][1:] == ["41", "346.8", "5", "3"], rows[1]

        @case("常规追加：历史字节不变，行数 +1")
        def _append():
            before = path.read_bytes()
            mt.append_tiers(tiers(("42", 6), ("45.6", 4)), 51, 400.0, path)
            after = path.read_bytes()
            assert after.startswith(before), "历史字节被改写"
            assert after.endswith(b"\r\n"), "末行不是 CRLF"
            assert len(rows_of(path)) == 3, "行数不是 3"

        @case("价格变化：新列追加到末尾，历史行补空")
        def _evolve():
            mt.append_tiers(tiers(("42", 7), ("45.6", 5), ("888", 1)), 61, 500.0, path)
            rows = rows_of(path)
            assert rows[0][-1] == "Lv.3（￥888）", rows[0]
            assert all(len(r) == len(rows[0]) for r in rows), "存在列数不一致的历史行"
            assert rows[1][-1] == "" and rows[2][-1] == "", "历史行未补空位"
            assert rows[3][-1] == "1", rows[3]

        @case("半行修复：截断到最后一个换行后追加")
        def _partial():
            good = path.read_bytes()
            n_before = len(rows_of(path))
            with open(path, "ab") as f:
                f.write(b"2026-10-28 20:00:00,999")
            mt.append_tiers(tiers(("42", 9), ("45.6", 7), ("888", 3)), 81, 700.0, path)
            raw = path.read_bytes()
            assert raw.startswith(good), "截断时伤到了完整行"
            assert b"2026-10-28 20:00:00,999" not in raw, "半行未被截断"
            rows = rows_of(path)
            assert len(rows) == n_before + 1, f"行数 {len(rows)} != {n_before + 1}"
            assert rows[-1][1] == "81", rows[-1]

        @case("行尾被外部改成 LF：归一化为 CRLF")
        def _lf():
            text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
            path.write_text(text, encoding="utf-8", newline="\n")
            n_before = len(rows_of(path))
            mt.append_tiers(tiers(("42", 10), ("45.6", 8), ("888", 4)), 91, 800.0, path)
            raw = path.read_bytes()
            assert raw.startswith(BOM), "归一化后缺 BOM"
            stripped = raw.replace(b"\r\n", b"")
            bad = stripped.find(b"\n")
            assert bad == -1, f"仍存在非 CRLF 行尾(孤立换行偏移 {bad})"
            assert len(rows_of(path)) == n_before + 1, "归一化后行数不对"

        @case("重写失败：原文件不被破坏，临时文件被清理")
        def _atomic():
            before = path.read_bytes()
            original = mt.os.replace

            def boom(src, dst):
                raise OSError("模拟替换失败")

            mt.os.replace = boom
            try:
                try:
                    mt.append_tiers(tiers(("42", 11), ("45.6", 9), ("888", 5), ("1000", 1)), 101, 900.0, path)
                except OSError:
                    pass
                else:
                    raise AssertionError("重写失败未抛出异常")
            finally:
                mt.os.replace = original
            assert path.read_bytes() == before, "重写失败后原文件被改动"
            assert not Path(f"{path}.tmp").exists(), "临时文件未清理"

    failed = [r for r in RESULTS if not r[1]]
    for name, ok, reason in RESULTS:
        print(("PASS  " if ok else "FAIL  ") + name + (f"  -> {reason}" if reason else ""))
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
