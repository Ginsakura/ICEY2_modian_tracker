"""摩点众筹数据可视化: 基础趋势图(--plot)与交互式分析窗口(--analyze)。

本模块是项目内唯一的绘图实现，采集端(modian_tracker.py)不导入本模块，
以保证采集功能与 matplotlib 解耦。

类关系(按可复用性拆分): 

    BaseChart        数据加载 + 基础趋势渲染 + 十字跟踪 tooltip，供两种用法复用
      ├─ PlotChart       --plot: 渲染后直接显示，输出与重构前逐像素一致
      └─ AnalyzeChart    --analyze: 五个视图 + matplotlib 原生控件 + 数据实时更新
"""
import csv
import logging
import math
import os
import re
import time
from datetime import datetime
from types import FunctionType, MethodType

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.text import Text
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Wedge
from matplotlib.widgets import Button, CheckButtons, RadioButtons, RangeSlider, Slider
from numpy.typing import NDArray
from typing import TYPE_CHECKING, Any, Literal, Optional, TypedDict, cast

if TYPE_CHECKING:
    # figure.pyi 只把 Figure.canvas 标注为 FigureCanvasBase，而 blitting 用的
    # copy_from_bbox/restore_region 实际定义在 FigureCanvasAgg 上，此导入仅供静态检查
    from matplotlib.backends.backend_agg import FigureCanvasAgg

import shared_logger
logger = shared_logger.get_logger("charts")

CSV_FILE: str = "data.csv"

# 总金额里程碑 L 形标注: 数据范围内每个整百万倍(27M、28M…，虚线)/整千万倍(30M、40M…，实线)，
# 判定"刚超过": 上一点 < 阈值 ≤ 当前点(相邻点比较); 千万倍数优先，同一点不重复标记。
# 固定颜色与红色总金额折线及堆积区域色(蓝/橙/绿/红/紫/棕/粉/灰/黄绿/青)均保持可区分
MILLION_STEP: int = 1_000_000        # 百万级里程碑间距(虚线)
TENMILLION_STEP: int = 10_000_000    # 千万级里程碑间距(实线)


class MilestoneStyle(TypedDict):
    """里程碑标注线条样式(竖线/横线共用): 键名限定，避免 **style 展开被当作 plot 的其它关键字"""
    linestyle: str
    color: str


MILESTONE_STYLE: dict[str, MilestoneStyle] = {
    "million": {"linestyle": "--", "color": "#4B0082"},      # 深靛紫虚线
    "tenmillion": {"linestyle": "-", "color": "#000000"},    # 黑色实线
}

# --plot 时间轴左边界(十周年《艾希》续作众筹起始时刻)，右边界为末点 + 10 分钟
PLOT_X_LEFT: datetime = datetime(2026, 10, 5, 22, 0)
X_RIGHT_PAD: float = 10 / 1440        # 10 分钟，matplotlib 天数单位


def _fmt_money(v: float, _=None) -> str:
    """金额类刻度: 千分位整数"""
    return f"{v:,.0f}"


class CursorTracker(Figure):
    """十字跟踪虚线 + 数据行提示框(纯 matplotlib 事件 + blitting 双背景优化)。

    生命周期与 Figure 绑定: 事件只连接一次，切换视图时用 bind() 重设目标轴与数据。
    blitting 策略: 静态重元素只在重建时渲染一次(bg_clean)，tooltip 的多行 CJK 文本
    渲染开销大(实测 ≈27ms)，故仅在跨数据点或对齐状态变化时重建 bg_tt(含 100ms 节流); 
    每次鼠标移动只恢复背景并重绘十字线(实测 ≈0.4ms)。
    """

    TT_THROTTLE: float = 0.1   # tooltip 重建节流间隔(秒)

    def __init__(self, fig: Figure):
        self.fig: Figure = fig
        self.panels: list[Axes] = []            # 需要绘制十字线的面板(按绘制层级由外到内)
        self.anchor_ax: Optional[Axes] = None       # 提示框锚点所在轴
        self.x_arr: Optional[NDArray[np.float64]] = None
        self.anchor_y: Optional[NDArray[np.float64]] = None
        self.provider: Optional[FunctionType|MethodType] = None        # idx -> [文本行]
        self.vlines: list[Line2D] = []
        self.hlines: list[Line2D] = []
        self.tooltip: Optional[Text] = None
        self._bg_clean: Optional[Figure] = None
        self._bg_tt: Optional[Figure] = None
        self._last_key: Optional[tuple[int, Any, Literal['top', 'bottom', 'center']]] = None
        self._last_time: float = 0.0
        fig.canvas.mpl_connect("motion_notify_event", self._on_move)
        for sig in ("button_release_event", "scroll_event", "resize_event"):
            fig.canvas.mpl_connect(sig, self.mark_dirty)

    def _agg_canvas(self) -> "FigureCanvasAgg":
        """blit 接口(copy_from_bbox / restore_region)只在 FigureCanvasAgg 上声明，
        而 Figure.canvas 的静态类型为 FigureCanvasBase，此处仅做类型收窄，运行期无行为。"""
        return cast("FigureCanvasAgg", self.fig.canvas)

    def mark_dirty(self, event=None) -> None:
        """缩放/平移结束或窗口 resize 后背景失效，下次移动时重建"""
        self._bg_clean = None
        self._bg_tt = None

    def bind(self, panels: list[Axes], anchor_ax: Optional[Axes], x_arr: NDArray[np.float64], anchor_y: NDArray[np.float64], provider: Optional[FunctionType|MethodType]) -> None:
        """为当前视图(重新)创建十字线与提示框; panels 为空时禁用跟踪"""
        self.panels = list(panels)
        self.anchor_ax = anchor_ax
        self.x_arr = np.asarray(x_arr)
        self.anchor_y = np.asarray(anchor_y, dtype=float)
        self.provider = provider
        self._bg_clean = None
        self._bg_tt = None
        self._last_key = None
        self.vlines = []
        self.hlines = []
        self.tooltip = None
        for ax in self.panels:
            # zorder=4: 高于面板数据线(默认 2)、低于面板 legend(默认 5)
            vline: list[Line2D] = ax.plot([], [], color="gray", linestyle="--", linewidth=1, zorder=4)
            hline: list[Line2D] = ax.plot([], [], color="gray", linestyle="--", linewidth=1, zorder=4)
            vline[0].set_visible(False)
            hline[0].set_visible(False)
            self.vlines.append(vline[0])
            self.hlines.append(hline[0])
        if self.panels and (self.anchor_ax is not None):
            self.tooltip = self.anchor_ax.text(
                0, 0, "", fontsize=9, zorder=4, va="center", ha="left",
                bbox={"boxstyle": "round", "facecolor": "white", "edgecolor": "gray", "alpha": 0.9},
            )
            self.tooltip.set_visible(False)

    def _hide(self) -> None:
        """移出绘图区: 隐藏三件套并恢复干净背景"""
        if not self.vlines:
            return
        visible = any(a.get_visible() for a in self.vlines) or any(a.get_visible() for a in self.hlines)
        if not visible:
            return
        for art in self.vlines + self.hlines + [self.tooltip]:
            if art is not None:
                art.set_visible(False)
        if self._bg_clean is not None:
            self._agg_canvas().restore_region(self._bg_clean)
            self.fig.canvas.blit(self.fig.bbox)
        else:
            self.fig.canvas.draw_idle()

    def _on_move(self, event) -> None:
        """鼠标移动: 更新十字虚线(每帧)与 tooltip(跨数据点时重建背景)"""
        if not self.vlines:
            return
        if self.anchor_ax is None:
            return
        if self.x_arr is None:
            return
        if self.anchor_y is None:
            return
        if self.tooltip is None:
            return

        if event.inaxes is None or event.xdata is None or event.inaxes not in self.panels:
            self._hide()
            return
        hover_ax = event.inaxes
        x, y = event.xdata, event.ydata

        # 最近数据行 → tooltip 内容与锚点(吸附数据点，位置由 (idx, ha, va) 唯一决定)
        i = int(np.abs(self.x_arr - x).argmin())
        ax_anchor = self.anchor_ax
        x0, x1_ = ax_anchor.get_xlim()
        y0, y1_ = ax_anchor.get_ylim()
        dx = 0.02 * (x1_ - x0)
        on_left = self.x_arr[i] < x0 + 0.65 * (x1_ - x0)
        ty = self.anchor_y[i]
        frac = (ty - y0) / (y1_ - y0) if y1_ > y0 else 0.5
        va = "top" if frac > 0.75 else "bottom" if frac < 0.25 else "center"
        key = (i, on_left, va)

        if self._bg_clean is None or self._bg_tt is None:
            # 背景失效(首次/缩放/resize): 设置 tooltip 并整体重建
            self._apply_tooltip(i, on_left, va, dx)
            self.tooltip.set_visible(True)
            self._rebuild_bgs()
            self._last_key, self._last_time = key, time.monotonic()
        elif key != self._last_key and time.monotonic() - self._last_time >= self.TT_THROTTLE:
            # 跨数据点或翻转状态变化: 更新 tooltip 并重印入背景(节流内跳过，下帧再试)
            self._apply_tooltip(i, on_left, va, dx)
            self._agg_canvas().restore_region(self._bg_clean)
            self.tooltip.set_visible(True)
            self.anchor_ax.draw_artist(self.tooltip)
            self._bg_tt = self._agg_canvas().copy_from_bbox(self.fig.bbox)
            self._last_key, self._last_time = key, time.monotonic()

        # 每帧快速路径: 恢复含 tooltip 的背景 + 仅重绘十字线
        # 竖线贯穿所有面板，横线只画在鼠标所在面板(多面板视图下的自然读数方式)
        for ax, vline in zip(self.panels, self.vlines):
            vline.set_data([x, x], ax.get_ylim())
            vline.set_visible(True)
        for ax, hline in zip(self.panels, self.hlines):
            if ax is hover_ax:
                hx0, hx1 = ax.get_xlim()
                hline.set_data((hx0, hx1), [y, y])
                hline.set_visible(True)
            else:
                hline.set_visible(False)
        self._agg_canvas().restore_region(self._bg_tt)
        for ax, vline in zip(self.panels, self.vlines):
            ax.draw_artist(vline)
        for ax, hline in zip(self.panels, self.hlines):
            if hline.get_visible():
                ax.draw_artist(hline)
        self.fig.canvas.blit(self.fig.bbox)

    def _apply_tooltip(self, i: int, on_left: bool, va: Literal["top", "bottom", "center"], dx: float) -> None:
        """把最近数据行的内容写入 tooltip 并按锚点/对齐状态定位"""
        if self.provider is None:
            return
        if self.x_arr is None:
            return
        if self.anchor_y is None:
            return
        if self.tooltip is None:
            return
        
        self.tooltip.set_text("\n".join(self.provider(i)))
        x = self.x_arr[i]
        self.tooltip.set_position((x + dx if on_left else x - dx, float(self.anchor_y[i])))
        self.tooltip.set_horizontalalignment("left" if on_left else "right")
        self.tooltip.set_verticalalignment(va)

    def _rebuild_bgs(self) -> None:
        """全量渲染一次静态图，生成干净背景与含 tooltip 的背景快照"""
        if self.tooltip is None:
            return
        if self.anchor_ax is None:
            return
        
        vis = [(a, a.get_visible()) for a in self.vlines + self.hlines + [self.tooltip]]
        for a, _ in vis:
            a.set_visible(False)
        self.fig.canvas.draw()                      # 全量重绘(无三件套)，一次性成本
        self._bg_clean = self._agg_canvas().copy_from_bbox(self.fig.bbox)
        if self.tooltip.get_text():
            self.tooltip.set_visible(True)
            self.anchor_ax.draw_artist(self.tooltip)
        self._bg_tt = self._agg_canvas().copy_from_bbox(self.fig.bbox)
        for a, v in vis:
            a.set_visible(v)


class BaseChart:
    """CSV 数据加载与基础趋势渲染的公共基类。

    子类复用其数据层(load/col_values/聚合/里程碑)与绘图层(draw_trend/
    attach_dual_status/row_tooltip)，并共享 CursorTracker 的交互行为。
    """

    def __init__(self, csv_file=CSV_FILE, logger=None):
        self.csv_file = csv_file
        self.logger = logger
        self.header = []
        self.parsed = []            # [(datetime, row)]，按时序升序
        self.mtime = None
        self._index = {}

    # ---------------- 数据层 ----------------

    def load(self, quiet=False) -> bool:
        """读取 CSV、解析时间戳并按时序排序(解析失败的行跳过); 返回是否成功"""
        if not os.path.exists(self.csv_file):
            if not quiet:
                print(f"未找到数据文件: {self.csv_file}，请先执行记录。")
            return False
        with open(self.csv_file, "r", encoding="utf-8-sig", newline="") as f:
            rows = [r for r in csv.reader(f) if r]
        if len(rows) < 2 or "timestamp" not in rows[0]:
            if not quiet:
                print("CSV 文件为空或未包含有效时间戳列。")
            return False
        parsed = []
        for r in rows[1:]:
            try:
                ts = datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S")
            except (ValueError, IndexError):
                continue
            parsed.append((ts, r))
        parsed.sort(key=lambda item: item[0])
        self.header = rows[0]
        self.parsed = parsed
        self._index = {name: i for i, name in enumerate(self.header)}
        try:
            self.mtime = os.path.getmtime(self.csv_file)
        except OSError:
            self.mtime = None
        return True

    def cell(self, row, name: str) -> float:
        """取单元格数值; 列缺失、越界或空值返回 NaN"""
        i = self._index.get(name)
        if i is None or i >= len(row):
            return float("nan")
        try:
            return float(row[i])
        except ValueError:
            return float("nan")

    def col_values(self, name: str) -> list[float]:
        """整列数值(空值 -> NaN)"""
        i = self._index.get(name)
        if i is None:
            return [float("nan")] * len(self.parsed)
        vals = []
        for _, r in self.parsed:
            try:
                vals.append(float(r[i]) if r[i] else float("nan"))
            except (ValueError, IndexError):
                vals.append(float("nan"))
        return vals

    def x_dates(self) -> NDArray[np.float64]:
        """时间戳转 matplotlib 日期数值"""
        return mdates.date2num([ts for ts, _ in self.parsed])

    @staticmethod
    def _lv_no(col: str) -> int:
        m = re.search(r"Lv(\d+)", col)
        return int(m.group(1)) if m else -1

    def lv_cols(self) -> list[str]:
        return sorted([c for c in self.header if c.startswith("Lv")], key=self._lv_no)

    def stack_labels(self) -> list[str]:
        """堆积顺序: 自下而上 Lv1~Lv9、无偿支持"""
        return self.lv_cols() + (["无偿支持"] if "无偿支持" in self.header else [])

    @staticmethod
    def cycle_color(k: int) -> str:
        """matplotlib 默认配色循环中第 k 个颜色(与 stackplot 自动配色顺序一致)"""
        cycle = plt.rcParams["axes.prop_cycle"].by_key()["color"]
        return cycle[k % len(cycle)]

    def tier_color(self, label: str) -> str:
        """档位列在其堆积顺序中的配色"""
        labels = self.stack_labels()
        return self.cycle_color(labels.index(label) if label in labels else 0)

    def tier_prices(self) -> list[tuple[str, float]]:
        """付费档位(列名, 价格)按价格升序"""
        out = []
        for col in self.lv_cols():
            m = re.search(r"[¥￥]\s*([\d.]+)", col)
            if m:
                out.append((col, float(m.group(1))))
        return sorted(out, key=lambda x: x[1])

    @staticmethod
    def visual_width(s: str) -> int:
        """估算渲染宽度: 全角字符按 2 个半角字符计，用于空格补齐对齐"""
        return sum(2 if ord(ch) > 0x2E80 else 1 for ch in s)

    def legend_names(self, last_row: list[str]) -> dict[str, str]:
        """图例标签: 列名 + 最新值，空格补齐使 '--' 竖排对齐(\\t 在图例中无法对齐)"""
        values = {}
        for c in self.stack_labels() + ["总人数", "总金额"]:
            i = self._index.get(c)
            if i is None or not last_row:
                continue
            values[c] = last_row[i] if i < len(last_row) else ""
        max_left = max((self.visual_width(c) for c, v in values.items() if v), default=0)
        return {
            c: (c + " " * (max_left - self.visual_width(c)) + f"--{v}" if v else c)
            for c, v in values.items()
        }

    def row_tooltip(self, i: int) -> list[str]:
        """数据行提示框内容: 时间 + 该行全部列(与重构前一致)"""
        ts, row = self.parsed[i]
        parts: list[str] = [ts.strftime("%Y-%m-%d %H:%M:%S")]
        for name, val in zip(self.header, row):
            if name == "timestamp":
                continue
            parts.append(f"{name}: {val if val else '--'}")
        return parts

    def share_matrix(self, labels: list[str]) -> tuple[list[datetime], list[list[float]], list[float]]:
        """各档位占比序列(分母为该行档位人数之和，页面总数与档位和并不一致)。

        返回 (时间列表, [[各标签百分比序列]], [该行档位数总和])
        """
        xs = []
        series = [[] for _ in labels]
        totals = []
        for ts, row in self.parsed:
            vals = [self.cell(row, c) for c in labels]
            if any(math.isnan(v) for v in vals):
                continue
            total = sum(vals)
            if total <= 0:
                continue
            xs.append(ts)
            totals.append(total)
            for k, v in enumerate(vals):
                series[k].append(v / total * 100)
        return xs, series, totals

    def aggregate(self, seconds: int | None = None) -> list[tuple[datetime, float, float]]:
        """按时间桶聚合(取桶内最后一行，累计值语义); seconds 为 None 时用原始采样点"""
        points = []
        for ts, row in self.parsed:
            p = self.cell(row, "总人数")
            m = self.cell(row, "总金额")
            if math.isnan(p) and math.isnan(m):
                continue
            points.append((ts, p, m))
        if not seconds:
            return points
        buckets = {}
        for ts, p, m in points:
            buckets[int(ts.timestamp()) // seconds] = (ts, p, m)
        return [buckets[k] for k in sorted(buckets)]

    def rate_series(self, seconds: int | None = None) -> tuple[list[datetime], list[float], list[float]]:
        """增速序列: 返回 (时间, 人数增速(人/时), 金额增速(元/时))"""
        pts = self.aggregate(seconds)
        xs, rp, rm = [], [], []
        for i in range(1, len(pts)):
            prev, cur = pts[i - 1], pts[i]
            dt_h = (cur[0] - prev[0]).total_seconds() / 3600
            if dt_h <= 0 or math.isnan(prev[1]) or math.isnan(cur[1]):
                continue
            xs.append(cur[0])
            rp.append((cur[1] - prev[1]) / dt_h)
            rm.append((cur[2] - prev[2]) / dt_h)
        return xs, rp, rm

    def arpu_series(self, seconds: int | None = None) -> tuple[list[datetime], list[float], list[float]]:
        """客单价序列: 返回 (时间, 累计客单价(元/人), 边际客单价(元/人))"""
        pts = self.aggregate(seconds)
        xs, cum, marg = [], [], []
        for i, (ts, p, m) in enumerate(pts):
            if math.isnan(p) or math.isnan(m) or p <= 0:
                continue
            xs.append(ts)
            cum.append(m / p)
            prev_p = pts[i - 1][1] if i else float("nan")
            prev_m = pts[i - 1][2] if i else float("nan")
            d_p = p - prev_p
            marg.append((m - prev_m) / d_p if d_p > 0 else float("nan"))
        return xs, cum, marg

    def milestones(self) -> list[dict]:
        """每个整百万倍里程碑: 返回值含跨越时刻、相对上一里程碑的耗时(小时)。

        首个里程碑自首个有效金额点起算，区间不完整(partial=True)。
        """
        money = self.col_values("总金额")
        valid = [i for i, v in enumerate(money) if not math.isnan(v)]
        if not valid:
            return []
        max_v = max(money[i] for i in valid)
        marks = []
        prev_ts = self.parsed[valid[0]][0]
        partial = True
        for k in range(1, int(max_v // MILLION_STEP) + 1):
            threshold = k * MILLION_STEP
            idx = next(
                (i for i in range(1, len(money))
                 if not math.isnan(money[i]) and not math.isnan(money[i - 1])
                 and money[i - 1] < threshold <= money[i]),
                None,
            )
            if idx is None:
                continue
            ts = self.parsed[idx][0]
            marks.append({
                "value": float(threshold),
                "ts": ts,
                "hours": (ts - prev_ts).total_seconds() / 3600,
                "tenmillion": threshold % TENMILLION_STEP == 0,
                "partial": partial,
            })
            prev_ts = ts
            partial = False
        return marks

    # ---------------- 绘图层 ----------------

    @staticmethod
    def setup_fonts() -> None:
        plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "PingFang SC", "sans-serif"]
        plt.rcParams["axes.unicode_minus"] = False

    @staticmethod
    def rotate_xlabels(ax: Axes, *, deg: int = 30) -> None:
        for label in ax.get_xticklabels():
            label.set_rotation(deg)

    def draw_trend(self, ax1: Axes, *, visible: Optional[dict[str, bool]] = None, xlim: Optional[tuple[float, float]] = None, title: Optional[str] = None) -> dict[str, Any]:	
        """在给定左轴上绘制基础趋势(堆积面积 + 总人数折线 + 总金额折线 + 里程碑标注)。

        返回绘图产物，供交互层复用: 
        {ax2, line_people, line_money, money_vals_m, x_dates, anchor_y}
        visible 为 {档位列: bool}，为 False 的档位按 0 绘制并从图例中移除。
        """
        x_dates = self.x_dates()
        last_row = self.parsed[-1][1] if self.parsed else []
        names = self.legend_names(last_row)

        def legend_name(col):
            """返回补齐空格的图例标签，如 'Lv1(￥42)   --4652'"""
            return names.get(col, col)

        labels = self.stack_labels()
        series_ys, series_labels = [], []
        for col in labels:
            if visible is not None and not visible.get(col, True):
                series_ys.append([0.0] * len(self.parsed))
                series_labels.append("_nolegend_")
            else:
                series_ys.append([v / 1e4 for v in self.col_values(col)])
                series_labels.append(legend_name(col))

        # 左轴: 支持份数堆积面积图(含各档位)，单位万人
        ax1.stackplot(x_dates, *series_ys, labels=series_labels, baseline="zero", alpha=0.85)
        ax1.set_ylabel("万人", fontsize=11)
        ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.2f}"))

        # 左轴: 总人数折线(单位万人)
        line_people = None
        if "总人数" in self.header:
            line_people, = ax1.plot(
                x_dates, [v / 1e4 for v in self.col_values("总人数")],
                color="black", linewidth=2, label=legend_name("总人数"),
            )

        # 右轴: 总金额折线(单位百万元)
        line_money = None
        money_vals_m = None
        money_yuan = None
        ax2 = ax1.twinx()
        if "总金额" in self.header:
            money_yuan = self.col_values("总金额")          # 原始金额(元)
            money_vals_m = [v / 1e6 for v in money_yuan]
            line_money, = ax2.plot(
                x_dates, money_vals_m,
                color="red", linewidth=2, label=legend_name("总金额"),
            )
            ax2.set_ylabel("总金额(百万元)", fontsize=11, color="red")
            ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.2f}"))
            ax2.tick_params(axis="y", labelcolor="red")

            # 总金额里程碑 L 形标注(整百万倍虚线 / 整千万倍实线，连到 X/Y 轴)
            valid_m = [v for v in money_vals_m if not math.isnan(v)]
            if valid_m:
                lo, hi = min(valid_m), max(valid_m)
                span = max(hi - lo, 1e-9)
                # 固定右轴范围，避免里程碑竖线拉到 y=0 导致数据区间被整体缩放
                ax2.set_ylim(lo - 0.05 * span, hi + 0.05 * span)

                def milestone_points(step):
                    """返回 [(idx, 阈值)]: 每个整 step 倍数的跨越点。
                    判定: 上一点 < k*step ≤ 当前点(相邻点比较，跳过 NaN)，即"刚超过"的第一个点; 
                    倍数发生在数据范围之外(首点之前)时自然无命中。"""
                    max_v = max(v for v in money_yuan if not math.isnan(v))
                    marks = []
                    for k in range(1, int(max_v // step) + 1):
                        threshold = k * step
                        idx = next(
                            (i for i in range(1, len(money_yuan))
                             if (not math.isnan(money_yuan[i]) and not math.isnan(money_yuan[i - 1])
                                 and money_yuan[i - 1] < threshold <= money_yuan[i])),
                            None,
                        )
                        if idx is not None:
                            marks.append((idx, threshold))
                    return marks

                # 千万级优先占点，百万级跳过同一点，避免同一位置一虚一实两条线重叠
                ten_marks = milestone_points(TENMILLION_STEP)
                ten_idx = {i for i, _ in ten_marks}
                mil_marks = [(i, t) for i, t in milestone_points(MILLION_STEP) if i not in ten_idx]

                y_bottom, _ = ax2.get_ylim()
                x_right = x_dates[-1] + X_RIGHT_PAD   # 与 xlim 右边界一致(横线连到右侧 Y 轴)
                for kind, marks in (("million", mil_marks), ("tenmillion", ten_marks)):
                    style = MILESTONE_STYLE[kind]
                    for idx, _threshold in marks:
                        x_i = x_dates[idx]
                        y_i = money_vals_m[idx]
                        # 竖线: 里程碑点垂直向下连到 X 轴
                        ax2.plot([x_i, x_i], [y_bottom, y_i], **style, linewidth=1.0)
                        # 横线: 里程碑点水平向右连到 Y 轴(右侧 spine)
                        ax2.plot([x_i, x_right], [y_i, y_i], **style, linewidth=1.0)

        ax1.yaxis.set_major_locator(mticker.MultipleLocator(2))     # 主刻度间距: 1 万人
        ax1.yaxis.set_minor_locator(mticker.MultipleLocator(0.5))   # 次刻度间距: 0.5 万人
        ax2.yaxis.set_major_locator(mticker.MultipleLocator(0.5))   # 主刻度间距: 1 百万元
        ax2.yaxis.set_minor_locator(mticker.MultipleLocator(0.1))   # 次刻度间距: 0.1 百万元

        ax1.set_xlabel("时间", fontsize=11)
        ax1.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax1.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
        # 与重构前保持逐像素一致; 分析视图的当前轴不一定是 ax1，故由调用方另行补一次旋转
        plt.xticks(rotation=30)

        # 时间轴范围: --plot 默认左边界固定紧贴左侧、右侧留 10 分钟空白; 分析视图传入窗口范围
        if xlim is None:
            xlim = (
                float(mdates.date2num(PLOT_X_LEFT)),
                float(x_dates[-1].item()) + X_RIGHT_PAD,
            )
        ax1.set_xlim(*xlim)

        if title is None:
            last_time = mdates.num2date(x_dates[-1])
            title = f"摩点众筹各档位支持人数堆积趋势(截至{last_time:%Y年%m月%d日 %H:%M})"
        ax1.set_title(title, fontsize=14, fontweight="bold")
        ax1.grid(True, linestyle="--", alpha=0.4)

        ax1.legend(loc="upper left")
        if "总金额" in self.header:
            ax2.legend(loc="center left")

        # tooltip 锚点 y: 金额线交点(无金额线时映射人数线交点到右轴坐标)
        anchor_y = None
        if money_vals_m is not None:
            anchor_y = np.asarray(money_vals_m)
        elif line_people is not None:
            yp = np.asarray([v / 1e4 for v in self.col_values("总人数")])
            pix = ax1.transData.transform(np.column_stack([np.zeros_like(yp), yp]))[:, 1]
            anchor_y = ax2.transData.inverted().transform(
                np.column_stack([np.zeros_like(pix), pix]))[:, 1]

        return {
            "ax2": ax2,
            "line_people": line_people,
            "line_money": line_money,
            "money_vals_m": money_vals_m,
            "x_dates": x_dates,
            "anchor_y": anchor_y,
        }

    def attach_dual_status(self, ax1: Axes, ax2: Axes) -> None:
        """状态栏坐标显示: 右下方默认显示 (时间, 已支持份数, 总金额)，普通数值形式"""
        if "总人数" not in self.header or "总金额" not in self.header:
            return
        time_fmt = mdates.DateFormatter("%Y-%m-%d %H:%M")

        def fmt_coord(x, y, cur_ax):
            """把鼠标所在坐标映射回两轴数据坐标，拼接为 (时间, 人数, 总金额)"""
            try:
                x_fig, y_fig = cur_ax.transData.transform((x, y))
                x1, people = ax1.transData.inverted().transform((x_fig, y_fig))
                _, money = ax2.transData.inverted().transform((x_fig, y_fig))
                # 复用与坐标轴刻度相同的格式化方式，保证时间显示一致
                time_str = time_fmt.format_ticks([x1])[0]
                # 轴数据为万人/百万元，这里还原为原始人数与金额显示
                return f"({time_str}, {people * 1e4:.0f}, {money * 1e6:.2f})"
            except Exception:
                return ""

        ax1.format_coord = lambda x, y: fmt_coord(x, y, ax1)
        ax2.format_coord = lambda x, y: fmt_coord(x, y, ax2)

    @staticmethod
    def attach_time_status(ax: Axes, unit: str = "") -> None:
        """状态栏坐标显示: 时间为日期格式，数值保留两位小数"""
        time_fmt = mdates.DateFormatter("%Y-%m-%d %H:%M")

        def fmt_coord(x, y):
            try:
                return f"({time_fmt.format_ticks([x])[0]}, {y:,.2f}{unit})"
            except Exception:
                return ""

        ax.format_coord = fmt_coord


class PlotChart(BaseChart):
    """--plot: 基础趋势图，渲染结果与重构前完全一致(无控件、无分析视图)。"""

    def plot(self) -> None:
        self.setup_fonts()
        if not self.load():
            return
        fig = plt.figure("十周年《艾希》续作《艾希: 续》Steam版众筹数据", figsize=(12, 6))
        fig.subplots_adjust(**{"left": 0.06, "bottom": 0.08, "right": 0.94, "top": 0.955})
        ax1 = fig.subplots()
        res = self.draw_trend(ax1)
        self.attach_dual_status(ax1, res["ax2"])

        # 十字跟踪虚线 + 数据行提示框
        cursor_lines = [ln for ln in (res["line_people"], res["line_money"]) if ln is not None]
        if cursor_lines:
            self.cursor = CursorTracker(fig)
            self.cursor.bind(
                panels=[res["ax2"]], anchor_ax=res["ax2"],
                x_arr=res["x_dates"], anchor_y=res["anchor_y"],
                provider=self.row_tooltip,
            )
        plt.show()


class AnalyzeChart(BaseChart):
    """--analyze: 交互式分析窗口。

    基础绘图复用 BaseChart.render 产物; 在此基础上提供五个视图与
    matplotlib 原生控件(视图切换 / 时间窗口 / 聚合粒度 / 档位显隐 / 重置)，
    并轮询 CSV 变化以保留控件状态实时重绘。
    """

    VIEW_TREND = "基础趋势"
    VIEW_GROWTH = "增速分析"
    VIEW_SHARE = "档位结构占比"
    VIEW_ARPU = "客单价指标"
    VIEW_MILESTONE = "里程碑节奏"
    VIEWS = (VIEW_TREND, VIEW_GROWTH, VIEW_SHARE, VIEW_ARPU, VIEW_MILESTONE)

    GRAN_LABELS = ("原始", "10分钟", "1小时")
    GRAN_SECONDS = (None, 600, 3600)
    GRAN_DEFAULT = 1                 # 默认 10 分钟聚合: 增速/客单价噪声与细节的折中
    GRAN_APPLIES = (VIEW_GROWTH, VIEW_ARPU)

    FIG_SIZE = (15.0, 9.0)
    PLOT_L, PLOT_R = 0.19, 0.915                # 右边界外扩，压缩与窗口边框的留白；左边界实际取
                                                # max(PLOT_L, 左侧控件列 + 轴标签列)，见 plot_l()
    PLOT_B, PLOT_T = 0.145, 0.952               # 上边界外扩，压缩与窗口边框的留白；下边界留足
                                                # "刻度标签 + 时间轴标题 + 时间窗口滑块" 三层高度
    PANEL_GAP = 0.02                            # 上下子图之间的最小留白(实际间距见 panel_gap())
    TITLE_FONT_PT = 12                          # 双子图标题字号(点数)
    TITLE_PAD_PT = 6.0                          # axes.titlepad 默认值(点数)
    TICK_LEN_PT = 3.5                           # 刻度线向外伸出的长度(点数)
    TICK_LABEL_IN = 0.90                        # 一个 "10-07 00:00" 刻度标签占用的横向宽度(英寸)
    BOTTOM_MARGIN_IN = 1.30                     # 绘图区下边界到底边所需高度(英寸)
    LEFT_MARGIN_IN = 0.90                       # 左侧 y 轴标题 + 刻度标签所需宽度(英寸)
    CTRL_COL_R = 0.16                           # 左侧控件列(单选/勾选/按钮)的右边界(图宽比例)
    SLIDER_RECT = (0.215, 0.058, 0.62, 0.028)
    SNAP_RECT = (0.215, 0.014, 0.62, 0.028)
    DONUT_RECT = (0.645, 0.245, 0.30, 0.50)     # 正方形(width * 15 == height * 9)
    MARGINAL_YLIM = (0, 650)                    # 边际客单价显示范围(覆盖全部档位价格线)
    PRICE_CLUSTER_GAP = 12                      # 价格参考线标注合并阈值(元)

    def __init__(self, csv_file: str = CSV_FILE, logger: Optional[logging.Logger] = None, refresh_interval: int = 60):
        super().__init__(csv_file=csv_file, logger=logger)
        self.setup_fonts()
        if not self.load():
            raise RuntimeError(f"无法加载数据文件: {csv_file}")
        n = max(0, len(self.parsed) - 1)
        self.state = {
            "view": self.VIEW_TREND,
            "gran": self.GRAN_DEFAULT,
            "window": (0, n),
            "snapshot": n,
            "visible": {c: True for c in self.stack_labels()},
        }
        self._ctrl_axes = []
        self._snapshot_ax = None
        self._suppress = False
        self._pending_render = False
        self._last_render = 0.0

        self.fig = plt.figure("摩点众筹数据分析", figsize=self.FIG_SIZE)
        self.cursor = CursorTracker(self.fig)
        self._build_controls()
        self._refresh_interval = max(5, int(refresh_interval))
        # 控件回调会在同一帧内密集触发，重绘按 120ms 节流 + 合并，避免拖动时逐帧全量渲染
        self._render_timer = self.fig.canvas.new_timer(interval=120)
        self._render_timer.add_callback(self._on_render_tick)
        self._render_timer.start()
        self._poll_timer = self.fig.canvas.new_timer(interval=self._refresh_interval * 1000)
        self._poll_timer.add_callback(self._poll_data)
        self._poll_timer.start()

        self.render_view()

    def show(self) -> None:
        plt.show()

    # ---------------- 控件 ----------------

    def _build_controls(self) -> None:
        fig = self.fig

        def ctrl_ax(rect: tuple[float, float, float, float]) -> Axes:
            ax = fig.add_axes(rect)
            self._ctrl_axes.append(ax)
            return ax

        # 视图切换
        self._ax_view = ctrl_ax((0.015, 0.63, 0.145, 0.255))
        self._ax_view.set_title("视图", fontsize=10, pad=2)
        self.radio_view = RadioButtons(
            self._ax_view, list(self.VIEWS), active=0, label_props={"fontsize": [9]},
        )
        self.radio_view.on_clicked(self._on_view_change)

        # 聚合粒度
        self._ax_gran = ctrl_ax((0.015, 0.475, 0.145, 0.105))
        self._ax_gran.set_title("聚合粒度", fontsize=10, pad=2)
        self.radio_gran = RadioButtons(
            self._ax_gran, list(self.GRAN_LABELS), active=self.state["gran"],
            label_props={"fontsize": [9]},
        )
        self.radio_gran.on_clicked(self._on_gran_change)

        # 重置(交互逃生通道)
        self._ax_reset = ctrl_ax((0.015, 0.42, 0.145, 0.04))
        self.btn_reset = Button(self._ax_reset, "重置", hovercolor="#d9d9d9")
        self.btn_reset.on_clicked(self._on_reset)

        # 档位显隐
        self._ax_tier = ctrl_ax((0.015, 0.03, 0.145, 0.355))
        self._ax_tier.set_title("档位显隐", fontsize=10, pad=2)
        self._build_tier_check()

        # 时间窗口(RangeSlider: 起止双柄，作用于所有时序视图)
        ax_time = ctrl_ax(self.SLIDER_RECT)
        n = max(0, len(self.parsed) - 1)
        self.slider_window = RangeSlider(
            ax_time, "时间窗口", 0, n, valinit=(0, n), valstep=1, valfmt="%d",
        )
        self.slider_window.on_changed(self._on_window_change)

        # 快照时刻(仅占比视图的环形图使用)
        ax_snap = ctrl_ax(self.SNAP_RECT)
        self.slider_snap = Slider(
            ax_snap, "快照时刻", 0, n, valinit=n, valstep=1, valfmt="%d",
        )
        self.slider_snap.on_changed(self._on_snap_change)
        self._sync_slider_text()

    def _build_tier_check(self) -> None:
        labels = self.stack_labels()
        self._tier_labels = labels
        actives = [bool(self.state["visible"].get(c, True)) for c in labels]
        self._ax_tier.clear()
        self.check_tier = CheckButtons(
            self._ax_tier, labels, actives, label_props={"fontsize": [8]},
        )
        self.check_tier.on_clicked(self._on_tier_toggle)

    def _sync_control_state(self) -> None:
        """按当前视图标注各控件是否适用(不适用则置灰并停用交互)"""
        view = self.state["view"]
        gran_on = view in self.GRAN_APPLIES
        tier_on = view in (self.VIEW_TREND, self.VIEW_SHARE)
        window_on = view != self.VIEW_MILESTONE
        snap_on = view == self.VIEW_SHARE

        # RadioButtons / CheckButtons 的 set_active() 是"选中第 N 项"，
        # 启停须走 Widget.active 属性(Slider 的 set_active 才是启停)
        self.radio_gran.active = gran_on
        self._ax_gran.set_title("聚合粒度" + ("" if gran_on else "(本视图不适用)"), fontsize=10, pad=2)
        self.check_tier.active = tier_on
        self._ax_tier.set_title("档位显隐" + ("" if tier_on else "(本视图不适用)"), fontsize=10, pad=2)
        # 时间窗口滑块的"不适用"用整体变淡表示: 标签加长后缀会向左顶进档位显隐面板里
        self.slider_window.set_active(window_on)
        self.slider_window.label.set_text("时间窗口")
        dim = 1.0 if window_on else 0.3
        for artist in (self.slider_window.label, self.slider_window.poly,
                       self.slider_window.valtext, *self.slider_window._handles):
            artist.set_alpha(dim)
        self.slider_snap.ax.set_visible(snap_on)

    def _sync_slider_text(self) -> None:
        """滑块数值文本改为可读时间(默认 valfmt 只显示索引)"""
        lo, hi = self.state["window"]
        t_lo = self.parsed[lo][0] if self.parsed else None
        t_hi = self.parsed[hi][0] if self.parsed else None
        self.slider_window.valtext.set_text(
            f"{t_lo:%m-%d %H:%M} ~ {t_hi:%m-%d %H:%M}" if t_lo and t_hi else ""
        )
        idx = min(self.state["snapshot"], len(self.parsed) - 1) if self.parsed else 0
        self.slider_snap.valtext.set_text(
            f"{self.parsed[idx][0]:%m-%d %H:%M}" if self.parsed else ""
        )

    # ---------------- 控件回调 ----------------

    def _on_view_change(self, label: Optional[str]) -> None:
        if self._suppress or label == self.state["view"]:
            return
        self.state["view"] = label
        self.render_view()

    def _on_gran_change(self, label: Optional[str]) -> None:
        if self._suppress:
            return
        idx = self.GRAN_LABELS.index(label)
        if idx == self.state["gran"]:
            return
        self.state["gran"] = idx
        if self.state["view"] in self.GRAN_APPLIES:
            self.render_view()

    def _on_tier_toggle(self, label: Optional[str]) -> None:
        if self._suppress:
            return
        for lb, on in zip(self._tier_labels, self.check_tier.get_status()):
            self.state["visible"][lb] = bool(on)
        if self.state["view"] in (self.VIEW_TREND, self.VIEW_SHARE):
            self.render_view()

    def _on_window_change(self, val: tuple) -> None:
        if self._suppress:
            return
        lo, hi = int(round(val[0])), int(round(val[1]))
        if (lo, hi) == self.state["window"]:
            return
        self.state["window"] = (lo, hi)
        self._sync_slider_text()
        if self.state["view"] != self.VIEW_MILESTONE:
            self._request_render(delay=0.15)

    def _on_snap_change(self, val: float) -> None:
        if self._suppress:
            return
        idx = int(round(val))
        if idx == self.state["snapshot"]:
            return
        self.state["snapshot"] = idx
        self._sync_slider_text()
        if self.state["view"] == self.VIEW_SHARE and self._snapshot_ax is not None:
            # 快照面板很小，直接清空重绘该面板即可(widget 自身只 blit 自己区域)
            self._draw_snapshot(self._snapshot_ax)
            self.fig.canvas.draw_idle()

    def _on_reset(self, event):
        n = max(0, len(self.parsed) - 1)
        self._suppress = True
        try:
            self.state["view"] = self.VIEW_TREND
            self.state["window"] = (0, n)
            self.state["gran"] = self.GRAN_DEFAULT
            self.state["snapshot"] = n
            for col in self.state["visible"]:
                self.state["visible"][col] = True
            self.radio_view.set_active(0)
            self.radio_gran.set_active(self.state["gran"])
            for i, on in enumerate(self.check_tier.get_status()):
                if not on:
                    self.check_tier.set_active(i, state=True)
            self.slider_window.set_val((0, n))
            self.slider_snap.set_val(n)
            self._sync_slider_text()
        finally:
            self._suppress = False
        self.render_view()

    # ---------------- 重绘调度 ----------------

    def _request_render(self, delay=0.0):
        """控件拖动时按 delay 节流，多余请求合并到定时器下一次触发"""
        now = time.monotonic()
        if delay and now - self._last_render < delay:
            self._pending_render = True
            return
        self._pending_render = False
        self._last_render = now
        self.render_view()

    def _on_render_tick(self):
        if self._pending_render:
            self._request_render()

    def _poll_data(self):
        """定时检查 CSV 变化: 重载数据并保留控件状态重绘当前视图"""
        try:
            mtime = os.path.getmtime(self.csv_file)
        except OSError:
            return
        if mtime == self.mtime:
            return
        old_n = max(0, len(self.parsed) - 1)
        if not self.load(quiet=True):
            return
        n = max(0, len(self.parsed) - 1)
        lo, hi = self.state["window"]
        self._suppress = True
        try:
            # 窗口/快照原本停在末尾时跟随新数据，否则保持用户选定的区间
            if hi >= old_n:
                hi = n
            if self.state["snapshot"] >= old_n:
                self.state["snapshot"] = n
            self.state["window"] = (min(lo, n), hi)
            self.slider_window.valmax = n
            self.slider_window.ax.set_xlim(0, n)
            self.slider_window.set_val(self.state["window"])
            self.slider_snap.valmax = n
            self.slider_snap.ax.set_xlim(0, n)
            self.slider_snap.set_val(self.state["snapshot"])
            self._sync_slider_text()
            new_labels = self.stack_labels()
            if new_labels != self._tier_labels:
                # 页面新增档位列: 重建显隐控件
                for col in new_labels:
                    self.state["visible"].setdefault(col, True)
                self._build_tier_check()
        finally:
            self._suppress = False
        self.render_view()

    # ---------------- 视图渲染 ----------------

    def _clear_view_axes(self) -> None:
        for ax in list(self.fig.axes):
            if ax in self._ctrl_axes:
                continue
            self.fig.delaxes(ax)
        self._snapshot_ax = None
        self.cursor.bind(panels=[], anchor_ax=None, x_arr=np.array([]), anchor_y=np.array([]),  provider=None)

    def window_xlim(self, pad: bool = True) -> Optional[tuple[float, float]]:
        """时间窗口对应的 x 轴范围(matplotlib 日期数值)"""
        if not self.parsed:
            return None
        lo, hi = self.state["window"]
        lo = min(max(lo, 0), len(self.parsed) - 1)
        hi = min(max(hi, 0), len(self.parsed) - 1)
        return (
            float(mdates.date2num(self.parsed[lo][0])),
            float(mdates.date2num(self.parsed[hi][0])) + (X_RIGHT_PAD if pad else 0)
        )

    def _notice(self, text: str) -> None:
        """数据不足时的占位面板"""
        ax = self.fig.add_axes((self.plot_l(), self.plot_b(), self.PLOT_R - self.plot_l(), self.PLOT_T - self.plot_b()))
        ax.set_xticks([])
        ax.set_yticks([])
        ax.text(0.5, 0.5, text, ha="center", va="center", fontsize=12, color="#666666")
        ax.set_title(self.state["view"], fontsize=13)

    def plot_l(self) -> float:
        """绘图区左边界(图宽比例)。

        左边界左侧要依次排下 y 轴标题与刻度标签(以磅/英寸计，不随窗口缩放)，再往左是控件列。
        固定比例在窄窗口或宽数值标签(如 "160,000")下会让刻度标签钻到控件面板底下被遮住，
        因此按"控件列右边界 + 标签所需英寸数"换算。
        """
        w_in = self.fig.get_figwidth()
        return min(0.34, max(self.PLOT_L, self.CTRL_COL_R + self.LEFT_MARGIN_IN / w_in))

    def plot_b(self) -> float:
        """绘图区下边界(图高比例)。

        底部自上而下叠着 x 刻度标签、时间轴标题、时间窗口滑块、快照滑块。字号与滑块轨道高度
        以磅/像素计、不随窗口缩放，若下边界用固定比例，窗口一变小时轴标题就会重新压到滑块上，
        所以按"底部所需英寸数"换算成当前图高的比例。
        """
        return max(self.PLOT_B, self.BOTTOM_MARGIN_IN / self.fig.get_figheight())

    def panel_gap(self) -> float:
        """上下子图间距(图高比例)。

        上子图关闭了刻度标签，但下子图标题画在自己顶部之上、上子图的刻度线也向下伸出，
        固定的小间距会让标题压在上子图底边上。字号以磅为单位不随窗口缩放，而这里的坐标是
        图高比例，所以按当前图高换算所需间距。
        """
        need_pt = self.TITLE_FONT_PT * 1.45 + self.TITLE_PAD_PT + self.TICK_LEN_PT + 4
        return max(self.PANEL_GAP, need_pt / 72.0 / self.fig.get_figheight())

    def time_maxticks(self, ax: Axes) -> int:
        """按轴宽推算时间刻度上限，避免窄轴上的 "10-07 00:00" 标签彼此重叠"""
        width_in = ax.get_position().width * self.fig.get_figwidth()
        return max(3, min(11, int(width_in / self.TICK_LABEL_IN)))

    def _apply_time_axis(self, ax: Axes, xlim: Optional[tuple[Any, Any]] = None) -> None:
        """时序视图统一的时间轴样式(刻度水平放置，避免 30° 倾斜时伸入下方滑块区)"""
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=self.time_maxticks(ax)))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
        self.rotate_xlabels(ax, deg=0)
        if xlim:
            ax.set_xlim(*xlim)

    def render_view(self) -> None:
        """清空绘图区并按当前状态重绘选中视图，随后同步控件可用状态"""
        self._clear_view_axes()
        view = self.state["view"]
        if view == self.VIEW_TREND:
            self._draw_trend_view()
        elif view == self.VIEW_GROWTH:
            self._draw_growth_view()
        elif view == self.VIEW_SHARE:
            self._draw_share_view()
        elif view == self.VIEW_ARPU:
            self._draw_arpu_view()
        else:
            self._draw_milestone_view()
        self._sync_control_state()
        self.fig.canvas.draw_idle()

    # ---- 视图一: 基础趋势(复用 BaseChart 基础渲染)----

    def _draw_trend_view(self) -> None:
        rect = (self.plot_l(), self.plot_b(), self.PLOT_R - self.plot_l(), self.PLOT_T - self.plot_b())
        ax1 = self.fig.add_axes(rect)
        res = self.draw_trend(ax1, visible=self.state["visible"], xlim=self.window_xlim())
        self.rotate_xlabels(ax1, deg=0)       # draw_trend 内的 plt.xticks 面向 figure 当前轴，此处显式拉平
        ax2 = res["ax2"]
        self.attach_dual_status(ax1, ax2)
        if res["line_people"] is not None or res["line_money"] is not None:
            self.cursor.bind(
                panels=[ax2], anchor_ax=ax2,
                x_arr=res["x_dates"], anchor_y=res["anchor_y"],
                provider=self.row_tooltip,
            )

    # ---- 视图二: 增速分析(上下双子图，量纲独立)----

    def _draw_growth_view(self) -> None:
        sec = self.GRAN_SECONDS[self.state["gran"]]
        xs, rp, rm = self.rate_series(sec)
        if not xs:
            self._notice("聚合后无有效数据点，无法计算增速。")
            return
        x = mdates.date2num(xs)
        w = self.PLOT_R - self.plot_l()
        top_p = self.PLOT_T
        ax_p = self.fig.add_axes((self.plot_l(), top_p - 0.395, w, 0.395))
        top_m = top_p - 0.395 - self.panel_gap()
        ax_m = self.fig.add_axes((self.plot_l(), self.plot_b(), w, top_m - self.plot_b()), sharex=ax_p)
        ax_p.xaxis.set_tick_params(labelbottom=False)
        gran = self.GRAN_LABELS[self.state["gran"]]

        ax_p.plot(x, rp, color="#1f77b4", linewidth=1.2)
        ax_p.axhline(0, color="#999999", linewidth=0.8, linestyle="--")
        ax_p.set_ylabel("人/小时", fontsize=10)
        ax_p.set_title(f"人数增速({gran}聚合，负值为退订)", fontsize=12, fontweight="bold")
        ax_p.grid(True, linestyle="--", alpha=0.4)

        ax_m.plot(x, rm, color="#d62728", linewidth=1.2)
        ax_m.axhline(0, color="#999999", linewidth=0.8, linestyle="--")
        ax_m.set_ylabel("元/小时", fontsize=10, color="#d62728")
        ax_m.yaxis.set_major_formatter(mticker.FuncFormatter(_fmt_money))
        ax_m.tick_params(axis="y", labelcolor="#d62728")
        ax_m.set_xlabel("时间", fontsize=11)
        ax_m.set_title(f"金额增速({gran}聚合)", fontsize=12, fontweight="bold")
        ax_m.grid(True, linestyle="--", alpha=0.4)

        self._apply_time_axis(ax_m, self.window_xlim())
        self.attach_time_status(ax_p, " 人/时")
        self.attach_time_status(ax_m, " 元/时")

        def provider(i: int) -> list[str]:
            return [
                f"{xs[i]:%Y-%m-%d %H:%M}",
                f"人数增速: {rp[i]:,.2f} 人/时",
                f"金额增速: {rm[i]:,.2f} 元/时",
            ]

        self.cursor.bind(
            panels=[ax_p, ax_m], anchor_ax=ax_p,
            x_arr=x, anchor_y=np.asarray(rp, dtype=float), provider=provider,
        )

    # ---- 视图三: 档位结构占比(100% 堆积 + 环形快照)----

    def _draw_share_view(self) -> None:
        labels = [c for c in self.stack_labels() if self.state["visible"].get(c, True)]
        if not labels:
            self._notice("已隐藏全部档位，请在左侧至少保留一个档位。")
            return
        xs, series, _totals = self.share_matrix(labels)
        if not xs:
            self._notice("档位数据不可用，无法计算占比。")
            return
        x = mdates.date2num(xs)
        stack_w = max(0.30, self.DONUT_RECT[0] - 0.02 - self.plot_l())
        ax = self.fig.add_axes((self.plot_l(), self.plot_b(), stack_w, self.PLOT_T - self.plot_b()))
        # 显式指定颜色，保证隐藏档位后其余档位配色不变(与基础趋势图一致)
        ax.stackplot(x, *series,
                     labels=labels, colors=[self.tier_color(c) for c in labels],
                     baseline="zero", alpha=0.85)
        ax.set_ylim(0, 100)
        ax.set_ylabel("占比(%)", fontsize=11)
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}"))
        ax.set_title("档位结构占比迁移(占档位人数总和)", fontsize=12, fontweight="bold")
        ax.grid(True, linestyle="--", alpha=0.4)
        self._apply_time_axis(ax, self.window_xlim())
        self.attach_time_status(ax, " %")
        # 档位配色与右侧快照图例一致，故此处不再重复图例

        self._snapshot_ax = self.fig.add_axes((self.DONUT_RECT))
        self._draw_snapshot(self._snapshot_ax)

        def provider(i):
            lines = [f"{xs[i]:%Y-%m-%d %H:%M}"]
            for k, lb in enumerate(labels):
                lines.append(f"{lb}: {series[k][i]:.2f}%")
            return lines

        self.cursor.bind(
            panels=[ax], anchor_ax=ax,
            x_arr=x, anchor_y=np.zeros(len(x)), provider=provider,
        )

    def _draw_snapshot(self, ax: Axes) -> None:
        """环形图快照: 档位顺序固定为 Lv1~Lv9、无偿支持(与堆积顺序一致)，极小档位由图例数字兜底"""
        ax.clear()
        labels = self.stack_labels()
        if not self.parsed:
            return
        idx = min(max(self.state["snapshot"], 0), len(self.parsed) - 1)
        ts, row = self.parsed[idx]
        pairs = []
        for c in labels:
            if not self.state["visible"].get(c, True):
                continue
            v = self.cell(row, c)
            pairs.append((c, 0.0 if math.isnan(v) or v < 0 else v))
        total = sum(v for _, v in pairs)
        ax.set_xlim(-1.15, 1.15)
        ax.set_ylim(-1.15, 1.15)
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.set_title(f"占比快照 {ts:%m-%d %H:%M}", fontsize=11)
        if total <= 0:
            ax.text(0, 0, "无档位数据", ha="center", va="center", fontsize=10, color="#666666")
            return

        radius, width = 1.00, 0.24           # 外半径与环宽(环细一些，图例移到图下方)
        theta = 90.0                          # 自 12 点方向顺时针
        for name, v in pairs:
            frac = v / total
            span = frac * 360
            color = self.tier_color(name)
            ax.add_patch(Wedge(
                (0, 0), radius, theta - span, theta, width=width,
                facecolor=color, edgecolor="white", linewidth=1.2, zorder=3,
            ))
            if frac >= 0.03:                  # 过小的扇形不标注，由图例数值兜底
                mid = math.radians(theta - span / 2)
                r_lab = radius - width / 2
                ax.text(r_lab * math.cos(mid), r_lab * math.sin(mid), f"{frac * 100:.1f}%",
                        ha="center", va="center", fontsize=8, color="white",
                        fontweight="bold", zorder=4)
            theta -= span
        ax.text(0, 0, f"{ts:%m-%d %H:%M}\n合计 {total:,.0f} 人",
                ha="center", va="center", fontsize=9, zorder=4)

        handles = [Patch(facecolor=self.tier_color(n), edgecolor="white") for n, _ in pairs]
        texts = [f"{n}  {v / total * 100:.2f}%  ({v:,.0f})" for n, v in pairs]
        ax.legend(handles, texts, loc="upper center", bbox_to_anchor=(0.5, 0.0),
                  ncol=2, fontsize=7, handlelength=1.1, handleheight=0.9,
                  columnspacing=1.0, labelspacing=0.3, frameon=False)

    # ---- 视图四: 客单价指标 ----

    def _draw_arpu_view(self) -> None:
        sec = self.GRAN_SECONDS[self.state["gran"]]
        xs, cum, marg = self.arpu_series(sec)
        if not xs:
            self._notice("聚合后无有效数据点，无法计算客单价。")
            return
        x = mdates.date2num(xs)
        w = self.PLOT_R - self.plot_l()
        gran = self.GRAN_LABELS[self.state["gran"]]
        # 上: 边际客单价(新用户实际支付水平)叠加档位价格参考线
        # 下: 累计客单价单独放大，避免与 42~598 元的价格线量纲互相压平
        ax_m = self.fig.add_axes((self.plot_l(), self.PLOT_T - 0.425, w, 0.425))
        top_c = self.PLOT_T - 0.425 - self.panel_gap()
        ax_c = self.fig.add_axes((self.plot_l(), self.plot_b(), w, top_c - self.plot_b()), sharex=ax_m)
        ax_m.xaxis.set_tick_params(labelbottom=False)

        prices = self.tier_prices()
        for _col, price in prices:
            ax_m.axhline(price, color="#888888", linestyle="--", linewidth=0.8, zorder=1)
        ax_m.plot(x, marg, color="#1f77b4", linewidth=1.2, zorder=3)
        ax_m.set_ylim(*self.MARGINAL_YLIM)
        ax_m.set_ylabel("元/人", fontsize=10)
        ax_m.set_title(f"边际客单价({gran}聚合，Δ金额/Δ人数)与档位价格参考线",
                       fontsize=12, fontweight="bold")
        ax_m.grid(True, linestyle="--", alpha=0.4)
        self._label_price_lines(ax_m, prices)
        lo, hi = self.MARGINAL_YLIM
        out_of_range = sum(1 for v in marg if math.isnan(v) or not (lo <= v <= hi))
        if out_of_range:
            ax_m.text(0.01, 0.02, f"{out_of_range} 个点不在 {lo:.0f}~{hi:.0f} 元区间(未显示)",
                      transform=ax_m.transAxes, fontsize=8, color="#777777", va="bottom")

        ax_c.plot(x, cum, color="red", linewidth=1.6)
        ax_c.set_ylabel("元/人", fontsize=10, color="red")
        ax_c.tick_params(axis="y", labelcolor="red")
        ax_c.set_title("累计客单价(总金额 ÷ 总人数)", fontsize=12, fontweight="bold")
        ax_c.grid(True, linestyle="--", alpha=0.4)
        ax_c.set_xlabel("时间", fontsize=11)
        if cum:
            lo_c, hi_c = min(cum), max(cum)
            pad = max((hi_c - lo_c) * 0.15, 0.5)
            ax_c.set_ylim(lo_c - pad, hi_c + pad)
            ax_c.annotate(f"{cum[0]:.2f}", (x[0], cum[0]), textcoords="offset points",
                          xytext=(4, 6), fontsize=8, color="#555555")
            ax_c.annotate(f"{cum[-1]:.2f}", (x[-1], cum[-1]), textcoords="offset points",
                          xytext=(-4, -12), fontsize=8, color="#555555", ha="right")

        self._apply_time_axis(ax_c, self.window_xlim())
        self.attach_time_status(ax_m, " 元/人")
        self.attach_time_status(ax_c, " 元/人")

        def provider(i):
            return [
                f"{xs[i]:%Y-%m-%d %H:%M}",
                f"边际客单价: {marg[i]:,.2f} 元/人" if not math.isnan(marg[i]) else "边际客单价: --",
                f"累计客单价: {cum[i]:,.2f} 元/人",
            ]

        self.cursor.bind(
            panels=[ax_m, ax_c], anchor_ax=ax_c,
            x_arr=x, anchor_y=np.asarray(cum, dtype=float), provider=provider,
        )

    def _label_price_lines(self, ax: Axes, prices: list[tuple[str, float]]) -> None:
        """价格参考线右侧直接标注; 间距过小的价格(如 42 / 45.6)合并为同一标注"""
        clusters = []
        for col, price in prices:
            if clusters and price - clusters[-1][-1][1] < self.PRICE_CLUSTER_GAP:
                clusters[-1].append((col, price))
            else:
                clusters.append([(col, price)])
        for cluster in clusters:
            avg = sum(p for _, p in cluster) / len(cluster)
            text = " / ".join(f"￥{p:g}" for _c, p in cluster)
            ax.text(1.005, avg, text, transform=ax.get_yaxis_transform(),
                    ha="left", va="center", fontsize=8, color="#555555")

    # ---- 视图五: 里程碑节奏 ----

    def _draw_milestone_view(self) -> None:
        marks = self.milestones()
        if not marks:
            self._notice("未找到可用的金额数据，无法计算里程碑节奏。")
            return
        rect = (self.plot_l(), self.plot_b(), self.PLOT_R - self.plot_l(), self.PLOT_T - self.plot_b())
        ax = self.fig.add_axes(rect)
        axr = ax.twinx()
        xs = mdates.date2num([m["ts"] for m in marks])

        bar_w = 8 / 24                          # 固定 8 小时宽度的柱(时间点标记，非耗时跨度)
        for m, x in zip(marks, xs):
            color = "#4B0082" if m["tenmillion"] else "#1f77b4"
            ax.bar(x, m["hours"], width=bar_w, color=color, edgecolor="#333333",
                   linewidth=0.8, zorder=3)
            label = f"{m['value'] / MILLION_STEP:.0f}M\n{m['hours']:.1f}h"
            if m["partial"]:
                label += "*"
            ax.annotate(label, (x, m["hours"]), textcoords="offset points", xytext=(0, 3),
                        ha="center", va="bottom", fontsize=7.5, zorder=4)

        hours = [m["hours"] for m in marks]
        ax.set_ylim(0, max(hours) * 1.28)
        ax.set_ylabel("耗时(小时)", fontsize=11)
        ax.set_title("里程碑节奏: 每提升 100 万元所需的耗时(紫柱为千万元级)",
                     fontsize=13, fontweight="bold")
        ax.grid(True, axis="y", linestyle="--", alpha=0.4)
        ax.text(0.01, 0.97, "* 首个里程碑自数据起点起算，区间不完整，耗时偏小",
                transform=ax.transAxes, fontsize=8, color="#777777", va="top", zorder=6)

        # 累计金额曲线(右轴): 作为"当时筹款斜率"的上下文
        money_m = [v / MILLION_STEP if not math.isnan(v) else float("nan")
                   for v in self.col_values("总金额")]
        axr.plot(self.x_dates(), money_m, color="red", linewidth=1.6, alpha=0.55, zorder=2)
        axr.set_ylabel("累计金额(百万元)", fontsize=11, color="red")
        axr.tick_params(axis="y", labelcolor="red")

        x_lo = float(min(xs)) - 12 / 24
        x_hi = float(max(xs)) + 24 / 24
        ax.set_xlim(x_lo, x_hi)
        in_view = [v for xv, v in zip(self.x_dates(), money_m) if x_lo <= xv <= x_hi and not math.isnan(v)]
        if in_view:
            lo, hi = min(in_view), max(in_view)
            span = max(hi - lo, 1e-9)
            axr.set_ylim(lo - 0.08 * span, hi + 0.08 * span)
        self._apply_time_axis(ax)
        self.attach_time_status(ax, " 小时")
        self.attach_time_status(axr, " 百万元")

        anchor_y = [m["value"] / MILLION_STEP for m in marks]

        def provider(i):
            m = marks[i]
            note = "(自数据起点起算，区间不完整)" if m["partial"] else ""
            return [
                f"{m['value'] / MILLION_STEP:.0f} 百万元里程碑",
                f"跨越时刻: {m['ts']:%Y-%m-%d %H:%M}",
                f"耗时: {m['hours']:.2f} 小时{note}",
            ]

        self.cursor.bind(
            panels=[axr], anchor_ax=axr,
            x_arr=xs, anchor_y=np.asarray(anchor_y, dtype=float), provider=provider,
        )
