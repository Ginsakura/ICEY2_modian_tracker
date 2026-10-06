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
import logging
import os
import re
import time
from datetime import datetime
import requests
from bs4 import BeautifulSoup

URL = "https://zhongchou.modian.com/item/160465.html"
CSV_FILE = "modian_backers.csv"
LOG_FILE = "modian_tracker.log"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def setup_logging():
    """配置日志：同时输出到控制台与日志文件"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


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
        logging.warning("未抓取到有效档位数据，请确认页面是否正常。")
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
    logging.info("成功记录 %d 个档位数据至 %s", len(data), CSV_FILE)


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

    # 左轴：总人数折线（单位万人）
    if "总人数" in header:
        ax1.plot(
            x_dates, [v / 1e4 for v in col_values("总人数")],
            color="black", linewidth=1.5, marker="o", markersize=3, label=legend_name("总人数"),
        )

    # 右轴：总金额折线（单位百万元）
    ax2 = ax1.twinx()
    if "总金额" in header:
        ax2.plot(
            x_dates, [v / 1e6 for v in col_values("总金额")],
            color="red", linewidth=1.5, marker="s", markersize=3, label=legend_name("总金额"),
        )
        ax2.set_ylabel("总金额（百万元）", fontsize=11, color="red")
        ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.2f}"))
        ax2.tick_params(axis="y", labelcolor="red")

    ax1.yaxis.set_major_locator(mticker.MultipleLocator(1))     # 主刻度间距：1 万人
    ax1.yaxis.set_minor_locator(mticker.MultipleLocator(0.5))   # 次刻度间距：0.5 万人
    ax2.yaxis.set_major_locator(mticker.MultipleLocator(0.1))     # 主刻度间距：1 百万元
    # ax2.yaxis.set_minor_locator(mticker.MultipleLocator(0.1))   # 次刻度间距：0.1 百万元

    ax1.set_xlabel("时间", fontsize=11)
    ax1.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    plt.xticks(rotation=30)

    ax1.set_title("摩点众筹各档位支持人数堆积趋势", fontsize=14, fontweight="bold")
    ax1.grid(True, linestyle="--", alpha=0.4)

    ax1.legend(loc="best")
    if "总金额" in header:
        ax2.legend(loc="center right")

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

    plt.show()


def run_forever(interval):
    """按固定间隔循环抓取并记录数据，直到手动中断"""
    logging.info("开始循环抓取，间隔 %d 秒（按 Ctrl+C 退出）", interval)
    while True:
        start = time.monotonic()
        try:
            tier_data, total_count, total_money = fetch_tier_data()
            append_to_csv(tier_data, total_count, total_money)
        except Exception:
            logging.exception("本轮抓取失败，将在 %d 秒后重试", interval)

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

    setup_logging()
    try:
        run_forever(args.interval)
    except KeyboardInterrupt:
        logging.info("已手动停止循环抓取。")


if __name__ == "__main__":
    main()