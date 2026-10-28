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
import io
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

_BOM: bytes = b"\xef\xbb\xbf"


def _expected_header(data):
    """本次采样应有的表头: 固定三列 + 页面返回的档位列（保持页面顺序）"""
    return ["timestamp", "总人数", "总金额", *data.keys()]


def _read_header(path=None):
    """只读取 CSV 首行并解析为表头列表；文件缺失、为空或缺 BOM 时返回 None"""
    path = path or CSV_FILE
    try:
        with open(path, "rb", buffering=0) as f:
            first = f.readline()
    except OSError:
        return None
    if not first or not first.startswith(_BOM):
        return None
    try:
        return next(csv.reader([first.decode("utf-8-sig").rstrip("\r\n")]), None)
    except (UnicodeDecodeError, csv.Error):
        return None


def _check_file_format(path=None):
    """检查能否直接追加，返回 (表头, 状态)。

    状态: missing 文件不存在 / empty 空文件 / no_bom 缺 BOM /
          partial 末行不完整 / crlf 行尾不是 CRLF / ok 可直接追加。
    常规追加只读取表头行与文件末尾 2 字节，不触碰历史数据行。
    """
    path = path or CSV_FILE
    if not os.path.exists(path):
        return None, "missing"
    try:
        with open(path, "rb", buffering=0) as f:
            first = f.readline()
            if not first:
                return None, "empty"
            if not first.startswith(_BOM):
                return None, "no_bom"
            size = os.fstat(f.fileno()).st_size
            if size < 2:
                return None, "partial"
            f.seek(-2, os.SEEK_END)
            tail = f.read(2)
    except OSError:
        return None, "missing"
    try:
        header = next(csv.reader([first.decode("utf-8-sig").rstrip("\r\n")]), None)
    except (UnicodeDecodeError, csv.Error):
        return None, "no_bom"
    if not header:
        return None, "empty"
    if tail[1:] != b"\n":
        return None, "partial"
    if tail[0:1] != b"\r":
        return None, "crlf"
    return header, "ok"


def _truncate_partial_line(path=None):
    """末尾存在崩溃残留的半行时，截断到最后一个完整行；返回是否发生截断"""
    path = path or CSV_FILE
    try:
        size = os.path.getsize(path)
        if size == 0:
            return False
        with open(path, "rb+") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) == b"\n":
                return False
            pos = size
            new_size = 0
            while pos > 0:
                step = min(8192, pos)
                pos -= step
                f.seek(pos)
                idx = f.read(step).rfind(b"\n")
                if idx != -1:
                    new_size = pos + idx + 1
                    break
            f.truncate(new_size)
    except OSError:
        logger.exception("修复 %s 的不完整末行失败", path)
        return False
    logger.warning("检测到 %s 末尾存在不完整行，已截断到 %d 字节", path, new_size)
    return True


def _format_row(header, data, total_count, total_money):
    """按表头列序构造一行；未解析到的值写空字符串，列位不移动"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [""] * len(header)
    for i, col in enumerate(header):
        if col == "timestamp":
            row[i] = now_str
        elif col == "总人数":
            row[i] = str(total_count) if total_count else ""
        elif col == "总金额":
            row[i] = str(total_money) if total_money else ""
        elif col in data:
            row[i] = _clean_cell(data[col])
    return row


def _render_line(row):
    """把一行渲染成 CSV 文本（csv.writer 默认 CRLF 行尾），供二进制追加"""
    buf = io.StringIO()
    csv.writer(buf).writerow(row)
    return buf.getvalue()


def _write_csv_atomic(rows, money_idx, path=None):
    """全量写入同目录临时文件后原子替换，避免写到一半损坏原文件"""
    path = path or CSV_FILE
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            for row in rows:
                cleaned = [
                    "" if cell is None else (cell if i == 0 or i == money_idx else _clean_cell(cell))
                    for i, cell in enumerate(row)
                ]
                writer.writerow(cleaned)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            logger.exception("清理临时文件 %s 失败", tmp)
        raise


def _rewrite_csv(data, total_count, total_money):
    """表头演进或文件格式异常时的一次性全量重写（原子替换）"""
    rows = []
    if os.path.exists(CSV_FILE) and os.path.getsize(CSV_FILE) > 0:
        with open(CSV_FILE, "r", encoding="utf-8-sig", newline="") as f:
            rows = [r for r in csv.reader(f) if r]
    if rows:
        header = rows[0]
        for col in data:
            if col not in header:
                header.append(col)
    else:
        header = _expected_header(data)
        rows = [header]
    # 新增档位列后，历史行一律补齐到表头宽度，列位不移动
    for old in rows[1:]:
        if len(old) < len(header):
            old.extend([""] * (len(header) - len(old)))
    rows.append(_format_row(header, data, total_count, total_money))
    money_idx = header.index("总金额") if "总金额" in header else -1
    _write_csv_atomic(rows, money_idx)
    logger.info("已重写 %s（%d 列，%d 行数据）", CSV_FILE, len(header), len(rows) - 1)


def append_to_csv(data, total_count, total_money):
    """带时间戳追加到 CSV 文件。

    常规路径只读表头并单行追加，写入成本与历史规模无关；仅在文件缺失、
    表头缺列或编码/行尾异常时回退到一次性全量重写。
    """
    if not data:
        logger.warning("未抓取到有效档位数据，请确认页面是否正常。")
        return

    header, state = _check_file_format()
    if state == "partial":
        _truncate_partial_line()
        header, state = _check_file_format()

    if state != "ok" or header is None or any(col not in header for col in data):
        _rewrite_csv(data, total_count, total_money)
        return

    line = _render_line(_format_row(header, data, total_count, total_money))
    with open(CSV_FILE, "ab") as f:
        f.write(line.encode("utf-8"))
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