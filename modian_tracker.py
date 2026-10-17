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

def parse_tier_data(html):
    """解析页面 HTML，返回 (档位字典, 总人数, 总金额)"""
    soup = BeautifulSoup(html, "html.parser")
    tier_dict = {}

    level = 1
    # 仅选取档位卡片内的计数元素，按页面顺序排列（首个为“无偿支持”）
    for em in soup.select(".back-list em.count"):
        card = em.find_parent("div", class_="back-list")
        if card is None:
            continue
        card_text = card.get_text()

        # 优先使用 i 属性（售罄档位文本为“已抢光／限 X 份”，仍可从 i 取得支持数）
        count = None
        if em.get("i"):
            count = int(str(em["i"]))
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
        "--analyze",
        "-a",
        action="store_true",
        help="打开交互式数据分析窗口（不进入循环抓取）",
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
        # 仅绘图/分析分支按需导入，保证采集路径（无参运行）不加载 matplotlib
        import charts
        charts.PlotChart().plot()
        return

    if args.analyze:
        import charts
        try:
            charts.AnalyzeChart().show()
        except RuntimeError as exc:
            print(exc)
        return

    if args.interval < 1:
        parser.error("--interval 必须为正整数秒")

    try:
        run_forever(args.interval)
    except KeyboardInterrupt:
        logger.info("已手动停止循环抓取。")


if __name__ == "__main__":
    main()