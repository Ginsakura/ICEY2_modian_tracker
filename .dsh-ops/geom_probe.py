"""打印 analyze 各视图底部区域的精确几何关系（显示坐标，y 自下而上）。

用法: python .dsh-ops/geom_probe.py
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


def bb(t, r):
    return t.get_window_extent(r)


ch = charts.AnalyzeChart()
fig = ch.fig
for view in charts.AnalyzeChart.VIEWS:
    for gi, gran in enumerate(charts.AnalyzeChart.GRAN_SECONDS):
        if view not in charts.AnalyzeChart.GRAN_APPLIES and gran is not None:
            continue
        ch.state["view"] = view
        ch.state["gran"] = gi
        ch.render_view()
        fig.canvas.draw()
        r = fig.canvas.get_renderer()
        plots = [a for a in fig.axes if a not in ch._ctrl_axes and a.axison]
        for i, ax in enumerate(plots):
            bottom = ax.bbox.y0
            ticks = [bb(t, r) for t in ax.get_xticklabels()
                     if t.get_text() and t.get_visible() and t.get_rotation() == 0]
            tl = (min(t.y0 for t in ticks), max(t.y1 for t in ticks)) if ticks else (0, 0)
            lab = ax.xaxis.label
            xlb = bb(lab, r) if lab.get_text() and lab.get_visible() else None
            sw = ch.slider_window.ax.bbox
            if xlb is None:
                print(f"{view}/g{gi}: ax{i} bottom={bottom:.1f} ticks={tl[0]:.1f}..{tl[1]:.1f} (无 xlabel)")
                continue
            print(f"{view}/g{gi}: ax{i} bottom={bottom:.1f} ticks={tl[0]:.1f}..{tl[1]:.1f}"
                  f"  xlabel={xlb.y0:.1f}..{xlb.y1:.1f}  slider={sw.y0:.1f}..{sw.y1:.1f}"
                  f"  xlabel-slider_clear={xlb.y0 - sw.y1:.1f}")
