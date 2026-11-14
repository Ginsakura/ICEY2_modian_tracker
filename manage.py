#!/usr/bin/env python3
"""trackers.json 的交互式与非交互式配置工具。

无子命令时进入菜单；子命令：list / check / preview / add / disable。
预览使用与采集端相同的 fetch_tiers/format_tier_name，保存前必须成功抓取一次。
"""
from __future__ import annotations

import argparse
import copy
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import shared_logger

# 仅供测试/特殊环境覆盖；正常运行时仍写仓库根 tracker_log.log
if os.environ.get("TRACKER_LOG_PATH"):
    shared_logger.LOG_PATH = Path(os.environ["TRACKER_LOG_PATH"]).resolve()

import modian_tracker
import tier_columns
import tracker_config

CONFIG_PATH = tracker_config.CONFIG_PATH
DEFAULT_INTERVAL = 60


@dataclass(frozen=True)
class Preview:
    url: str
    timestamp: str
    total_count: int
    total_money: float
    columns: list[tuple[str, int]]


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def _load_config(path: str | Path | None) -> tracker_config.TrackerConfig:
    return tracker_config.load_config(path)


def _load_raw(path: str | Path | None) -> dict[str, Any]:
    raw = tracker_config.load_raw(path)
    if not isinstance(raw, dict):
        raise tracker_config.ConfigError("trackers.json 顶层必须是对象")
    return raw


def preview_url(url: str) -> Preview:
    """抓取并解析一次目标页面；失败时抛出异常，不写任何文件。"""
    tiers, total_count, total_money = modian_tracker.fetch_tiers(url)
    paid = 0
    columns: list[tuple[str, int]] = []
    for tier in tiers:
        if tier.free:
            columns.append((tier_columns.FREE_COLUMN, tier.count))
        else:
            paid += 1
            columns.append((tier_columns.format_tier_name(paid, tier), tier.count))
    return Preview(
        url=url,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        total_count=total_count,
        total_money=total_money,
        columns=columns,
    )


def print_preview(preview: Preview) -> None:
    print(f"预览时间: {preview.timestamp}")
    print(f"追踪链接: {preview.url}")
    print(f"总人数: {preview.total_count}    总金额: {preview.total_money:,.2f} 元"
          f"    档位数量: {len(preview.columns)}")
    print("将写入的档位列名:")
    for name, count in preview.columns:
        print(f"  {name}    份数: {count}")


def _entry_interval(raw: dict[str, Any], name: str) -> int:
    projects = raw.get("projects")
    if isinstance(projects, dict) and isinstance(projects.get(name), dict):
        value = projects[name].get("interval")
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    defaults = raw.get("defaults")
    if isinstance(defaults, dict) and isinstance(defaults.get("interval"), int):
        return int(defaults["interval"])
    return DEFAULT_INTERVAL


def _validate_candidate(raw: dict[str, Any], path: str | Path | None) -> tracker_config.TrackerConfig:
    return tracker_config.validate_config(raw, path)


def _find_raw_name(raw: dict[str, Any], wanted: str) -> str:
    projects = raw.get("projects")
    if not isinstance(projects, dict):
        raise tracker_config.ConfigError("projects 必须是非空对象")
    if wanted in projects:
        return wanted
    matches = [name for name in projects if str(name).casefold() == wanted.casefold()]
    if len(matches) == 1:
        return matches[0]
    raise tracker_config.ConfigError(f"配置中没有项目: {wanted}")


def _ask(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def _ask_int(label: str, default: int) -> int:
    while True:
        text = _ask(label, str(default))
        try:
            return int(text)
        except ValueError:
            print("请输入整数。")


def _confirm() -> bool:
    return input("确认写入 trackers.json? [y/N]: ").strip().lower() in ("y", "yes")


def _choose_project(config: tracker_config.TrackerConfig, prompt: str = "选择项目") -> str:
    if not config.order:
        raise tracker_config.ConfigError("配置中没有项目")
    print("可选项目:")
    for i, name in enumerate(config.order, 1):
        project = config.projects[name]
        state = "启用" if project.enabled else "停用"
        print(f"  {i}. {name}  ({state}, {project.data_file})")
    while True:
        text = input(f"{prompt}（序号或名称）: ").strip()
        if text.isdigit():
            index = int(text)
            if 1 <= index <= len(config.order):
                return config.order[index - 1]
        elif text:
            try:
                return config.project(text).name
            except tracker_config.ConfigError as exc:
                print(exc)
        print("请输入有效的序号或项目名。")


def cmd_list(path: str | Path | None) -> int:
    config = _load_config(path)
    print(f"配置文件: {config.path}")
    print(f"默认项目: {config.default_project_name}    项目数: {len(config.order)}")
    for name in config.order:
        project = config.projects[name]
        state = "启用" if project.enabled else "停用"
        window = []
        if project.collect_from:
            window.append(f"从 {project.collect_from:%Y-%m-%d %H:%M}")
        if project.collect_until:
            window.append(f"到 {project.collect_until:%Y-%m-%d %H:%M}")
        window_text = "，".join(window) if window else "不限"
        campaign = project.campaign
        campaign_bits = []
        if campaign.deadline:
            campaign_bits.append(f"截止 {campaign.deadline:%Y-%m-%d %H:%M}")
        if campaign.phases:
            campaign_bits.append(f"阶段 {len(campaign.phases)} 段")
        if campaign.milestone_step:
            campaign_bits.append(f"步长 {campaign.milestone_step}")
        campaign_text = "，".join(campaign_bits) if campaign_bits else "无"
        print(f"- {name} [{state}] interval={project.interval}s data_file={project.data_file}")
        print(f"    url={project.url}")
        print(f"    窗口: {window_text}    campaign: {campaign_text}")
    return 0


def cmd_check(path: str | Path | None) -> int:
    config = _load_config(path)
    print(f"配置有效：{len(config.order)} 个项目；默认项目：{config.default_project_name}")
    for name in config.order:
        project = config.projects[name]
        print(f"  {name}: {'启用' if project.enabled else '停用'}, {project.data_file}, interval={project.interval}")
    return 0


def cmd_preview(path: str | Path | None, project_name: str | None, url: str | None) -> int:
    target = url
    if not target:
        config = _load_config(path)
        target = config.project(project_name).url
    preview = preview_url(target)
    print_preview(preview)
    return 0


def _build_entry(name: str, url: str, data_file: str, interval: int,
                 enabled: bool = True) -> dict[str, Any]:
    return {
        "url": url,
        "data_file": data_file,
        "interval": interval,
        "enabled": enabled,
    }


def _add_project(raw: dict[str, Any], path: str | Path | None, *,
                 name: str, url: str, data_file: str, interval: int,
                 enabled: bool = True, assume_yes: bool = False) -> int:
    projects = raw.setdefault("projects", {})
    if not isinstance(projects, dict):
        raise tracker_config.ConfigError("projects 必须是非空对象")
    if any(str(existing).casefold() == name.casefold() for existing in projects):
        raise tracker_config.ConfigError(f"项目名已存在（大小写不敏感）: {name}")
    candidate = copy.deepcopy(raw)
    candidate["projects"][name] = _build_entry(name, url, data_file, interval, enabled)
    _validate_candidate(candidate, path)
    preview = preview_url(url)
    print_preview(preview)
    if not assume_yes and not _confirm():
        print("已取消，未写入配置。")
        return 0
    _validate_candidate(candidate, path)
    tracker_config.atomic_save(candidate, path)
    print(f"已保存项目: {name}")
    return 0


def cmd_add(path: str | Path | None, args: argparse.Namespace) -> int:
    raw = _load_raw(path)
    interval = args.interval if args.interval is not None else _entry_interval(raw, args.name)
    return _add_project(
        raw, path,
        name=args.name, url=args.url, data_file=args.data_file,
        interval=interval, enabled=not args.disabled, assume_yes=args.yes,
    )


def _disable_project(raw: dict[str, Any], path: str | Path | None, wanted: str) -> int:
    name = _find_raw_name(raw, wanted)
    entry = raw["projects"][name]
    if not isinstance(entry, dict):
        raise tracker_config.ConfigError(f"项目 {name!r} 的配置必须是对象")
    entry["enabled"] = False
    tracker_config.atomic_save(raw, path)
    print(f"已停用项目: {name}")
    return 0


def cmd_disable(path: str | Path | None, project_name: str) -> int:
    return _disable_project(_load_raw(path), path, project_name)


# ---------------------------------------------------------------- 交互菜单

def interactive_add(path: str | Path | None) -> None:
    raw = _load_raw(path)
    name = _ask("项目名（字母数字下划线）")
    if not name:
        print("项目名不能为空。")
        return
    if any(str(existing).casefold() == name.casefold()
           for existing in (raw.get("projects") or {})):
        print(f"项目名已存在（大小写不敏感）: {name}")
        return
    url = _ask("追踪链接")
    data_file = _ask("数据文件名（相对仓库根）", f"data/{name}.csv")
    interval = _ask_int("抓取间隔（秒，30-3600）", _entry_interval(raw, name))
    _add_project(raw, path, name=name, url=url, data_file=data_file,
                 interval=interval, enabled=True)


def interactive_modify(path: str | Path | None) -> None:
    config = _load_config(path)
    name = _choose_project(config)
    raw = _load_raw(path)
    entry = raw["projects"][name]
    if not isinstance(entry, dict):
        raise tracker_config.ConfigError(f"项目 {name!r} 的配置必须是对象")
    candidate = copy.deepcopy(raw)
    target = candidate["projects"][name]
    target["url"] = _ask("追踪链接", str(entry.get("url", "")))
    target["data_file"] = _ask("数据文件名", str(entry.get("data_file", "")))
    target["interval"] = _ask_int("抓取间隔（秒）", int(entry.get("interval", config.projects[name].interval)))
    enabled_text = _ask("是否启用（y/n）", "y" if entry.get("enabled", True) else "n")
    target["enabled"] = enabled_text.lower() in ("y", "yes", "1", "true")
    _validate_candidate(candidate, path)
    preview = preview_url(str(target["url"]))
    print_preview(preview)
    if not _confirm():
        print("已取消，未写入配置。")
        return
    _validate_candidate(candidate, path)
    tracker_config.atomic_save(candidate, path)
    print(f"已保存项目: {name}")


def interactive_disable(path: str | Path | None) -> None:
    config = _load_config(path)
    name = _choose_project(config)
    _disable_project(_load_raw(path), path, name)


def interactive_preview(path: str | Path | None) -> None:
    config = _load_config(path)
    name = _choose_project(config)
    preview = preview_url(config.projects[name].url)
    print_preview(preview)


def interactive_menu(path: str | Path | None) -> int:
    while True:
        print()
        print("trackers.json 配置工具")
        print("  1. 列出项目")
        print("  2. 新增项目")
        print("  3. 修改项目")
        print("  4. 停用项目")
        print("  5. 预览项目")
        print("  0. 退出")
        try:
            choice = input("请选择: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if choice in ("0", "q", "quit", "exit"):
            return 0
        try:
            if choice == "1":
                cmd_list(path)
            elif choice == "2":
                interactive_add(path)
            elif choice == "3":
                interactive_modify(path)
            elif choice == "4":
                interactive_disable(path)
            elif choice == "5":
                interactive_preview(path)
            else:
                print("未知选项，请输入 0-5。")
        except tracker_config.ConfigError as exc:
            print(f"错误: {exc}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 菜单不因单个操作失败退出
            print(f"操作失败: {exc}", file=sys.stderr)


# ---------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="trackers.json 配置工具")
    parser.add_argument("--config", default=str(CONFIG_PATH), help="配置文件路径")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="列出项目")
    sub.add_parser("check", help="校验配置")

    p_preview = sub.add_parser("preview", help="抓取并预览项目")
    p_preview.add_argument("--project", help="项目名（缺省用默认项目）")
    p_preview.add_argument("--url", help="直接预览一个摩点链接")

    p_add = sub.add_parser("add", help="新增项目（保存前预览）")
    p_add.add_argument("--name", required=True, help="项目名")
    p_add.add_argument("--url", required=True, help="追踪链接")
    p_add.add_argument("--data-file", required=True, help="数据文件名（相对仓库根）")
    p_add.add_argument("--interval", type=int, help="抓取间隔（秒）")
    p_add.add_argument("--disabled", action="store_true", help="新增后停用")
    p_add.add_argument("--yes", action="store_true", help="预览成功后直接保存，不再询问")

    p_disable = sub.add_parser("disable", help="停用项目")
    p_disable.add_argument("--project", required=True, help="项目名")
    return parser


def main(argv: list[str] | None = None) -> int:
    _utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    path = args.config
    try:
        if args.command is None:
            return interactive_menu(path)
        if args.command == "list":
            return cmd_list(path)
        if args.command == "check":
            return cmd_check(path)
        if args.command == "preview":
            return cmd_preview(path, args.project, args.url)
        if args.command == "add":
            return cmd_add(path, args)
        if args.command == "disable":
            return cmd_disable(path, args.project)
        parser.error(f"未知子命令: {args.command}")
    except tracker_config.ConfigError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 CLI 顶层兜底，保证非零退出
        print(f"操作失败: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
