"""量出各视图左侧 y 刻度标签/轴标题的实际占位，用于推算左侧留白(PLOT_L)。

用法: python .dsh-ops/left_probe.py
"""
import os
import sys
import logging

import matplotlib

matplotlib.use("Agg")
from matplotlib.backend_bases import FigureCanvasBase


class _DummyTimer:
    def add_callback(self, *a, **k):
        return None

    def start(self, *a, **k):
        return None

    def stop(self, *a, **k):
        return None


FigureCanvasBase.new_timer = lambda self, *a, **k: _DummyTimer()

sys.path.insert(0, os.path.abspath("."))
import shared_logger

shared_logger._initialized = True
logging.basicConfig(level=logging.WARNING, handlers=[logging.StreamHandler()])

import charts  # noqa: E402

ch = charts.AnalyzeChart()
fig = ch.fig
for size in [(15.0, 9.0), (12.0, 7.2)]:
    fig.set_size_inches(*size)
    print(f"\n########## {size[0]}x{size[1]} in")
    for view in charts.AnalyzeChart.VIEWS:
        for gi, gran in enumerate(charts.AnalyzeChart.GRAN_SECONDS):
            if view not in charts.AnalyzeChart.GRAN_APPLIES and gran is not None:
                continue
            ch.state["view"] = view
            ch.state["gran"] = gi
            ch.render_view()
            fig.canvas.draw()
            r = fig.canvas.get_renderer()
            ctrl = [a.bbox for a in ch._ctrl_axes if a.get_visible()]
            panel_right = max(b.x1 for b in ctrl)
            axes_left = min(a.bbox.x0 for a in fig.axes
                            if a not in ch._ctrl_axes and a.axison and a.get_visible())
            left = []
            for ax in fig.axes:
                if ax in ch._ctrl_axes or not ax.axison or not ax.get_visible():
                    continue
                if ax.yaxis.get_visible():
                    for t in ax.get_yticklabels(which="major"):
                        if t.get_text().strip() and t.get_visible():
                            left.append((t.get_window_extent(r).x0, t.get_text()))
                    lab = ax.yaxis.label
                    if lab.get_text().strip() and lab.get_visible():
                        left.append((lab.get_window_extent(r).x0, "ylabel:" + lab.get_text()))
            if not left:
                continue
            x0, who = min(left)
            print(f"  {view}/g{gi}: panel_right={panel_right:.2f} axes_left={axes_left:.2f}"
                  f" leftmost={x0:.2f} ({who}) 需要 PLOT_L>={(panel_right + 3 + axes_left - x0 + 6) / (size[0] * 100):.4f}"
                  f"  当前={charts.AnalyzeChart.PLOT_L}")
