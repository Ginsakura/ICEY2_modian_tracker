"""trackers.json 的加载、校验与原子保存。

配置结构见 trackers.json；所有时间均按本机本地时间解释（naive datetime）。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "trackers.json"

PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")
ITEM_PATH_RE = re.compile(r"^/item/[0-9]+\.html$")

PROJECT_KEYS = {
    "url", "data_file", "interval", "enabled",
    "collect_from", "collect_until", "campaign",
}
CAMPAIGN_KEYS = {"deadline", "start", "phases", "milestone_step"}
PHASE_KEYS = {"name", "start", "end"}


class ConfigError(ValueError):
    """配置非法；消息为可直接展示给用户的中文。"""


@dataclass(frozen=True)
class Phase:
    name: str
    start: date
    end: date


@dataclass(frozen=True)
class CampaignConfig:
    deadline: datetime | None = None
    start: datetime | None = None
    phases: tuple[Phase, ...] = ()
    milestone_step: int | None = None


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    url: str
    data_file: str
    data_path: Path
    interval: int
    enabled: bool
    collect_from: datetime | None
    collect_until: datetime | None
    campaign: CampaignConfig


@dataclass(frozen=True)
class TrackerConfig:
    path: Path
    version: int
    projects: Mapping[str, ProjectConfig]
    order: tuple[str, ...]
    default_project_name: str
    raw: Mapping[str, Any] = field(repr=False, compare=False)

    def project(self, name: str | None = None) -> ProjectConfig:
        key = name or self.default_project_name
        if key in self.projects:
            return self.projects[key]
        # 命令行容错：大小写不敏感匹配唯一项目名
        matches = [n for n in self.order if n.casefold() == str(key).casefold()]
        if len(matches) == 1:
            return self.projects[matches[0]]
        raise ConfigError(f"配置中没有项目: {key}")

    def enabled_projects(self) -> list[ProjectConfig]:
        return [self.projects[n] for n in self.order if self.projects[n].enabled]


def _config_path(path: str | Path | None) -> Path:
    return Path(path).resolve() if path else CONFIG_PATH


def _error(message: str) -> None:
    raise ConfigError(message)


def _as_int(value: Any, field_name: str, project_name: str | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        prefix = f"项目 {project_name!r}: " if project_name else ""
        _error(f"{prefix}{field_name} 必须是整数")
    return value


def _parse_dt(value: Any, field_name: str, project_name: str,
              formats: tuple[str, ...] = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S")) -> datetime:
    if not isinstance(value, str):
        _error(f"项目 {project_name!r}: {field_name} 必须是时间字符串")
    for fmt in formats:
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    _error(f"项目 {project_name!r}: {field_name} 时间格式应为 YYYY-MM-DD HH:MM[:SS]")


def _parse_date(value: Any, field_name: str, project_name: str) -> date:
    if not isinstance(value, str):
        _error(f"项目 {project_name!r}: {field_name} 必须是日期字符串")
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        _error(f"项目 {project_name!r}: {field_name} 日期格式应为 YYYY-MM-DD")


def _parse_collect(value: Any, field_name: str, project_name: str) -> datetime:
    return _parse_dt(value, field_name, project_name)


def _validate_campaign(raw: Any, project_name: str) -> CampaignConfig:
    if not isinstance(raw, dict):
        _error(f"项目 {project_name!r}: campaign 必须是对象")
    unknown = set(raw) - CAMPAIGN_KEYS
    if unknown:
        _error(f"项目 {project_name!r}: campaign 含未定义字段: {', '.join(sorted(unknown))}")

    deadline = None
    if "deadline" in raw and raw["deadline"] is not None:
        deadline = _parse_dt(raw["deadline"], "campaign.deadline", project_name,
                             formats=("%Y-%m-%d %H:%M",))
    start = None
    if "start" in raw and raw["start"] is not None:
        start = _parse_dt(raw["start"], "campaign.start", project_name,
                          formats=("%Y-%m-%d %H:%M",))

    phases: list[Phase] = []
    if "phases" in raw and raw["phases"] is not None:
        items = raw["phases"]
        if not isinstance(items, list) or not items:
            _error(f"项目 {project_name!r}: campaign.phases 必须是非空数组")
        for i, item in enumerate(items, 1):
            if not isinstance(item, dict):
                _error(f"项目 {project_name!r}: campaign.phases 第 {i} 项必须是对象")
            unknown = set(item) - PHASE_KEYS
            if unknown:
                _error(f"项目 {project_name!r}: campaign.phases 第 {i} 项含未定义字段: "
                       f"{', '.join(sorted(unknown))}")
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                _error(f"项目 {project_name!r}: campaign.phases 第 {i} 项 name 不能为空")
            p_start = _parse_date(item.get("start"), f"campaign.phases 第 {i} 项 start", project_name)
            p_end = _parse_date(item.get("end"), f"campaign.phases 第 {i} 项 end", project_name)
            if not p_start < p_end:
                _error(f"项目 {project_name!r}: campaign.phases 第 {i} 项 start 必须早于 end")
            phases.append(Phase(name.strip(), p_start, p_end))

    milestone_step = None
    if "milestone_step" in raw and raw["milestone_step"] is not None:
        milestone_step = _as_int(raw["milestone_step"], "campaign.milestone_step", project_name)
        if milestone_step <= 0:
            _error(f"项目 {project_name!r}: campaign.milestone_step 必须为正整数")

    return CampaignConfig(deadline, start, tuple(phases), milestone_step)


def _ensure_relative_data_file(value: Any, project_name: str, root: Path) -> tuple[str, Path]:
    if not isinstance(value, str) or not value.strip():
        _error(f"项目 {project_name!r}: data_file 必须是非空字符串")
    text = value.strip()
    if not text.lower().endswith(".csv"):
        _error(f"项目 {project_name!r}: data_file 必须以 .csv 结尾")
    if Path(text).is_absolute() or PureWindowsPath(text).drive:
        _error(f"项目 {project_name!r}: data_file 必须是相对路径，不能是绝对路径")
    if any(part == ".." for part in Path(text).parts):
        _error(f"项目 {project_name!r}: data_file 不能包含 ..")
    resolved = (root / text).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        _error(f"项目 {project_name!r}: data_file 解析后越出仓库目录: {resolved}")
    return text, resolved


def validate_config(raw: Any, path: str | Path | None = None) -> TrackerConfig:
    """校验原始 JSON 字典并构造 TrackerConfig；任何非法项都抛 ConfigError。"""
    config_path = _config_path(path)
    root = config_path.parent
    if not isinstance(raw, dict):
        _error("trackers.json 顶层必须是对象")

    version = _as_int(raw.get("version"), "version")
    if version != 1:
        _error(f"不支持的配置版本: {version}（当前只支持 version=1）")

    defaults = raw.get("defaults", {})
    if defaults is None:
        defaults = {}
    if not isinstance(defaults, dict):
        _error("defaults 必须是对象")
    default_interval = 60
    if "interval" in defaults:
        default_interval = _as_int(defaults["interval"], "defaults.interval")
        if not 30 <= default_interval <= 3600:
            _error("defaults.interval 必须在 30 到 3600 之间")
    default_project = defaults.get("project")
    if default_project is not None and (not isinstance(default_project, str) or not default_project):
        _error("defaults.project 必须是非空字符串")

    projects_raw = raw.get("projects")
    if not isinstance(projects_raw, dict) or not projects_raw:
        _error("projects 必须是非空对象")

    seen: dict[str, str] = {}
    parsed: dict[str, ProjectConfig] = {}
    order: list[str] = []
    data_paths: dict[str, str] = {}

    for name, entry in projects_raw.items():
        if not isinstance(name, str) or not PROJECT_NAME_RE.match(name):
            _error(f"项目名 {name!r} 非法：只允许大小写字母、数字与下划线，长度 1-32")
        folded = name.casefold()
        if folded in seen:
            _error(f"项目名大小写冲突: {seen[folded]!r} 与 {name!r}")
        seen[folded] = name
        order.append(name)

        if not isinstance(entry, dict):
            _error(f"项目 {name!r}: 配置必须是对象")
        unknown = set(entry) - PROJECT_KEYS
        if unknown:
            _error(f"项目 {name!r}: 含未定义字段: {', '.join(sorted(unknown))}")

        url = entry.get("url")
        if not isinstance(url, str):
            _error(f"项目 {name!r}: url 必须是非空字符串")
        from urllib.parse import urlsplit
        parts = urlsplit(url.strip())
        if (parts.scheme != "https" or parts.netloc != "zhongchou.modian.com"
                or not ITEM_PATH_RE.match(parts.path) or parts.query or parts.fragment):
            _error(f"项目 {name!r}: 目前只支持 https://zhongchou.modian.com/item/<数字>.html 形式的摩点链接")

        data_file, data_path = _ensure_relative_data_file(entry.get("data_file"), name, root)
        key = os.path.normcase(str(data_path))
        if key in data_paths:
            _error(f"项目 {name!r} 与 {data_paths[key]!r} 的 data_file 指向同一文件: {data_path}")
        data_paths[key] = name

        interval = default_interval
        if "interval" in entry:
            interval = _as_int(entry["interval"], "interval", name)
            if not 30 <= interval <= 3600:
                _error(f"项目 {name!r}: interval 必须在 30 到 3600 之间")

        enabled = entry.get("enabled", True)
        if not isinstance(enabled, bool):
            _error(f"项目 {name!r}: enabled 必须是布尔值")

        collect_from = None
        collect_until = None
        if "collect_from" in entry and entry["collect_from"] is not None:
            collect_from = _parse_collect(entry["collect_from"], "collect_from", name)
        if "collect_until" in entry and entry["collect_until"] is not None:
            collect_until = _parse_collect(entry["collect_until"], "collect_until", name)
        if collect_from is not None and collect_until is not None and not collect_from < collect_until:
            _error(f"项目 {name!r}: collect_from 必须早于 collect_until")

        campaign = CampaignConfig()
        if "campaign" in entry and entry["campaign"] is not None:
            campaign = _validate_campaign(entry["campaign"], name)

        parsed[name] = ProjectConfig(
            name=name,
            url=url.strip(),
            data_file=data_file,
            data_path=data_path,
            interval=interval,
            enabled=enabled,
            collect_from=collect_from,
            collect_until=collect_until,
            campaign=campaign,
        )

    if default_project is None:
        default_name = order[0]
    else:
        matches = [name for name in order if name.casefold() == default_project.casefold()]
        if not matches:
            _error(f"defaults.project 指向不存在的项目: {default_project}")
        default_name = matches[0]

    return TrackerConfig(
        path=config_path,
        version=version,
        projects=parsed,
        order=tuple(order),
        default_project_name=default_name,
        raw=raw,
    )


def load_raw(path: str | Path | None = None) -> dict[str, Any]:
    config_path = _config_path(path)
    try:
        text = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        _error(f"配置文件不存在: {config_path}")
    except OSError as exc:
        _error(f"读取配置文件失败: {config_path}: {exc}")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        _error(f"配置文件 JSON 解析失败: {config_path}: 第 {exc.lineno} 行第 {exc.colno} 列: {exc.msg}")
    return raw


def load_config(path: str | Path | None = None) -> TrackerConfig:
    config_path = _config_path(path)
    return validate_config(load_raw(config_path), config_path)


def default_project(config: TrackerConfig) -> ProjectConfig:
    return config.project(config.default_project_name)


def atomic_save(raw: Mapping[str, Any], path: str | Path | None = None) -> TrackerConfig:
    """校验后写同目录临时文件并原子替换；校验失败时不改动原文件。"""
    config_path = _config_path(path)
    validate_config(raw, config_path)
    tmp_path: str | None = None
    try:
        fd, tmp_path = tempfile.mkstemp(prefix=f".{config_path.name}.", suffix=".tmp", dir=config_path.parent)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, config_path)
        tmp_path = None
    except Exception:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise
    return load_config(config_path)
