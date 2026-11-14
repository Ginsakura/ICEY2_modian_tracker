# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "beautifulsoup4>=4.15.0",
#     "matplotlib>=3.11.2",
#     "requests>=2.34.2",
# ]
# ///
"""摩点众筹追踪器：配置驱动的多项目采集 + 单项目图表入口。

项目、追踪链接、数据文件名、采集窗口与图表元数据全部来自 trackers.json；
本模块不再提供旧版单项目/固定 CSV 入口。
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import re
import signal
import sys
import threading
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Iterable

import requests
from bs4 import BeautifulSoup

import sync_data
import tracker_config
from shared_logger import get_logger
from tier_columns import (
    FREE_COLUMN,
    Tier,
    canonical_header,
    format_tier_name,
    parse_price,
    resolve_columns,
    strip_lv_prefix,
)

logger = get_logger("modian_tracker")

ROOT = Path(__file__).resolve().parent
PID_FILE = ROOT / "run.pid"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# ---------------------------------------------------------------- 解析

def parse_tiers(html: str) -> tuple[list[Tier], int, float]:
    """解析页面 HTML，按页面顺序返回 (Tier 列表, 总人数, 总金额)。"""
    soup = BeautifulSoup(html, "html.parser")
    tiers: list[Tier] = []
    for em in soup.select(".back-list em.count"):
        card = em.find_parent("div", class_="back-list")
        if card is None:
            continue
        card_text = card.get_text(" ", strip=True)
        count = None
        if em.get("i"):
            try:
                count = int(str(em["i"]))
            except ValueError:
                count = None
        if count is None:
            match = re.search(r"(\d+)\s*份", em.get_text())
            if match:
                count = int(match.group(1))
        if count is None:
            continue

        if "无偿支持" in card_text:
            tiers.append(Tier(None, None, FREE_COLUMN, count, True))
            continue

        price_match = re.search(r"[¥￥]\s*([\d\.]+)", card_text)
        if not price_match:
            continue
        price_text = price_match.group(1)
        try:
            price_value = Decimal(price_text)
        except Exception:  # noqa: BLE001 页面脏数据不阻断其他档位
            continue
        sub = card.select_one(".back-sub-title")
        title = strip_lv_prefix(sub.get_text(" ", strip=True)) if sub else ""
        tiers.append(Tier(price_text, price_value, title, count, False))

    total_count = 0
    total_money = 0.0
    count_el = soup.select_one("span[backer_count]")
    money_el = soup.select_one("span[backer_money]")
    if count_el:
        try:
            total_count = int(count_el.get_text().replace(",", "").strip())
        except ValueError:
            total_count = 0
    if money_el:
        try:
            total_money = float(money_el.get_text().replace(",", "").strip())
        except ValueError:
            total_money = 0.0
    return tiers, total_count, total_money


def fetch_tiers(url: str) -> tuple[list[Tier], int, float]:
    """抓取指定摩点页面并解析；非 200 或解析失败时抛异常。"""
    target = url
    resp = requests.get(target, headers=HEADERS, timeout=15)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {target}")
    resp.encoding = "utf-8"
    tiers, total_count, total_money = parse_tiers(resp.text)
    if not tiers:
        raise RuntimeError(f"未解析到任何档位: {target}")
    return tiers, total_count, total_money


# ---------------------------------------------------------------- CSV 基础

def _clean_cell(value):
    """去除人数类数值末尾的 .0（如 '4647.0' -> '4647'）；空值保持为空"""
    if value is None:
        return ""
    s = str(value).strip()
    return s[:-2] if s.endswith(".0") else s


_BOM: bytes = b"\xef\xbb\xbf"


def _check_file_format(path: Path) -> tuple[list[str] | None, str]:
    """检查能否直接追加，返回 (表头, 状态)。

    状态: missing 文件不存在 / empty 空文件 / no_bom 缺 BOM /
          partial 末行不完整 / crlf 行尾不是 CRLF / ok 可直接追加。
    """
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


def _truncate_partial_line(path: Path) -> bool:
    """末尾存在崩溃残留的半行时，截断到最后一个完整行；返回是否发生截断"""
    new_size = 0
    try:
        size = os.path.getsize(path)
        if size == 0:
            return False
        with open(path, "rb+") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) == b"\n":
                return False
            pos = size
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


def _format_tier_row(header, mapping, total_count, total_money):
    """按表头列序构造新格式一行；mapping 为 [(Tier, 列名)]。"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [""] * len(header)
    index = {col: i for i, col in enumerate(header)}
    for col, value in (("timestamp", now_str),
                       ("总人数", str(total_count) if total_count else ""),
                       ("总金额", str(total_money) if total_money else "")):
        if col in index:
            row[index[col]] = value
    for tier, col in mapping:
        if col in index:
            row[index[col]] = _clean_cell(tier.count)
    return row


def _render_line(row):
    """把一行渲染成 CSV 文本（csv.writer 默认 CRLF 行尾），供二进制追加"""
    buf = io.StringIO()
    csv.writer(buf).writerow(row)
    return buf.getvalue()


def _write_csv_atomic(rows, money_idx, path: Path) -> None:
    """全量写入同目录临时文件后原子替换，避免写到一半损坏原文件"""
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


def _read_rows(path):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.reader(f) if r]


# ---------------------------------------------------------------- 追加写入

def _rewrite_tiers(tiers: Iterable[Tier], total_count: int, total_money: float, path: Path,
                   project_name: str | None = None) -> None:
    tiers = list(tiers)
    prefix = f"项目 {project_name}: " if project_name else ""
    rows = _read_rows(path)
    if rows:
        mapping, header, _changed = resolve_columns(rows[0], tiers)
    else:
        header = canonical_header(tiers)
        mapping = []
        paid_index = 0
        for tier in tiers:
            if tier.free:
                mapping.append((tier, FREE_COLUMN))
            else:
                paid_index += 1
                mapping.append((tier, format_tier_name(paid_index, tier)))
        rows = [header]
    rows[0] = header
    for old in rows[1:]:
        if len(old) < len(header):
            old.extend([""] * (len(header) - len(old)))
    rows.append(_format_tier_row(header, mapping, total_count, total_money))
    money_idx = header.index("总金额") if "总金额" in header else -1
    _write_csv_atomic(rows, money_idx, path)
    logger.info("%s已重写 %s（%d 列，%d 行数据）", prefix, path, len(header), len(rows) - 1)


def append_tiers(tiers, total_count, total_money, path, project_name: str | None = None) -> bool:
    """按价格身份追加一行到项目 CSV；返回是否成功写入。"""
    tiers = list(tiers)
    if not tiers:
        raise ValueError("未解析到任何档位数据")
    path = Path(path)
    prefix = f"项目 {project_name}: " if project_name else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    header, state = _check_file_format(path)
    if state == "partial":
        _truncate_partial_line(path)
        header, state = _check_file_format(path)
    if state != "ok" or header is None:
        _rewrite_tiers(tiers, total_count, total_money, path, project_name)
        return True

    mapping, new_header, changed = resolve_columns(header, tiers)
    if changed:
        _rewrite_tiers(tiers, total_count, total_money, path, project_name)
        return True

    line = _render_line(_format_tier_row(header, mapping, total_count, total_money))
    with open(path, "ab") as f:
        f.write(line.encode("utf-8"))
    logger.info("%s成功记录 %d 个档位数据至 %s", prefix, len(mapping), path)
    return True


# ---------------------------------------------------------------- 采集

class Collector(threading.Thread):
    """单个项目的采集线程；窗口结束或 stop 置位时退出。"""

    def __init__(self, project: tracker_config.ProjectConfig, stop: threading.Event):
        super().__init__(name=f"collector:{project.name}")
        self.project = project
        self.stop = stop
        self.log = get_logger(f"collector:{project.name}")

    def _wait_until(self, target: datetime) -> bool:
        while not self.stop.is_set():
            remain = (target - datetime.now()).total_seconds()
            if remain <= 0:
                return True
            self.stop.wait(min(60.0, remain))
        return False

    def run(self) -> None:
        project = self.project
        now = datetime.now()
        if project.collect_until is not None and now >= project.collect_until:
            self.log.info("项目 %s 启动时已过采集窗口 %s，跳过", project.name, project.collect_until)
            return
        if project.collect_from is not None and now < project.collect_from:
            self.log.info("项目 %s 等待采集起点 %s", project.name, project.collect_from)
            if not self._wait_until(project.collect_from):
                return
            if project.collect_until is not None and datetime.now() >= project.collect_until:
                self.log.info("项目 %s 到达起点时窗口已结束，跳过", project.name)
                return

        self.log.info("项目 %s 开始采集：%s -> %s，间隔 %d 秒",
                      project.name, project.url, project.data_file, project.interval)
        while not self.stop.is_set():
            now = datetime.now()
            if project.collect_until is not None and now >= project.collect_until:
                self.log.info("项目 %s 已到采集终点 %s，线程结束", project.name, project.collect_until)
                break
            started = time.monotonic()
            try:
                tiers, total_count, total_money = fetch_tiers(project.url)
                if project.collect_until is not None and datetime.now() >= project.collect_until:
                    self.log.info("项目 %s 抓取完成时已过采集终点 %s，本轮不写入",
                                  project.name, project.collect_until)
                    break
                append_tiers(tiers, total_count, total_money, project.data_path, project.name)
                self.log.info("项目 %s 采集成功：%d 档，总人数 %d，总金额 %.2f",
                              project.name, len(tiers), total_count, total_money)
            except Exception:  # noqa: BLE001 单项目失败不终止其他项目
                self.log.exception("项目 %s 本轮抓取失败，将在 %d 秒后重试",
                                   project.name, project.interval)

            next_at = started + project.interval
            if project.collect_until is not None:
                wait_sec = max(0.0, next_at - time.monotonic())
                if datetime.now() + timedelta(seconds=wait_sec) >= project.collect_until:
                    self.log.info("项目 %s 下一次采样将越过终点，线程结束", project.name)
                    break
            self.stop.wait(max(0.0, next_at - time.monotonic()))


def run_once(config: tracker_config.TrackerConfig,
             project_name: str | None = None) -> int:
    """逐项目抓取一轮；窗口外 skipped，不写 run.pid、不启 sync。"""
    results: list[tuple[str, str, int, float]] = []
    failed = False
    if project_name is not None:
        project = config.project(project_name)
        if not project.enabled:
            raise tracker_config.ConfigError(f"项目 {project.name} 未启用，无法单轮采集")
        projects = [project]
    else:
        projects = config.enabled_projects()
    for project in projects:
        now = datetime.now()
        if project.collect_until is not None and now >= project.collect_until:
            results.append((project.name, "skipped", 0, 0.0))
            continue
        if project.collect_from is not None and now < project.collect_from:
            results.append((project.name, "skipped", 0, 0.0))
            continue
        try:
            tiers, total_count, total_money = fetch_tiers(project.url)
            if project.collect_until is not None and datetime.now() >= project.collect_until:
                logger.info("项目 %s 抓取完成时已过采集终点 %s，本轮不写入",
                            project.name, project.collect_until)
                results.append((project.name, "skipped", 0, 0.0))
                continue
            append_tiers(tiers, total_count, total_money, project.data_path, project.name)
            results.append((project.name, "ok", total_count, total_money))
        except Exception as exc:  # noqa: BLE001
            logger.exception("项目 %s 单轮抓取失败", project.name)
            print(f"{project.name}\t失败\t{exc}", file=sys.stderr)
            results.append((project.name, "失败", 0, 0.0))
            failed = True

    for name, status, count, money in results:
        if status == "skipped":
            print(f"{name}\tskipped\t窗口外")
        elif status == "ok":
            print(f"{name}\tok\t总人数 {count}\t总金额 {money:.2f}")
    return 1 if failed else 0


def _install_signal_handlers(stop: threading.Event) -> None:
    def handler(signum, _frame):
        logger.info("收到信号 %s，完成当前轮后退出", signum)
        stop.set()

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass


def run_collector(config: tracker_config.TrackerConfig) -> int:
    """单进程多线程采集；所有采集线程结束后删除 run.pid 并退出。"""
    projects = config.enabled_projects()
    if not projects:
        logger.warning("配置中没有启用的项目")
        return 0

    try:
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        logger.exception("写入 run.pid 失败: %s", PID_FILE)
        return 1

    stop = threading.Event()
    _install_signal_handlers(stop)
    sync_thread = threading.Thread(target=sync_data.sync_loop, name="git-sync", daemon=True)
    sync_thread.start()
    logger.info("每日 git 同步线程已启动（项目数 %d）", len(projects))

    threads = [Collector(project, stop) for project in projects]
    for thread in threads:
        thread.start()
    try:
        for thread in threads:
            thread.join()
    finally:
        stop.set()
        try:
            PID_FILE.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            logger.exception("删除 run.pid 失败: %s", PID_FILE)
    logger.info("所有采集线程已结束，进程退出")
    return 0


# ---------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="摩点众筹多项目追踪器")
    parser.add_argument("--config", default=str(tracker_config.CONFIG_PATH),
                        help="配置文件路径（默认仓库根 trackers.json）")
    parser.add_argument("--plot", "-p", action="store_true",
                        help="绘制折线图（不进入循环抓取）")
    parser.add_argument("--analyze", "-a", action="store_true",
                        help="打开交互式数据分析窗口（不进入循环抓取）")
    parser.add_argument("--project", help="图表或单轮采集使用的项目名")
    parser.add_argument("--once", action="store_true",
                        help="按采集窗口逐项目抓取一轮后退出")
    return parser


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def main() -> int:
    _utf8_stdio()
    parser = build_parser()
    args = parser.parse_args()
    try:
        config = tracker_config.load_config(args.config)
    except tracker_config.ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2

    if args.plot:
        import charts
        charts.PlotChart(project=args.project, config_path=args.config).plot()
        return 0
    if args.analyze:
        import charts
        try:
            charts.AnalyzeChart(project=args.project, config_path=args.config).show()
        except RuntimeError as exc:
            print(exc)
            return 1
        return 0
    if args.once:
        try:
            return run_once(config, args.project)
        except tracker_config.ConfigError as exc:
            print(f"配置错误: {exc}", file=sys.stderr)
            return 2
    return run_collector(config)


if __name__ == "__main__":
    raise SystemExit(main())
