# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "beautifulsoup4>=4.15.0",
#     "matplotlib>=3.11.2",
#     "pandas>=3.0.6",
#     "requests>=2.34.2",
# ]
# ///
import argparse
import os
import re
from datetime import datetime
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
import requests
from bs4 import BeautifulSoup

URL = "https://zhongchou.modian.com/item/160465.html"
CSV_FILE = "modian_backers.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def fetch_tier_data():
    """抓取页面中各档位价格及支持人数"""
    resp = requests.get(URL, headers=HEADERS, timeout=15)
    resp.encoding = "utf-8"

    soup = BeautifulSoup(resp.text, "html.parser")
    tier_dict = {}

    # 定位包含“已支持 ... 份”的文本节点
    support_elements = soup.find_all(text=re.compile(r"已支持\s*\d+\s*份"))

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


def append_to_csv(data, total_count, total_money):
    """带时间戳追加到 CSV 文件"""
    if not data:
        print(f"[{datetime.now()}] 未抓取到有效档位数据，请确认页面是否正常。")
        return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = {"timestamp": now_str, "总人数": total_count, "总金额": total_money, **data}
    new_df = pd.DataFrame([row])

    if os.path.exists(CSV_FILE):
        existing_df = pd.read_csv(CSV_FILE)
        # 合并历史数据与新列（宽表对齐）
        combined_df = pd.concat([existing_df, new_df], ignore_index=True)
        combined_df.to_csv(CSV_FILE, index=False, encoding="utf-8-sig")
    else:
        new_df.to_csv(CSV_FILE, index=False, encoding="utf-8-sig")

    print(f"[{now_str}] 成功记录 {len(data)} 个档位数据至 {CSV_FILE}")


def plot_trends():
    """读取 CSV 数据并生成折线图"""
    if not os.path.exists(CSV_FILE):
        print(f"未找到数据文件: {CSV_FILE}，请先执行记录。")
        return

    df = pd.read_csv(CSV_FILE)
    if df.empty or "timestamp" not in df.columns:
        print("CSV 文件为空或未包含有效时间戳列。")
        return

    # 中文字体设置
    plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "PingFang SC", "sans-serif"]
    plt.rcParams["axes.unicode_minus"] = False

    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values(by="timestamp")

    tier_columns = [col for col in df.columns if col not in ("timestamp", "总人数", "总金额")]

    plt.figure(figsize=(12, 6))
    for col in tier_columns:
        plt.plot(
            df["timestamp"],
            df[col],
            marker="o",
            markersize=3,
            label=f"档位 {col}",
        )

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


def main():
    parser = argparse.ArgumentParser(description="摩点众筹档位数据监控与可视化工具")
    parser.add_argument(
        "--plot",
        "-p",
        action="store_true",
        help="绘制折线图（默认不指定时为抓取并保存数据）",
    )

    args = parser.parse_args()

    if args.plot:
        plot_trends()
    else:
        tier_data, total_count, total_money = fetch_tier_data()
        append_to_csv(tier_data, total_count, total_money)


if __name__ == "__main__":
    main()