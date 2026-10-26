"""审计 --analyze 各视图中真正会被绘制的 Text 元素之间的几何重叠（无头渲染 + 精确 bbox 相交）。

只统计"眼睛能看到"的文本：绘图轴的刻度/轴标签/标题/注释/图例 + 控件面板的标题、
面板内文本(勾选/单选标签)与滑块标签/数值文本；控件轴内部隐藏的刻度一律排除。

用法（项目根目录）:
    .venv/Scripts/python.exe .dsh-ops/audit_overlap.py
"""
import os
import sys
import itertools

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
import logging
import shared_logger

shared_logger._initialized = True
logging.basicConfig(level=logging.WARNING, handlers=[logging.StreamHandler()])

import charts  # noqa: E402

MIN_AREA = 3.0          # 小于该像素面积的相交忽略（字形外框常规轻微接触）


def _add(items, t, owner, renderer):
    if t is None or not t.get_visible():
        return
    s = t.get_text()
    if not s or not s.strip():
        return
    bb = t.get_window_extent(renderer)
    if bb.width <= 0 or bb.height <= 0:
        return
    items.append((s.replace("\n", "\\n")[:36], owner, bb))


def collect(ch, renderer):
    fig = ch.fig
    items = []
    for i, ax in enumerate(fig.axes):
        owner = f"ax{i}"
        if ax in ch._ctrl_axes:
            _add(items, ax.title, owner, renderer)
            for t in ax.texts:
                _add(items, t, owner, renderer)
            continue
        if not ax.axison:
            continue
        xlo, xhi = ax.get_xlim()
        ylo, yhi = ax.get_ylim()
        xaxis_on = ax.xaxis.get_visible()
        yaxis_on = ax.yaxis.get_visible()
        for t in ax.xaxis.get_ticklabels(which="major") if xaxis_on else []:
            if xhi >= xlo and not (xlo <= t.get_position()[0] <= xhi):
                continue
            _add(items, t, owner, renderer)
        for t in ax.yaxis.get_ticklabels(which="major") if yaxis_on else []:
            if yhi >= ylo and not (ylo <= t.get_position()[1] <= yhi):
                continue
            _add(items, t, owner, renderer)
        for t in (ax.xaxis.label if xaxis_on else None,
                  ax.yaxis.label if yaxis_on else None, ax.title):
            _add(items, t, owner, renderer)
        for t in ax.texts:
            _add(items, t, owner, renderer)
        leg = ax.get_legend()
        if leg is not None:
            for t in leg.get_texts():
                _add(items, t, owner + "-legend", renderer)
    return items


def audit(tag, ch):
    fig = ch.fig
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    W, H = fig.canvas.get_width_height()
    items = collect(ch, renderer)
    problems = []
    for (s1, o1, b1), (s2, o2, b2) in itertools.combinations(items, 2):
        dx = min(b1.x1, b2.x1) - max(b1.x0, b2.x0)
        dy = min(b1.y1, b2.y1) - max(b1.y0, b2.y0)
        if dx > 0 and dy > 0 and dx * dy >= MIN_AREA:
            problems.append((round(dx * dy, 1), s1, o1, s2, o2,
                             f"({b1.x0:.0f},{b1.y0:.0f},{b1.x1:.0f},{b1.y1:.0f})",
                             f"({b2.x0:.0f},{b2.y0:.0f},{b2.x1:.0f},{b2.y1:.0f})"))
    # 文本压在滑块轨道上（滑块自身标签/数值在轨道两侧，不算；不可见轴/副轴的刻度不算）
    own = set()
    for w in (ch.slider_window, ch.slider_snap):
        own.update({id(w.label), id(w.valtext)})
    cands = []
    for ax in ch.fig.axes:
        if ax.axison:
            if ax.xaxis.get_visible():
                cands.extend(ax.get_xticklabels(which="major"))
                cands.append(ax.xaxis.label)
            if ax.yaxis.get_visible():
                cands.extend(ax.get_yticklabels(which="major"))
                cands.append(ax.yaxis.label)
            cands.append(ax.title)
            cands.extend(ax.texts)
            leg = ax.get_legend()
            if leg is not None:
                cands.extend(leg.get_texts())
    for name, w in (("时间窗口滑块", ch.slider_window), ("快照滑块", ch.slider_snap)):
        if not w.ax.get_visible():
            continue
        track = w.ax.bbox
        for t in cands:
            if id(t) in own or not t.get_visible() or not t.get_text().strip():
                continue
            b = t.get_window_extent(renderer)
            dx = min(b.x1, track.x1) - max(b.x0, track.x0)
            dy = min(b.y1, track.y1) - max(b.y0, track.y0)
            if dx > 0 and dy > 0 and dx * dy >= MIN_AREA:
                problems.append((round(dx * dy, 1), t.get_text()[:36], "text",
                                 f"压住{name}", "track",
                                 f"({b.x0:.0f},{b.y0:.0f},{b.x1:.0f},{b.y1:.0f})",
                                 f"({track.x0:.0f},{track.y0:.0f},{track.x1:.0f},{track.y1:.0f})"))
    # 绘图区的文本钻到左侧控件面板底下（面板是不透明白底/灰底，被盖住的标签等于看不见）
    panels = [("视图面板", ch._ax_view.bbox), ("聚合粒度面板", ch._ax_gran.bbox),
              ("重置按钮", ch._ax_reset.bbox), ("档位显隐面板", ch._ax_tier.bbox)]
    plot_texts = []
    for ax in ch.fig.axes:
        if ax in ch._ctrl_axes or not ax.axison or not ax.get_visible():
            continue
        if ax.xaxis.get_visible():
            plot_texts.extend(ax.get_xticklabels(which="major"))
            plot_texts.append(ax.xaxis.label)
        if ax.yaxis.get_visible():
            plot_texts.extend(ax.get_yticklabels(which="major"))
            plot_texts.append(ax.yaxis.label)
        plot_texts.append(ax.title)
        plot_texts.extend(ax.texts)
        leg = ax.get_legend()
        if leg is not None:
            plot_texts.extend(leg.get_texts())
    for pname, pb in panels:
        if not pb.width or not pb.height:
            continue
        for t in plot_texts:
            if not t.get_visible() or not t.get_text().strip():
                continue
            b = t.get_window_extent(renderer)
            dx = min(b.x1, pb.x1) - max(b.x0, pb.x0)
            dy = min(b.y1, pb.y1) - max(b.y0, pb.y0)
            if dx > 0 and dy > 0 and dx * dy >= MIN_AREA:
                problems.append((round(dx * dy, 1), t.get_text()[:36], "text",
                                 f"被{pname}遮住", "panel",
                                 f"({b.x0:.0f},{b.y0:.0f},{b.x1:.0f},{b.y1:.0f})",
                                 f"({pb.x0:.0f},{pb.y0:.0f},{pb.x1:.0f},{pb.y1:.0f})"))
    out = []
    for s, o, b in items:
        if b.x0 < -0.5 or b.y0 < -0.5 or b.x1 > W + 0.5 or b.y1 > H + 0.5:
            out.append((s, o, f"({b.x0:.0f},{b.y0:.0f},{b.x1:.0f},{b.y1:.0f})"))
    print(f"\n=== {tag}: texts={len(items)} overlaps={len(problems)} out_of_frame={len(out)}")
    for p in sorted(problems, reverse=True):
        print(f"  area={p[0]:>7}  [{p[2]}] {p[1]!r} {p[5]}  <->  [{p[4]}] {p[3]!r} {p[6]}")
    for s, o, bb in out:
        print(f"  OUT-OF-FRAME [{o}] {s!r} {bb}")
    return problems, out


ch = charts.AnalyzeChart()
total = 0
for size in [(15.0, 9.0), (12.0, 7.2)]:
    ch.fig.set_size_inches(*size)
    print(f"\n########## figure size {size[0]}x{size[1]} in ##########")
    for view in charts.AnalyzeChart.VIEWS:
        for gi, gran in enumerate(charts.AnalyzeChart.GRAN_SECONDS):
            if view not in charts.AnalyzeChart.GRAN_APPLIES and gran is not None:
                continue
            ch.state["view"] = view
            ch.state["gran"] = gi
            ch.render_view()
            probs, out = audit(f"{view} / gran={charts.AnalyzeChart.GRAN_LABELS[gi]}", ch)
            total += len(probs)
print(f"\nTOTAL overlaps={total}")
