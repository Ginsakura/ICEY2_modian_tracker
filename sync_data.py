"""每日 Git 自动同步守护进程（纯标准库，无第三方依赖，Windows / Termux 通用）

每日 23:00 执行一次「git add . + git commit -m "sync data: termux 月日 时分" + git push」，
push 失败按时间表重试；任一窗口 push 成功即结束本次任务。

重试时间表（次数 = 重试次数，每次间隔 10 分钟，含首试共 次数+1 次尝试）：
    当日 23:00  重试 3 次
    次日 08:00  重试 2 次
    次日 12:00  重试 2 次
    次日 18:00  重试 2 次
18 时窗口全部失败则关闭本次任务，交由下一次 23 时任务。

注意：
    - 每天仅在 23:00 任务开始时进行一次 add 和 commit，其余时刻仅尝试 push。
    - 任一 push 成功即结束本次任务。

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

# (窗口名, 相对任务起始日偏移天数, 小时, 分钟, 追加的重试次数)
TASK_WINDOWS = [
    ("d23", 0, 23, 0, 3),
    ("d08", 1, 8, 0, 2),
    ("d12", 1, 12, 0, 2),
    ("d18", 1, 18, 0, 2),
]
RETRY_INTERVAL_MIN = 10  # 重试间隔（分钟）
COMMIT_PREFIX = "sync data: termux "  # 提交消息前缀，末尾为「月日 时分」，如 "1011 2300"
STATE_FILE = ".git_sync_state.json"
GIT_TIMEOUT = 120  # 单个 git 命令超时秒数

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_PATH = SCRIPT_DIR / STATE_FILE


def log(msg):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def load_state():
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "phase" in data:
                return data
        except (json.JSONDecodeError, OSError):
            log("警告: 状态文件损坏，将重新开始")
    return {"task_date": None, "phase": "done", "attempts_used": 0, "commit_done": False}


def save_state(state):
    try:
        STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as e:
        log(f"警告: 状态文件写入失败: {e}")


def window_by_phase(phase):
    """按窗口名返回 (偏移天数, 小时, 分钟, 重试次数)，未知窗口返回 None"""
    for name, days, hour, minute, retries in TASK_WINDOWS:
        if name == phase:
            return days, hour, minute, retries
    return None


def window_start(state):
    """当前窗口的起始时刻（datetime）"""
    task_date = datetime.strptime(state["task_date"], "%Y-%m-%d").date()
    days, hour, minute, _ = window_by_phase(state["phase"])
    return datetime.combine(task_date + timedelta(days=days), datetime.min.time()) \
        .replace(hour=hour, minute=minute)


def new_task(today):
    """开启一个以今天为任务起始日的新任务（23:00 节点）"""
    return {"task_date": str(today.date()), "phase": "d23",
            "attempts_used": 0, "commit_done": False}


def run_git(args):
    """执行 git 命令，成功返回 True"""
    log("执行: git " + " ".join(args))
    try:
        proc = subprocess.run(
            ["git", *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace", cwd=SCRIPT_DIR, timeout=GIT_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        log(f"git 命令异常: {e}")
        return False
    if proc.returncode == 0:
        log(f"git {args[0]} 成功")
        return True
    detail = (proc.stdout + " " + proc.stderr).strip()
    log(f"git {args[0]} 失败: {detail if detail else f'退出码 {proc.returncode}'}")
    return False


def do_attempt(state, now):
    """执行一次尝试：23:00 窗口先 add+commit，然后 push。返回 (新状态, 是否成功)"""
    if state["phase"] == "d23" and not state["commit_done"]:
        if run_git(["add", "."]):
            msg = COMMIT_PREFIX + now.strftime("%m%d %H%M")
            run_git(["commit", "-m", msg])
        # 空提交（无变更）不视为失败，继续尝试 push
        state["commit_done"] = True
        log("本任务 add/commit 已完成")
    ok = run_git(["push"])
    return state, ok


def advance(state):
    """当前窗口尝试耗尽，推进到下一窗口；最后一个窗口后任务关闭"""
    phases = [w[0] for w in TASK_WINDOWS]
    i = phases.index(state["phase"])
    if i == len(phases) - 1:
        state["phase"] = "done"
        state["attempts_used"] = 0
        log("18 时窗口全部尝试失败，本次任务结束，交由下一次 23 时任务")
    else:
        state["phase"] = phases[i + 1]
        state["attempts_used"] = 0
        log(f"窗口 {phases[i]} 尝试耗尽，推进到下一窗口 {phases[i + 1]}")
    return state


def tick(state, now):
    """处理一次任务推进（尝试 / 跳窗 / 收尾）"""
    days, hour, minute, retries = window_by_phase(state["phase"])
    t_start = window_start(state)
    max_attempts = retries + 1
    window_end = t_start + timedelta(minutes=max_attempts * RETRY_INTERVAL_MIN)

    if now >= window_end:
        log(f"窗口 {state['phase']} 已过期（设备可能关机错过），跳过剩余尝试")
        return advance(state)

    attempt_time = t_start + timedelta(minutes=state["attempts_used"] * RETRY_INTERVAL_MIN)
    if now < attempt_time:
        return state  # 尚未到下一次尝试时刻
    if attempt_time - now > timedelta(minutes=1):
        log(f"检测到时间回拨，忽略本次误触发的尝试")
        return state

    log(f"窗口 {state['phase']} 第 {state['attempts_used'] + 1} 次尝试")
    state, ok = do_attempt(state, now)
    if ok:
        state["phase"] = "done"
        state["attempts_used"] = 0
        log("本次任务 push 成功，任务结束")
    else:
        state["attempts_used"] += 1
        if state["attempts_used"] >= max_attempts:
            return advance(state)
        log(f"{RETRY_INTERVAL_MIN} 分钟后的第 {state['attempts_used'] + 1} 次尝试")
    return state


def next_wakeup(state, now, tick_sec):
    """下一次需要醒来的时刻（datetime），确保不会睡过头多于一个轮询周期"""
    t23_today = now.replace(hour=23, minute=0, second=0, microsecond=0)
    if now < t23_today:
        candidates = [t23_today]
    else:
        candidates = [t23_today + timedelta(days=1)]

    if state["phase"] != "done":
        t_start = window_start(state)
        _, _, _, retries = window_by_phase(state["phase"])
        max_attempts = retries + 1
        window_end = t_start + timedelta(minutes=max_attempts * RETRY_INTERVAL_MIN)
        if now < window_end:
            attempt_time = t_start + timedelta(minutes=state["attempts_used"] * RETRY_INTERVAL_MIN)
            candidates.append(max(now + timedelta(seconds=1), attempt_time))

    delta = min(candidates) - now
    return max(1, min(int(delta.total_seconds()), tick_sec))


def dry_run():
    """打印未来调度计划，不执行任何 git 命令"""
    now = datetime.now()
    print("=" * 60)
    print(f"当前时间    : {now:%Y-%m-%d %H:%M:%S}")
    state = load_state()
    print(f"当前状态    : {json.dumps(state, ensure_ascii=False)}")
    print(f"状态文件    : {STATE_PATH}")
    print("-" * 60)

    today = now.date()
    t23 = datetime.combine(today, datetime.min.time()).replace(hour=23, minute=0)
    if now >= t23:
        t23 = t23 + timedelta(days=1)
    print(f"下一新任务  : {t23:%Y-%m-%d %H:%M}（add + commit + push，失败重试 {TASK_WINDOWS[0][4]} 次）")
    print("任务时间表  :")
    for name, days, hour, minute, retries in TASK_WINDOWS:
        t = (t23 + timedelta(days=days)).replace(hour=hour, minute=minute)
        for k in range(retries + 1):
            when = t + timedelta(minutes=k * RETRY_INTERVAL_MIN)
            tag = "commit+push" if name == "d23" and k == 0 else "push"
            print(f"  {when:%m-%d %H:%M}  {name} 第{k+1}次尝试（{tag}）")
    print("规则        : 任意一次 push 成功即结束任务；18 时全部失败则关闭，交由次日 23 时新任务")
    return 0


def sync_loop(tick_sec=30):
    """同步主循环：可独立运行，也可作为 daemon 线程嵌入 tracker

    每轮：23:00 开新任务（仅此处 add+commit）→ 推进重试状态机 → 按下次唤醒时刻睡眠。
    线程模式下 KeyboardInterrupt 只会发给主线程，此处兜底捕获所有异常防止线程静默死亡。
    """
    log("每日同步守护进程已启动")
    while True:
        try:
            now = datetime.now()
            state = load_state()

            # 每日 23:00 开启新任务（覆盖任何历史任务，保证每 24 小时 commit 一次）
            t23_today = now.replace(hour=23, minute=0, second=0, microsecond=0)
            if now >= t23_today and state["task_date"] != str(now.date()):
                state = new_task(now)
                save_state(state)
                log(f"开启新任务: task_date={state['task_date']}，"
                    f"commit 消息: {COMMIT_PREFIX}{now:%m%d %H%M}")

            if state["phase"] != "done":
                state = tick(state, now)
                save_state(state)

            time.sleep(next_wakeup(state, datetime.now(), tick_sec))
        except KeyboardInterrupt:
            log("已手动停止同步守护进程。")
            break
        except Exception as e:  # noqa: BLE001 —— 守护循环兜底，任何异常都不允许终止线程
            log(f"同步循环异常（{e.__class__.__name__}: {e}），10 秒后重试")
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