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


def fetch_tier_data():
    """抓取页面中各档位价格及支持人数"""
    resp = requests.get(URL, headers=HEADERS, timeout=15)
    resp.encoding = "utf-8"

    soup = BeautifulSoup(resp.text, "html.parser")
    tier_dict = {}

    # 定位包含“已支持 ... 份”的文本节点
    support_elements = soup.find_all(string=re.compile(r"已支持\s*\d+\s*份"))

    level = 1
    for elem in support_elements:
        card = elem.parent
        # 向上回溯至包含档位金额与支持数的共同卡片容器
        for _ in range(4):
            if card and re.search(r"[¥￥]\s*[\d\.]+", card.get_text()):
                break
            if card.parent:
                card = card.parent

        card_text = card.get_text()
        price_match = re.search(r"[¥￥]\s*([\d\.]+)", card_text)
        count_match = re.search(r"已支持\s*(\d+)\s*份", card_text)

        if price_match and count_match:
            price = price_match.group(1)
            count = int(count_match.group(1))
            # 按出现顺序编号为 Lv1、Lv2 ...
            key = f"Lv{level}（￥{price}）"
            tier_dict[key] = count
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

    timestamps = [item[0] for item in parsed]
    tier_columns = [col for col in header if col not in ("timestamp", "总人数", "总金额")]

    plt.figure(figsize=(12, 6))
    for col in tier_columns:
        idx = header.index(col)
        values = []
        for _, r in parsed:
            try:
                values.append(float(r[idx]) if r[idx] else float("nan"))
            except (ValueError, IndexError):
                values.append(float("nan"))
        plt.plot(timestamps, values, marker="o", markersize=3, label=f"档位 {col}")

    ax = plt.gca()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    plt.xticks(rotation=30)

    plt.title("摩点众筹各档位购买人数趋势", fontsize=14, fontweight="bold")
    plt.xlabel("时间", fontsize=11)
    plt.ylabel("已支持份数", fontsize=11)
    plt.grid(True, linestyle="--", alpha=0.6)
    plt.legend(bbox_to_anchor=(1.02, 1), loc="upper left", borderaxespad=0.0)

    plt.tight_layout()
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