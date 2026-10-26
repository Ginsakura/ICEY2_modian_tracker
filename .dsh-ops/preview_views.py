"""无头渲染 --analyze 的五个视图为 PNG，供人工/视觉检查图表重叠问题。

用法（项目根目录）:
    .venv/Scripts/python.exe .dsh-ops/preview_views.py [out_dir]
"""
import sys
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.backends.backend_agg  # noqa: F401
from matplotlib.backend_bases import FigureCanvasBase


class _DummyTimer:
    """Agg 后端没有真实定时器，构造 AnalyzeChart 时用的空实现"""

    def add_callback(self, *a, **k):
        return None

    def start(self, *a, **k):
        return None

    def stop(self, *a, **k):
        return None


FigureCanvasBase.new_timer = lambda self, *a, **k: _DummyTimer()

sys.path.insert(0, os.path.abspath("."))

# 采集进程常驻时会独占 tracker_log.log，预览脚本改用控制台日志，避免 FileHandler 抢锁
import logging  # noqa: E402
import shared_logger  # noqa: E402

shared_logger._initialized = True
logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler()])

import charts  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(".preview", "views")
os.makedirs(OUT, exist_ok=True)

ch = charts.AnalyzeChart()
print(f"rows={len(ch.parsed)} tiers={ch.stack_labels()}")
print(f"window={ch.state['window']} snapshot={ch.state['snapshot']}")

shots = []
for view in charts.AnalyzeChart.VIEWS:
    for gran in charts.AnalyzeChart.GRAN_SECONDS:
        if view not in charts.AnalyzeChart.GRAN_APPLIES and gran is not None:
            continue
        ch.state["view"] = view
        ch.state["gran"] = charts.AnalyzeChart.GRAN_SECONDS.index(gran)
        ch.render_view()
        tag = f"{charts.AnalyzeChart.VIEWS.index(view) + 1}_{view}_g{ch.state['gran']}"
        path = os.path.join(OUT, tag + ".png")
        ch.fig.savefig(path, dpi=100)
        shots.append(path)
        print("saved", path)

print("done", len(shots))
