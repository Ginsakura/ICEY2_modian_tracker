# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "beautifulsoup4>=4.15.0",
#     "matplotlib>=3.11.2",
#     "requests>=2.34.2",
# ]
# ///
import argparse
import csv
import math
import os
import re
import threading
import time
from datetime import datetime
import requests
from bs4 import BeautifulSoup

import sync_data
from shared_logger import get_logger

logger = get_logger("modian_tracker")

URL = "https://zhongchou.modian.com/item/160465.html"
CSV_FILE = "data.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# 总金额里程碑 L 形标注：数据范围内每个整百万倍（27M、28M…，虚线）/整千万倍（30M、40M…，实线），
# 判定"刚超过"：上一点 < 阈值 ≤ 当前点（相邻点比较）；千万倍数优先，同一点不重复标记。
# 固定颜色与红色总金额折线及堆积区域色（蓝/橙/绿/红/紫/棕/粉/灰/黄绿/青）均保持可区分
MILLION_STEP = 1_000_000        # 百万级里程碑间距（虚线）
TENMILLION_STEP = 10_000_000    # 千万级里程碑间距（实线）
MILESTONE_STYLE = {
    "million": {"linestyle": "--", "color": "#4B0082"},      # 深靛紫虚线
    "tenmillion": {"linestyle": "-", "color": "#000000"},    # 黑色实线
}

def parse_tier_data(html):
    """解析页面 HTML，返回 (档位字典, 总人数, 总金额)"""
    soup = BeautifulSoup(html, "html.parser")
    tier_dict = {}

    level = 1
    # 仅选取档位卡片内的计数元素，按页面顺序排列（首个为“无偿支持”）
    for em in soup.select(".back-list em.count"):
        card = em.find_parent("div", class_="back-list")
        card_text = card.get_text()

        # 优先使用 i 属性（售罄档位文本为“已抢光／限 X 份”，仍可从 i 取得支持数）
        count = None
        if em.get("i"):
            count = int(em["i"])
        else:
            count_match = re.search(r"(\d+)\s*份", em.get_text())
            if count_match:
                count = int(count_match.group(1))
        if count is None:
            continue

        if "无偿支持" in card_text:
            # 无偿支持为独立档位，不带 Lv 编号与价格
            tier_dict["无偿支持"] = count
            continue

        price_match = re.search(r"[¥￥]\s*([\d\.]+)", card_text)
        if not price_match:
            continue
        price = price_match.group(1)
        # 付费档位按出现顺序编号为 Lv1、Lv2 ...
        tier_dict[f"Lv{level}（￥{price}）"] = count
        level += 1

    # 提取页面总支持人数与已筹总金额
    total_count = 0
    total_money = 0.0
    count_el = soup.select_one("span[backer_count]")
    money_el = soup.select_one("span[backer_money]")
    if count_el:
        total_count = int(count_el.get_text().replace(",", "").strip())
    if money_el:
        total_money = float(money_el.get_text().replace(",", "").strip())

    return tier_dict, total_count, total_money

def fetch_tier_data():
    """抓取页面中各档位价格及支持人数（首个为无偿支持，其后为 Lv1~Lv9）"""
    resp = requests.get(URL, headers=HEADERS, timeout=15)
    resp.encoding = "utf-8"
    return parse_tier_data(resp.text)

def _clean_cell(value):
    """去除人数类数值末尾的 .0（如 '4647.0' -> '4647'）；空值保持为空"""
    if value is None:
        return ""
    s = str(value).strip()
    return s[:-2] if s.endswith(".0") else s

def _write_csv(rows, money_idx):
    """写入 CSV（utf-8-sig）；时间戳与总金额保持原样，其余列去除 .0 后缀"""
    with open(CSV_FILE, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        for row in rows:
            cleaned = [
                "" if cell is None else (cell if i == 0 or i == money_idx else _clean_cell(cell))
                for i, cell in enumerate(row)
            ]
            writer.writerow(cleaned)

def append_to_csv(data, total_count, total_money):
    """带时间戳追加到 CSV 文件（原生 csv 实现，无需 pandas）"""
    if not data:
        logger.warning("未抓取到有效档位数据，请确认页面是否正常。")
        return

    rows = []
    if os.path.exists(CSV_FILE):
        with open(CSV_FILE, "r", encoding="utf-8-sig", newline="") as f:
            rows = [r for r in csv.reader(f) if r]

    if rows:
        header = rows[0]
        # 若页面新增档位列，追加到表头并补齐历史行空位
        for col in data:
            if col not in header:
                header.append(col)
                for old in rows[1:]:
                    old.append("")
    else:
        header = ["timestamp", "总人数", "总金额", *data.keys()]
        rows = [header]

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_row = [""] * len(header)
    for i, col in enumerate(header):
        if col == "timestamp":
            new_row[i] = now_str
        elif col == "总人数":
            new_row[i] = total_count if total_count else ""
        elif col == "总金额":
            new_row[i] = total_money if total_money else ""
        elif col in data:
            new_row[i] = data[col]
    rows.append(new_row)

    money_idx = header.index("总金额") if "总金额" in header else None
    _write_csv(rows, money_idx)
    logger.info("成功记录 %d 个档位数据至 %s", len(data), CSV_FILE)

def plot_trends():
    """读取 CSV 数据并生成折线图（原生 csv 读取，无需 pandas）"""
    if not os.path.exists(CSV_FILE):
        print(f"未找到数据文件: {CSV_FILE}，请先执行记录。")
        return

    with open(CSV_FILE, "r", encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.reader(f) if r]
    if len(rows) < 2 or "timestamp" not in rows[0]:
        print("CSV 文件为空或未包含有效时间戳列。")
        return

    header = rows[0]

    # 仅在绘图时需要 matplotlib，因此在此处按需导入
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    # 中文字体设置
    plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "PingFang SC", "sans-serif"]
    plt.rcParams["axes.unicode_minus"] = False

    # 解析时间戳并按时序排序（跳过解析失败的行）
    parsed = []
    for r in rows[1:]:
        try:
            ts = datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S")
        except (ValueError, IndexError):
            continue
        parsed.append((ts, r))
    parsed.sort(key=lambda item: item[0])

    # 取最新一行数据，用于在图例中展示各系列当前值
    last_row = parsed[-1][1] if parsed else []

    def visual_width(s):
        """估算渲染宽度：全角字符按 2 个半角字符计，用于空格补齐对齐"""
        return sum(2 if ord(ch) > 0x2E80 else 1 for ch in s)

    timestamps = [item[0] for item in parsed]
    x_dates = mdates.date2num(timestamps)

    def col_values(name):
        idx = header.index(name)
        vals = []
        for _, r in parsed:
            try:
                vals.append(float(r[idx]) if r[idx] else float("nan"))
            except (ValueError, IndexError):
                vals.append(float("nan"))
        return vals

    # 堆积顺序：自下而上 Lv1~Lv9、无偿支持
    def lv_no(col):
        m = re.search(r"Lv(\d+)", col)
        return int(m.group(1)) if m else -1

    lv_cols = sorted([c for c in header if c.startswith("Lv")], key=lv_no)
    stack_labels = lv_cols + (["无偿支持"] if "无偿支持" in header else [])

    # 图例标签：列名 + 最新值，用空格补齐使 "--" 竖排对齐（\t 在图例中无法对齐）
    legend_vals = {
        c: last_row[header.index(c)]
        for c in stack_labels + ["总人数", "总金额"]
        if c in header and last_row
    }
    max_left = max((visual_width(c) for c, v in legend_vals.items() if v), default=0)
    legend_text = {
        c: (c + " " * (max_left - visual_width(c)) + f"--{v}" if v else c)
        for c, v in legend_vals.items()
    }

    def legend_name(col):
        """返回补齐空格的图例标签，如 'Lv1（￥42）   --4652'"""
        return legend_text.get(col, col)

    fig = plt.figure(f'十周年《艾希》续作《艾希：续》Steam版众筹数据', figsize=(12, 6))
    fig.subplots_adjust(**{"left":0.06,"bottom":0.08,"right":0.94,"top":0.955})
    ax1 = fig.subplots()

    # 左轴：支持份数堆积面积图（含各档位），单位万人
    stack_ys = [[v / 1e4 for v in col_values(c)] for c in stack_labels]
    ax1.stackplot(x_dates, *stack_ys, labels=[legend_name(c) for c in stack_labels], baseline="zero", alpha=0.85)
    ax1.set_ylabel("万人", fontsize=11)
    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.2f}"))

    # 左轴：总人数折线（单位万人）；保存引用供 mplcursors 使用
    line_people = None
    if "总人数" in header:
        line_people, = ax1.plot(
            x_dates, [v / 1e4 for v in col_values("总人数")],
            color="black", linewidth=2, label=legend_name("总人数"),
        )

    # 右轴：总金额折线（单位百万元）；保存引用供 mplcursors 使用
    line_money = None
    ax2 = ax1.twinx()
    if "总金额" in header:
        money_yuan = col_values("总金额")          # 原始金额（元）
        money_vals_m = [v / 1e6 for v in money_yuan]
        line_money, = ax2.plot(
            x_dates, money_vals_m,
            color="red", linewidth=2, label=legend_name("总金额"),
        )
        ax2.set_ylabel("总金额（百万元）", fontsize=11, color="red")
        ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.2f}"))
        ax2.tick_params(axis="y", labelcolor="red")

        # 总金额里程碑 L 形标注（整百万倍虚线 / 整千万倍实线，连到 X/Y 轴）
        valid_m = [v for v in money_vals_m if not math.isnan(v)]
        if valid_m:
            lo, hi = min(valid_m), max(valid_m)
            span = max(hi - lo, 1e-9)
            # 固定右轴范围，避免里程碑竖线拉到 y=0 导致数据区间被整体缩放
            ax2.set_ylim(lo - 0.05 * span, hi + 0.05 * span)

            def milestone_points(step):
                """返回 [(idx, 阈值)]：每个整 step 倍数的跨越点。
                判定：上一点 < k*step ≤ 当前点（相邻点比较，跳过 NaN），即"刚超过"的第一个点；
                倍数发生在数据范围之外（首点之前）时自然无命中。"""
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
            x_right = x_dates[-1] + 10 / 1440   # 与 xlim 右边界一致（横线连到右侧 Y 轴）
            for kind, marks in (("million", mil_marks), ("tenmillion", ten_marks)):
                style = MILESTONE_STYLE[kind]
                for idx, _threshold in marks:
                    x_i = x_dates[idx]
                    y_i = money_vals_m[idx]
                    # 竖线：里程碑点垂直向下连到 X 轴
                    ax2.plot([x_i, x_i], [y_bottom, y_i], **style, linewidth=1.0)
                    # 横线：里程碑点水平向右连到 Y 轴（右侧 spine）
                    ax2.plot([x_i, x_right], [y_i, y_i], **style, linewidth=1.0)

    ax1.yaxis.set_major_locator(mticker.MultipleLocator(2))     # 主刻度间距：1 万人
    ax1.yaxis.set_minor_locator(mticker.MultipleLocator(0.5))   # 次刻度间距：0.5 万人
    ax2.yaxis.set_major_locator(mticker.MultipleLocator(0.5))     # 主刻度间距：1 百万元
    ax2.yaxis.set_minor_locator(mticker.MultipleLocator(0.1))   # 次刻度间距：0.1 百万元

    ax1.set_xlabel("时间", fontsize=11)
    ax1.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    plt.xticks(rotation=30)

    # 时间轴范围：左边界固定为 2026-10-05 22:00 紧贴左侧，右侧留 10 分钟空白
    x_left = mdates.date2num(datetime(2026, 10, 5, 22, 0))
    x_right = x_dates[-1] + 10 / 1440  # 10 分钟转为 matplotlib 天数单位
    ax1.set_xlim(x_left, x_right)

    last_time = mdates.num2date(x_dates[-1])
    ax1.set_title(f"摩点众筹各档位支持人数堆积趋势(截至{last_time:%Y年%m月%d日 %H:%M})", fontsize=14, fontweight="bold")
    ax1.grid(True, linestyle="--", alpha=0.4)

    ax1.legend(loc="upper left")
    if "总金额" in header:
        ax2.legend(loc="center left")

    # 状态栏坐标显示：右下方默认显示 (时间, 已支持份数, 总金额)，普通数值形式
    if "总人数" in header and "总金额" in header:
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

    # 十字跟踪虚线 + 数据行提示框（纯 matplotlib 原生事件，替代 mplcursors）：
    # 以鼠标为交叉点画贯穿两轴的灰色虚线（每次事件实时读取当前 xlim/ylim，缩放/平移自动适配），
    # 提示框显示鼠标 x 位置最近数据行的全部数据（时间、总人数、总金额、各档位人数）
    cursor_lines = [ln for ln in (line_people, line_money) if ln is not None]
    if cursor_lines:
        import numpy as np

        # 两条贯穿坐标轴的虚线（初始隐藏）；画在 ax2（twinx 上层）上，天然高于 ax1 全部内容
        # （数据线、里程碑线、左轴 legend）；zorder=4：高于 ax2 数据线（默认 2）、低于 ax2 legend（默认 5）
        vline, = ax2.plot([], [], color="gray", linestyle="--", linewidth=1, zorder=4)
        hline, = ax2.plot([], [], color="gray", linestyle="--", linewidth=1, zorder=4)
        vline.set_visible(False)
        hline.set_visible(False)
        # legend 式数据提示框，跟随鼠标并带边缘翻转（zorder 同上，不遮挡 ax2 legend）
        tooltip = ax2.text(
            0, 0, "", fontsize=9, zorder=4, va="center", ha="left",
            bbox={"boxstyle": "round", "facecolor": "white", "edgecolor": "gray", "alpha": 0.9},
        )
        tooltip.set_visible(False)

        x_arr = np.asarray(x_dates)
        # blitting 双背景优化：静态重元素（面积图/数据线/里程碑/legend）只在重建时渲染一次。
        #   bg_clean —— 不含三件套的干净背景；bg_tt —— 干净背景 + tooltip 已印入。
        # 每次鼠标移动仅 restore(bg_tt) + 重绘两条十字线（实测 ≈0.4ms，60Hz 丝滑）；
        # tooltip 的多行 CJK 文本渲染实测 ≈27ms，故仅在数据行或对齐状态变化时才重建
        # bg_tt（并加 100ms 节流），避免逐帧卡顿。缩放/平移/resize 后标记脏，下次移动时重建。
        bg_clean = None
        bg_tt = None
        last_tt_key = None
        last_tt_time = 0.0
        TT_THROTTLE = 0.1   # tooltip 重建节流间隔（秒）

        # tooltip 锚点 y：金额线交点（无金额线时映射人数线交点到右轴坐标）
        if line_money is not None:
            anchor_y = np.asarray(money_vals_m)
        else:
            yp = np.asarray([v / 1e4 for v in col_values("总人数")])
            pix = ax1.transData.transform(np.column_stack([np.zeros_like(yp), yp]))[:, 1]
            anchor_y = ax2.transData.inverted().transform(np.column_stack([np.zeros_like(pix), pix]))[:, 1]

        def mark_dirty(event):
            """缩放/平移结束或窗口 resize 后背景失效，下次 mouse move 时重建"""
            nonlocal bg_clean, bg_tt
            bg_clean = bg_tt = None
        for sig in ("button_release_event", "scroll_event", "resize_event"):
            fig.canvas.mpl_connect(sig, mark_dirty)

        def rebuild_bgs():
            """全量渲染一次静态图，生成干净背景与含 tooltip 的背景快照"""
            nonlocal bg_clean, bg_tt
            vis = [(a, a.get_visible()) for a in (vline, hline, tooltip)]
            for a, _ in vis:
                a.set_visible(False)
            fig.canvas.draw()                       # 全量重绘（无三件套），≈200ms 一次性成本
            bg_clean = fig.canvas.copy_from_bbox(fig.bbox)
            if tooltip.get_text():
                tooltip.set_visible(True)
                ax2.draw_artist(tooltip)
            bg_tt = fig.canvas.copy_from_bbox(fig.bbox)
            for a, v in vis:
                a.set_visible(v)

        def apply_tooltip(i, on_left, va, dx):
            """把最近数据行全部数据写入 tooltip 并按锚点/对齐状态定位"""
            ts, row = parsed[i]
            parts = [ts.strftime("%Y-%m-%d %H:%M:%S")]
            for name, val in zip(header, row):
                if name == "timestamp":
                    continue
                parts.append(f"{name}: {val if val else '--'}")
            tooltip.set_text("\n".join(parts))
            tooltip.set_position((x_dates[i] + dx if on_left else x_dates[i] - dx, anchor_y[i]))
            tooltip.set_ha("left" if on_left else "right")
            tooltip.set_va(va)

        def on_move(event):
            """鼠标移动：更新十字虚线（每帧）与 tooltip（跨数据点时重建背景）"""
            nonlocal bg_tt, last_tt_key, last_tt_time
            # 注意：ax2 = twinx() 覆盖在 ax1 上层，绘图区内 event.inaxes 恒为 ax2
            if event.inaxes is None or event.xdata is None:
                if vline.get_visible():
                    for art in (vline, hline, tooltip):
                        art.set_visible(False)
                    if bg_clean is not None:
                        fig.canvas.restore_region(bg_clean)
                        fig.canvas.blit(fig.bbox)
                    else:
                        fig.canvas.draw_idle()
                return
            x, y = event.xdata, event.ydata   # inaxes 恒为 ax2，坐标即右轴数据坐标
            x0, x1_ = ax2.get_xlim()
            y0, y1_ = ax2.get_ylim()

            # 最近数据行 → tooltip 内容与锚点（吸附数据点，位置由 (idx, ha, va) 唯一决定）
            i = int(np.abs(x_arr - x).argmin())
            dx = 0.02 * (x1_ - x0)
            on_left = x_dates[i] < x0 + 0.65 * (x1_ - x0)
            ty = anchor_y[i]
            frac = (ty - y0) / (y1_ - y0) if y1_ > y0 else 0.5
            va = "top" if frac > 0.75 else "bottom" if frac < 0.25 else "center"
            key = (i, on_left, va)

            if bg_clean is None or bg_tt is None:
                # 背景失效（首次/缩放/resize）：设置 tooltip 并整体重建
                apply_tooltip(i, on_left, va, dx)
                tooltip.set_visible(True)
                rebuild_bgs()
                last_tt_key, last_tt_time = key, time.monotonic()
            elif key != last_tt_key and time.monotonic() - last_tt_time >= TT_THROTTLE:
                # 跨数据点或翻转状态变化：更新 tooltip 并重印入背景（节流内跳过，下帧再试）
                apply_tooltip(i, on_left, va, dx)
                fig.canvas.restore_region(bg_clean)
                tooltip.set_visible(True)
                ax2.draw_artist(tooltip)
                bg_tt = fig.canvas.copy_from_bbox(fig.bbox)
                last_tt_key, last_tt_time = key, time.monotonic()

            # 每帧快速路径：恢复含 tooltip 的背景 + 仅重绘两条十字线
            vline.set_data([x, x], (y0, y1_))
            hline.set_data((x0, x1_), [y, y])
            vline.set_visible(True)
            hline.set_visible(True)
            fig.canvas.restore_region(bg_tt)
            ax2.draw_artist(vline)
            ax2.draw_artist(hline)
            fig.canvas.blit(fig.bbox)

        fig.canvas.mpl_connect("motion_notify_event", on_move)

    plt.show()


def run_forever(interval):
    """按固定间隔循环抓取并记录数据，直到手动中断"""
    logger.info("开始循环抓取，间隔 %d 秒（按 Ctrl+C 退出）", interval)
    # 内嵌每日 git 同步线程：随本进程一同启停（daemon），无需单独驱动 sync 脚本
    threading.Thread(
        target=sync_data.sync_loop, name="git-sync", daemon=True
    ).start()
    logger.info("每日 git 同步线程已启动（12:00/22:00 add/commit/push，按时间表自动重试）")
    while True:
        start = time.monotonic()
        try:
            tier_data, total_count, total_money = fetch_tier_data()
            append_to_csv(tier_data, total_count, total_money)
        except Exception:
            logger.exception("本轮抓取失败，将在 %d 秒后重试", interval)

        elapsed = time.monotonic() - start
        time.sleep(max(0, interval - elapsed))


def main():
    parser = argparse.ArgumentParser(description="摩点众筹档位数据监控与可视化工具")
    parser.add_argument(
        "--plot",
        "-p",
        action="store_true",
        help="绘制折线图（不进入循环抓取）",
    )
    parser.add_argument(
        "--interval",
        "-i",
        type=int,
        default=60,
        help="循环抓取间隔秒数（默认 60）",
    )

    args = parser.parse_args()

    if args.plot:
        plot_trends()
        return

    if args.interval < 1:
        parser.error("--interval 必须为正整数秒")

    try:
        run_forever(args.interval)
    except KeyboardInterrupt:
        logger.info("已手动停止循环抓取。")


if __name__ == "__main__":
    main()