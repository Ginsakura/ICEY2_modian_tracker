"""每日 Git 自动同步守护进程（纯标准库，无第三方依赖，Windows / Termux 通用）

任务流定义从 sync_config.json 读取，运行时打印时刻表预览后执行任务队列。

用法:
    python sync_data.py               # 以守护进程方式运行
    python sync_data.py --dry-run     # 仅打印未来调度计划，不执行任何 git 命令
    python sync_data.py --tick 30     # 设置轮询间隔秒数（默认 30）
    也可作为线程嵌入 tracker：modian_tracker.py 的 run_forever 会以 daemon
    线程方式调用 sync_loop()，随抓取进程一同启停，无需单独驱动。
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from shared_logger import get_logger

logger = get_logger("sync_data")

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "sync_config.json"


# ── 配置加载 ────────────────────────────────────────────────────────────────
def load_config():
    """从 sync_config.json 加载配置，返回 (task_flows, commit_prefix, git_timeout, state_file)"""
    try:
        raw = CONFIG_PATH.read_text(encoding="utf-8")
        cfg = json.loads(raw)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"配置文件加载失败: {e}，使用内置默认配置")
        return _default_config()

    task_flows = {}
    for flow_key, flow_cfg in cfg.get("task_flows", {}).items():
        main = flow_cfg["main"]
        retries = flow_cfg.get("retries", [])
        task_flows[flow_key] = {
            "trigger_hour": flow_cfg["trigger_hour"],
            "description": flow_cfg.get("description", ""),
            "main": _parse_window(main),
            "retries": [_parse_window(w) for w in retries],
        }

    return (
        task_flows,
        cfg.get("commit_prefix", "auto sync data: termux "),
        cfg.get("git_timeout", 120),
        cfg.get("state_file", ".git_sync_state.json"),
    )


def _parse_window(w):
    """将 JSON 窗口对象解析为内部元组 (name, days_offset, hour, minute, retries, interval_min)"""
    return (
        w["name"],
        w.get("days_offset", 0),
        w["hour"],
        w.get("minute", 0),
        w.get("retries", 0),
        w.get("interval_minutes", 10),
    )


def _default_config():
    """内置默认配置（配置文件缺失时的后备）"""
    return (
        {
            "noon": {
                "trigger_hour": 12,
                "description": "午间任务流",
                "main": ("d12", 0, 12, 0, 3, 5),
                "retries": [("d16", 0, 16, 0, 2, 10), ("d18", 0, 18, 0, 2, 10)],
            },
            "night": {
                "trigger_hour": 22,
                "description": "夜间任务流",
                "main": ("d22", 0, 22, 0, 3, 5),
                "retries": [("d03", 1, 3, 0, 2, 10), ("d08", 1, 8, 0, 2, 10)],
            },
        },
        "auto sync data: termux ",
        120,
        ".git_sync_state.json",
    )


# 加载配置
TASK_FLOWS, COMMIT_PREFIX, GIT_TIMEOUT, STATE_FILE = load_config()
STATE_PATH = SCRIPT_DIR / STATE_FILE


# ── 状态管理 ────────────────────────────────────────────────────────────────
def _empty_flow_state(flow_key):
    """单个任务流的空状态"""
    return {"flow": flow_key, "phase": "done", "attempts_used": 0, "commit_done": False}


def load_state():
    """加载状态文件，兼容旧版单任务流格式"""
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                # 新版：所有任务流 key 都存在
                if all(k in data for k in TASK_FLOWS):
                    return data
                # 旧版单任务流格式迁移
                if "phase" in data:
                    logger.info("检测到旧版状态格式，迁移中...")
                    return _migrate_legacy_state(data)
        except (json.JSONDecodeError, OSError):
            logger.warning("状态文件损坏，将重新开始")
    return {k: _empty_flow_state(k) for k in TASK_FLOWS}


def _migrate_legacy_state(old):
    """将旧版单任务流状态迁移为新版多任务流格式"""
    phase = old.get("phase", "done")
    result = {k: _empty_flow_state(k) for k in TASK_FLOWS}
    for flow_key, flow in TASK_FLOWS.items():
        all_names = [w[0] for w in get_flow_windows(flow_key)]
        if phase in all_names:
            result[flow_key] = {
                "flow": flow_key,
                "phase": phase,
                "attempts_used": old.get("attempts_used", 0),
                "commit_done": old.get("commit_done", False),
            }
            break
    return result


def save_state(state):
    try:
        STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as e:
        logger.warning(f"状态文件写入失败: {e}")


# ── 任务流辅助函数 ──────────────────────────────────────────────────────────
def get_flow_windows(flow_key):
    """返回任务流的所有窗口列表 [main, retry1, retry2, ...]"""
    flow = TASK_FLOWS[flow_key]
    return [flow["main"]] + flow["retries"]


def get_window_info(window_name):
    """按窗口名查找其所属任务流和窗口元组，未找到返回 (None, None)"""
    for flow_key, flow in TASK_FLOWS.items():
        for win in get_flow_windows(flow_key):
            if win[0] == window_name:
                return flow_key, win
    return None, None


def window_start(task_date_str, window_tuple):
    """计算窗口的起始时刻（datetime）"""
    task_date = datetime.strptime(task_date_str, "%Y-%m-%d").date()
    _, days, hour, minute, _, _ = window_tuple
    return datetime.combine(task_date + timedelta(days=days), datetime.min.time()) \
        .replace(hour=hour, minute=minute)


def new_flow_state(flow_key, today):
    """开启一个新的任务流状态"""
    return {
        "flow": flow_key,
        "task_date": str(today.date()),
        "phase": TASK_FLOWS[flow_key]["main"][0],
        "attempts_used": 0,
        "commit_done": False,
    }


# ── Git 操作 ────────────────────────────────────────────────────────────────
def run_git(args):
    """执行 git 命令，成功返回 True"""
    logger.info(f"执行: git {' '.join(args)}")
    try:
        proc = subprocess.run(
            ["git", *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace", cwd=SCRIPT_DIR, timeout=GIT_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.error(f"git 命令异常: {e}")
        return False
    if proc.returncode == 0:
        logger.info(f"git {args[0]} 成功")
        return True
    detail = (proc.stdout + " " + proc.stderr).strip()
    logger.warning(f"git {args[0]} 失败: {detail if detail else f'退出码 {proc.returncode}'}")
    return False


def do_attempt(flow_state, now):
    """执行一次尝试：主窗口先 add+commit，然后 push。返回 (新状态, 是否成功)"""
    flow_key = flow_state["flow"]
    main_window_name = TASK_FLOWS[flow_key]["main"][0]

    if flow_state["phase"] == main_window_name and not flow_state["commit_done"]:
        if run_git(["add", "."]):
            msg = COMMIT_PREFIX + now.strftime("%m%d %H%M")
            run_git(["commit", "-m", msg])
        flow_state["commit_done"] = True
        logger.info(f"[{flow_key}] 本任务 add/commit 已完成")
    ok = run_git(["push"])
    return flow_state, ok


# ── 状态推进 ────────────────────────────────────────────────────────────────
def advance(flow_state):
    """当前窗口尝试耗尽，推进到下一窗口；最后一个窗口后任务关闭"""
    flow_key = flow_state["flow"]
    phases = [w[0] for w in get_flow_windows(flow_key)]
    i = phases.index(flow_state["phase"])

    if i == len(phases) - 1:
        flow_state["phase"] = "done"
        flow_state["attempts_used"] = 0
        logger.info(f"[{flow_key}] 全部窗口尝试失败，任务流关闭，交由下一次主窗口触发")
    else:
        flow_state["phase"] = phases[i + 1]
        flow_state["attempts_used"] = 0
        logger.info(f"[{flow_key}] 窗口 {phases[i]} 尝试耗尽，推进到下一窗口 {phases[i + 1]}")
    return flow_state


def tick(flow_state, now):
    """处理一次任务推进（尝试 / 跳窗 / 收尾）"""
    if flow_state["phase"] == "done":
        return flow_state

    flow_key = flow_state["flow"]
    _, window_tuple = get_window_info(flow_state["phase"])
    _, _, _, _, retries, interval_min = window_tuple
    max_attempts = retries + 1

    t_start = window_start(flow_state["task_date"], window_tuple)
    window_end = t_start + timedelta(minutes=max_attempts * interval_min)

    if now >= window_end:
        logger.info(f"[{flow_key}] 窗口 {flow_state['phase']} 已过期（设备可能关机错过），跳过剩余尝试")
        return advance(flow_state)

    attempt_time = t_start + timedelta(minutes=flow_state["attempts_used"] * interval_min)
    if now < attempt_time:
        return flow_state  # 尚未到下一次尝试时刻
    if attempt_time - now > timedelta(minutes=1):
        logger.warning(f"[{flow_key}] 检测到时间回拨，忽略本次误触发的尝试")
        return flow_state

    logger.info(f"[{flow_key}] 窗口 {flow_state['phase']} 第 {flow_state['attempts_used'] + 1} 次尝试")
    flow_state, ok = do_attempt(flow_state, now)
    if ok:
        flow_state["phase"] = "done"
        flow_state["attempts_used"] = 0
        logger.info(f"[{flow_key}] push 成功，任务流结束")
    else:
        flow_state["attempts_used"] += 1
        if flow_state["attempts_used"] >= max_attempts:
            return advance(flow_state)
        logger.info(f"[{flow_key}] {interval_min} 分钟后的第 {flow_state['attempts_used'] + 1} 次尝试")
    return flow_state


# ── 调度计算 ────────────────────────────────────────────────────────────────
def next_wakeup_for_flow(flow_state, now):
    """计算单个任务流的下次唤醒时刻（datetime），返回 None 表示无需唤醒"""
    if flow_state["phase"] == "done":
        return None

    _, window_tuple = get_window_info(flow_state["phase"])
    _, _, _, _, retries, interval_min = window_tuple
    max_attempts = retries + 1

    t_start = window_start(flow_state["task_date"], window_tuple)
    window_end = t_start + timedelta(minutes=max_attempts * interval_min)

    if now < window_end:
        attempt_time = t_start + timedelta(minutes=flow_state["attempts_used"] * interval_min)
        return max(now + timedelta(seconds=1), attempt_time)
    return None


def next_wakeup(state, now, tick_sec):
    """下一次需要醒来的时刻，确保不会睡过头多于一个轮询周期"""
    candidates = []

    for flow_key, flow_state in state.items():
        t = next_wakeup_for_flow(flow_state, now)
        if t:
            candidates.append(t)

    for flow_key, flow in TASK_FLOWS.items():
        trigger_hour = flow["trigger_hour"]
        t_trigger = now.replace(hour=trigger_hour, minute=0, second=0, microsecond=0)
        if now < t_trigger:
            candidates.append(t_trigger)
        else:
            candidates.append(t_trigger + timedelta(days=1))

    if not candidates:
        return tick_sec

    delta = min(candidates) - now
    return max(1, min(int(delta.total_seconds()), tick_sec))


# ── 时刻表预览 ──────────────────────────────────────────────────────────────
def print_schedule(now=None):
    """打印任务时刻表预览"""
    if now is None:
        now = datetime.now()

    logger.info("=" * 56)
    logger.info(f"配置文件    : {CONFIG_PATH}")
    logger.info(f"状态文件    : {STATE_PATH}")
    logger.info(f"当前时间    : {now:%Y-%m-%d %H:%M:%S}")
    logger.info("-" * 56)
    logger.info("任务时刻表  :")

    for flow_key, flow in TASK_FLOWS.items():
        trigger_hour = flow["trigger_hour"]
        t_trigger = now.replace(hour=trigger_hour, minute=0, second=0, microsecond=0)
        if now >= t_trigger:
            t_trigger += timedelta(days=1)

        desc = flow.get("description", "")
        logger.info(f"  [{flow_key}] {desc}（触发时刻 {trigger_hour:02d}:00）")
        windows = get_flow_windows(flow_key)
        for win in windows:
            win_name, days, hour, minute, retries, interval_min = win
            t_start = datetime.combine(
                t_trigger.date() + timedelta(days=days),
                datetime.min.time()
            ).replace(hour=hour, minute=minute)

            is_main = (win_name == flow["main"][0])
            tag = "commit+push" if is_main else "push"

            for k in range(retries + 1):
                when = t_start + timedelta(minutes=k * interval_min)
                logger.info(f"    {when:%m-%d %H:%M}  {win_name} 第{k+1}次尝试（{tag}，间隔{interval_min}分钟）")

    logger.info("规则: 任一 push 成功即结束该任务流；全部失败则关闭，交由下一次主窗口触发")
    logger.info("=" * 56)


# ── Dry Run ─────────────────────────────────────────────────────────────────
def dry_run():
    """打印未来调度计划和当前状态，不执行任何 git 命令"""
    now = datetime.now()
    print_schedule(now)
    state = load_state()
    logger.info(f"当前状态: {json.dumps(state, ensure_ascii=False)}")
    return 0


# ── 主循环 ──────────────────────────────────────────────────────────────────
def sync_loop(tick_sec=30):
    """同步主循环：可独立运行，也可作为 daemon 线程嵌入 tracker"""
    print_schedule()
    logger.info("每日同步守护进程已启动")

    while True:
        try:
            now = datetime.now()
            state = load_state()

            # 检查各任务流的触发时刻
            for flow_key, flow in TASK_FLOWS.items():
                trigger_hour = flow["trigger_hour"]
                t_trigger = now.replace(hour=trigger_hour, minute=0, second=0, microsecond=0)

                if now >= t_trigger and state[flow_key]["phase"] == "done":
                    task_date_str = state[flow_key].get("task_date")
                    expected_date = str(t_trigger.date())
                    if task_date_str != expected_date:
                        state[flow_key] = new_flow_state(flow_key, t_trigger)
                        logger.info(
                            f"[{flow_key}] 开启新任务流: task_date={state[flow_key]['task_date']}，"
                            f"commit 消息: {COMMIT_PREFIX}{now:%m%d %H%M}"
                        )

            # 推进各任务流
            for flow_key in TASK_FLOWS:
                if state[flow_key]["phase"] != "done":
                    state[flow_key] = tick(state[flow_key], now)

            save_state(state)
            time.sleep(next_wakeup(state, datetime.now(), tick_sec))

        except KeyboardInterrupt:
            logger.info("已手动停止同步守护进程。")
            break
        except Exception as e:  # noqa: BLE001
            logger.error(f"同步循环异常（{e.__class__.__name__}: {e}），10 秒后重试")
            time.sleep(10)


def main():
    parser = argparse.ArgumentParser(description="每日 Git 自动同步守护进程")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅打印未来调度计划，不执行任何 git 命令")
    parser.add_argument("--tick", type=int, default=30,
                        help="轮询间隔秒数（默认 30）")
    args = parser.parse_args()

    if args.dry_run:
        sys.exit(dry_run())
    if args.tick < 1:
        parser.error("--tick 必须为正整数秒")

    sync_loop(args.tick)


if __name__ == "__main__":
    main()
