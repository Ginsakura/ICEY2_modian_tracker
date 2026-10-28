"""视图兼容与高密度追加验证（OpenSpec 任务 4.4 / 4.5）。

- 4.4 截断行兼容：在临时副本末尾写入半行（时间戳截断 / 列缺失），确认 load()
  能跳过坏行且九个视图都能渲染。
- 4.5 高密度追加：10 秒间隔 500 行的夹具，连续追加 100 次，确认单次耗时与
  文件大小无关，再与 5000 行夹具对比。

用法(Windows venv):
    .venv/Scripts/python.exe scripts/verify_views.py
    .venv/Scripts/python.exe scripts/verify_views.py --csv data.csv --out /tmp/verify_views
"""
import argparse
import csv
import os
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import charts  # noqa: E402
import modian_tracker as mt  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def _ok(msg: str) -> None:
    PASS.append(msg)
    print(f"PASS {msg}")


def _bad(msg: str) -> None:
    FAIL.append(msg)
    print(f"FAIL {msg}")


def _render_all(csv_path: str, out_dir: str) -> tuple[bool, int]:
    os.makedirs(out_dir, exist_ok=True)
    ch = charts.AnalyzeChart(csv_path)
    ch._render_timer.stop()
    ch._poll_timer.stop()
    try:
        for view in charts.AnalyzeChart.VIEWS:
            ch.state["view"] = view
            ch.render_view()
            ch.fig.savefig(os.path.join(out_dir, f"{view}.png"), dpi=60)
        return True, len(ch.parsed)
    finally:
        plt.close(ch.fig)


def check_truncated(csv_path: str, tmp: str) -> None:
    with open(csv_path, "rb") as f:
        raw = f.read()
    cases = (
        ("时间戳截断", b"2026-10-28 22:0"),
        ("列缺失", b"2026-10-28 22:00:00,1234"),
    )
    for label, tail in cases:
        path = os.path.join(tmp, f"truncated_{label}.csv")
        with open(path, "wb") as f:
            f.write(raw)
            f.write(tail)
        try:
            ok, n = _render_all(path, os.path.join(tmp, "out_" + label))
        except Exception as exc:  # noqa: BLE001 验证脚本要一次跑完并列出全部失败
            _bad(f"4.4 {label}: 渲染抛异常 {type(exc).__name__}: {exc}")
            continue
        if ok and n >= 30000:
            _ok(f"4.4 {label}: 九个视图渲染通过，解析 {n} 行")
        else:
            _bad(f"4.4 {label}: 解析行数异常 {n}")


def _row(ts: datetime, i: int) -> str:
    return f"{ts:%Y-%m-%d %H:%M:%S},{1000 + i},{50000.0 + i * 1.5},{i % 7},1,2,3,4,5,6,7,8,9\r\n"


def _fixture(path: str, header: list[str], rows: int, step: int = 10) -> None:
    t0 = datetime(2026, 10, 1, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(b"\xef\xbb\xbf")
        f.write((",".join(header) + "\r\n").encode("utf-8"))
        for i in range(rows):
            f.write(_row(t0 + timedelta(seconds=step * i), i).encode("utf-8"))


def _append_batch(path: str, data: dict, count: int) -> list[float]:
    times: list[float] = []
    for i in range(count):
        t = time.perf_counter()
        mt.append_to_csv(data, 1000 + i, 50000.0 + i)
        times.append((time.perf_counter() - t) * 1000)
    return times


def check_dense(tmp: str, header: list[str]) -> None:
    data = {c: 1 for c in header[3:]}
    for rows in (500, 5000):
        path = os.path.join(tmp, f"dense_{rows}.csv")
        _fixture(path, header, rows)
        old_file = mt.CSV_FILE
        mt.CSV_FILE = path
        try:
            before = os.path.getsize(path)
            times = _append_batch(path, data, 100)
            after = os.path.getsize(path)
        finally:
            mt.CSV_FILE = old_file
        head = statistics.median(times[:20])
        tail = statistics.median(times[-20:])
        grew = after - before
        if tail <= head * 2.0 + 5:
            _ok(f"4.5 {rows} 行夹具: 前 20 次 {head:.2f}ms / 后 20 次 {tail:.2f}ms，文件增长 {grew} 字节")
        else:
            _bad(f"4.5 {rows} 行夹具: 追加耗时随规模上升 {head:.2f}ms -> {tail:.2f}ms")
        with open(path, "rb") as f:
            content = f.read()
        if content.count(b"\xef\xbb\xbf") == 1 and content.endswith(b"\r\n"):
            _ok(f"4.5 {rows} 行夹具: BOM 唯一、末尾 CRLF")
        else:
            _bad(f"4.5 {rows} 行夹具: BOM/CRLF 异常")


def main() -> int:
    ap = argparse.ArgumentParser(description="验证截断行兼容与高密度追加")
    ap.add_argument("--csv", default="data.csv")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    with open(args.csv, "r", encoding="utf-8-sig", newline="") as f:
        header = next(csv.reader(f))
    tmp = args.out or tempfile.mkdtemp(prefix="verify_views_")
    os.makedirs(tmp, exist_ok=True)
    print(f"临时目录: {tmp}")
    print(f"表头: {','.join(header)}")

    check_truncated(args.csv, tmp)
    check_dense(tmp, header)
    print(f"\n{len(PASS)} PASS / {len(FAIL)} FAIL")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
