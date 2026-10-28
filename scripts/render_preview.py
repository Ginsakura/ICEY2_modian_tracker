"""无头渲染 AnalyzeChart 的全部视图到 .preview/，供回归检查使用。

只依赖 charts.py 与 matplotlib；不进入采集/同步流程。
用法(Windows venv):
    .venv/Scripts/python.exe scripts/render_preview.py
    .venv/Scripts/python.exe scripts/render_preview.py --window 0,1440 --out .preview/small
"""
import argparse
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import charts  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="渲染九个分析视图为 PNG")
    ap.add_argument("--csv", default="data.csv")
    ap.add_argument("--out", default=".preview")
    ap.add_argument("--window", default="", help="时间窗口索引 lo,hi；缺省为全窗口")
    ap.add_argument("--dpi", type=int, default=90)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    ch = charts.AnalyzeChart(args.csv)
    # 无头模式不需要轮询与重绘节流
    ch._render_timer.stop()
    ch._poll_timer.stop()
    n = max(0, len(ch.parsed) - 1)
    if args.window:
        lo, hi = (int(x) for x in args.window.split(","))
        ch.state["window"] = (max(0, lo), min(n, hi))
        ch._sync_slider_text()

    ok = 0
    for view in charts.AnalyzeChart.VIEWS:
        ch.state["view"] = view
        try:
            ch.render_view()
            path = os.path.join(args.out, f"{view}.png")
            ch.fig.savefig(path, dpi=args.dpi)
            print(f"saved {path}")
            ok += 1
        except Exception as exc:  # noqa: BLE001 预览脚本要一次跑完并列出全部失败
            print(f"FAILED {view}: {type(exc).__name__}: {exc}")
    plt.close(ch.fig)
    print(f"{ok}/{len(charts.AnalyzeChart.VIEWS)} 渲染成功")
    return 0 if ok == len(charts.AnalyzeChart.VIEWS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
