#!/usr/bin/env python3
"""trackers.json 配置校验的回归测试（OpenSpec 任务 1.6 / 7.4）。"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import tracker_config  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def ok(label: str) -> None:
    PASS.append(label)
    print(f"PASS {label}")


def bad(label: str, detail: str = "") -> None:
    FAIL.append(label)
    print(f"FAIL {label}{(': ' + detail) if detail else ''}")


def base() -> dict:
    return {
        "version": 1,
        "defaults": {"interval": 60, "project": "icey2"},
        "projects": {
            "icey2": {
                "url": "https://zhongchou.modian.com/item/160465.html",
                "data_file": "data/icey2.csv",
                "interval": 60,
                "campaign": {
                    "deadline": "2026-11-15 23:59",
                    "start": "2026-10-05 22:00",
                    "phases": [{"name": "P1", "start": "2026-10-05", "end": "2026-10-08"}],
                },
            },
            "linglan": {
                "url": "https://zhongchou.modian.com/item/160539.html",
                "data_file": "data/linglan.csv",
                "collect_from": "2026-11-15 00:00",
                "collect_until": "2027-01-10 12:00",
            },
        },
    }


def expect_error(label: str, mutate, path: Path) -> None:
    raw = base()
    mutate(raw)
    try:
        tracker_config.validate_config(raw, path)
    except tracker_config.ConfigError:
        ok(label)
    except Exception as exc:  # noqa: BLE001
        bad(label, f"抛出了 {type(exc).__name__}: {exc}")
    else:
        bad(label, "没有拒绝")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="verify_config_", dir=ROOT / "temp"))
    config_path = tmp / "trackers.json"
    print(f"临时配置: {config_path}")

    try:
        cfg = tracker_config.validate_config(base(), config_path)
        assert cfg.default_project_name == "icey2"
        assert cfg.project("ICEY2").name == "icey2"
        assert [p.name for p in cfg.enabled_projects()] == ["icey2", "linglan"]
        assert cfg.projects["linglan"].collect_from is not None
        assert cfg.projects["icey2"].campaign.phases[0].start.isoformat() == "2026-10-05"
        ok("合法配置、大小写不敏感选择、默认项目")

        raw = base()
        raw["defaults"]["project"] = "ICEY2"
        cfg = tracker_config.validate_config(raw, config_path)
        assert cfg.default_project_name == "icey2"
        ok("defaults.project 大小写不敏感")

        raw = base()
        raw["defaults"]["interval"] = 120
        cfg = tracker_config.validate_config(raw, config_path)
        assert cfg.projects["icey2"].interval == 60
        assert cfg.projects["linglan"].interval == 120
        ok("项目 interval 覆盖 defaults.interval")

        expect_error("拒绝大小写重名", lambda r: r["projects"].update({"ICEY2": copy.deepcopy(r["projects"]["icey2"])}), config_path)
        expect_error("拒绝非法项目名", lambda r: r["projects"].update({"bad-name": copy.deepcopy(r["projects"]["icey2"])}), config_path)
        expect_error("拒绝超长项目名", lambda r: r["projects"].update({"a" * 33: copy.deepcopy(r["projects"]["icey2"])}), config_path)
        expect_error("拒绝 http 链接", lambda r: r["projects"]["icey2"].update({"url": "http://zhongchou.modian.com/item/160465.html"}), config_path)
        expect_error("拒绝非摩点链接", lambda r: r["projects"]["icey2"].update({"url": "https://example.com/item/160465.html"}), config_path)
        expect_error("拒绝非数字 item", lambda r: r["projects"]["icey2"].update({"url": "https://zhongchou.modian.com/item/abc.html"}), config_path)
        expect_error("拒绝带查询参数的链接", lambda r: r["projects"]["icey2"].update({"url": "https://zhongchou.modian.com/item/160465.html?x=1"}), config_path)
        expect_error("拒绝绝对 data_file", lambda r: r["projects"]["icey2"].update({"data_file": "C:/tmp/a.csv"}), config_path)
        expect_error("拒绝 .. 路径", lambda r: r["projects"]["icey2"].update({"data_file": "data/../a.csv"}), config_path)
        expect_error("拒绝非 csv 文件", lambda r: r["projects"]["icey2"].update({"data_file": "data/a.txt"}), config_path)
        expect_error("拒绝两个项目共用文件", lambda r: r["projects"]["linglan"].update({"data_file": "data/icey2.csv"}), config_path)
        expect_error("拒绝 interval 过小", lambda r: r["projects"]["icey2"].update({"interval": 29}), config_path)
        expect_error("拒绝 interval 过大", lambda r: r["projects"]["icey2"].update({"interval": 3601}), config_path)
        expect_error("拒绝布尔 interval", lambda r: r["projects"]["icey2"].update({"interval": True}), config_path)
        expect_error("拒绝未知项目字段", lambda r: r["projects"]["icey2"].update({"typo": 1}), config_path)
        expect_error("拒绝未知 campaign 字段", lambda r: r["projects"]["icey2"]["campaign"].update({"typo": 1}), config_path)
        expect_error("拒绝逆序 phases", lambda r: r["projects"]["icey2"]["campaign"].update(
            {"phases": [{"name": "P1", "start": "2026-10-08", "end": "2026-10-05"}]}), config_path)
        expect_error("拒绝空 phases", lambda r: r["projects"]["icey2"]["campaign"].update({"phases": []}), config_path)
        expect_error("拒绝逆序采集窗口", lambda r: r["projects"]["linglan"].update(
            {"collect_from": "2027-01-10 13:00", "collect_until": "2027-01-10 12:00"}), config_path)
        expect_error("拒绝非法 phases 日期", lambda r: r["projects"]["icey2"]["campaign"]["phases"][0].update(
            {"start": "2026/10/05"}), config_path)
        expect_error("拒绝 campaign 截止时间带秒", lambda r: r["projects"]["icey2"]["campaign"].update(
            {"deadline": "2026-11-15 23:59:00"}), config_path)
        expect_error("拒绝不存在的 defaults.project", lambda r: r["defaults"].update({"project": "nope"}), config_path)
        expect_error("拒绝 milestone_step=0", lambda r: r["projects"]["icey2"]["campaign"].update({"milestone_step": 0}), config_path)
        expect_error("拒绝 milestone_step 非整数", lambda r: r["projects"]["icey2"]["campaign"].update({"milestone_step": 1.5}), config_path)
        expect_error("拒绝未知顶层版本", lambda r: r.update({"version": 2}), config_path)
        expect_error("拒绝空 projects", lambda r: r.update({"projects": {}}), config_path)
        expect_error("拒绝非对象项目", lambda r: r["projects"].update({"bad": []}), config_path)

        # 原子保存：写临时配置后能读回，且落盘为 UTF-8 JSON
        tracker_config.atomic_save(base(), config_path)
        loaded = tracker_config.load_config(config_path)
        assert loaded.project("linglan").collect_until.year == 2027
        text = config_path.read_text(encoding="utf-8")
        assert "\"linglan\"" in text and "\n" in text
        ok("atomic_save 落盘并可重新加载")

        # 配置路径不存在
        try:
            tracker_config.load_config(tmp / "missing.json")
        except tracker_config.ConfigError:
            ok("不存在的配置文件给出错误")
        else:
            bad("不存在的配置文件给出错误", "没有抛错")
    finally:
        for p in sorted(tmp.rglob("*"), reverse=True):
            try:
                p.unlink() if p.is_file() else p.rmdir()
            except OSError:
                pass
        try:
            tmp.rmdir()
        except OSError:
            pass

    print(f"\n{len(PASS)} PASS / {len(FAIL)} FAIL")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
