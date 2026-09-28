"""Per-thread interrupt signaling for all tools.

Provides thread-scoped interrupt tracking so that interrupting one agent
session does not kill tools running in other sessions.  This is critical
in the gateway where multiple agents run concurrently in the same process.

The agent stores its execution thread ID at the start of run_conversation()
and passes it to set_interrupt()/clear_interrupt().  Tools call
is_interrupted() which checks the CURRENT thread — no argument needed.

Usage in tools:
    from .interrupt import is_interrupted
    if is_interrupted():
        return {"output": "[interrupted]", "returncode": 130}
"""

import logging
import os
import threading
from contextlib import contextmanager
from typing import Iterator

logger = logging.getLogger(__name__)

# Opt-in debug tracing — pairs with HERMES_DEBUG_INTERRUPT in
# tools/environments/base.py.  Enables per-call logging of set/check so the
# caller thread, target thread, and current state are visible when
# diagnosing "interrupt signaled but tool never saw it" reports.
_DEBUG_INTERRUPT = bool(os.getenv("HERMES_DEBUG_INTERRUPT"))

if _DEBUG_INTERRUPT:
    # AIAgent's quiet_mode path forces `tools` logger to ERROR on CLI startup.
    # Force our own logger back to INFO so the trace is visible in agent.log.
    logger.setLevel(logging.INFO)

# Set of thread idents that have been interrupted.
_interrupted_threads: set[int] = set()
_lock = threading.Lock()


def set_interrupt(active: bool, thread_id: int | None = None) -> None:
    """Set or clear interrupt for a specific thread.

    Args:
        active: True to signal interrupt, False to clear it.
        thread_id: Target thread ident.  When None, targets the
                   current thread (backward compat for CLI/tests).
    """
    tid = thread_id if thread_id is not None else threading.current_thread().ident
    with _lock:
        if active:
            _interrupted_threads.add(tid)
        else:
            _interrupted_threads.discard(tid)
        _snapshot = set(_interrupted_threads) if _DEBUG_INTERRUPT else None
    if _DEBUG_INTERRUPT:
        logger.info(
            "[interrupt-debug] set_interrupt(active=%s, target_tid=%s) "
            "called_from_tid=%s current_set=%s",
            active, tid, threading.current_thread().ident, _snapshot,
        )


def is_interrupted() -> bool:
    """Check if an interrupt has been requested for the current thread.

    Safe to call from any thread — each thread only sees its own
    interrupt state.
    """
    tid = threading.current_thread().ident
    with _lock:
        return tid in _interrupted_threads


# ---------------------------------------------------------------------------
# 按回合登记工作线程
# ---------------------------------------------------------------------------
# 中断位是按线程记的,但同步工具跑在 asyncio.to_thread 的线程池里
# (vendor/agent_framework/_tools.py:557),事件循环那一侧拿不到那个线程号。
# 所以要工具自己在入口登记「我是 task_id 这一回合的工作线程」,取消回合时才知道
# 往哪个线程打位。线程池会复用线程,退出时必须把位清掉,否则下一个落到同一线程的
# 工具一进来就看到中断。
_task_workers: dict[str, set[int]] = {}


@contextmanager
def worker_thread_of_task(task_id: str) -> Iterator[None]:
    """把当前线程登记为 task_id 的工作线程,退出时注销并清掉它的中断位。"""
    tid = threading.current_thread().ident
    with _lock:
        _task_workers.setdefault(task_id, set()).add(tid)
    try:
        yield
    finally:
        with _lock:
            workers = _task_workers.get(task_id)
            if workers is not None:
                workers.discard(tid)
                if not workers:
                    del _task_workers[task_id]
            _interrupted_threads.discard(tid)


def interrupt_task(task_id: str) -> int:
    """给 task_id 的所有工作线程打中断位,返回打了几个。

    等子进程的轮询循环(environments/base.py 的 _wait_for_process)看到位就杀进程组
    并以 returncode 130 返回,所以命令会在一个轮询间隔内(≤200ms)真正停掉。
    """
    with _lock:
        tids = list(_task_workers.get(task_id) or ())
        _interrupted_threads.update(tids)
    if _DEBUG_INTERRUPT:
        logger.info("[interrupt-debug] interrupt_task(task_id=%s) tids=%s", task_id, tids)
    return len(tids)


def clear_current_thread_interrupt() -> None:
    """Clear any interrupt bit on the CURRENT thread.

    Gives a user-approved command a clean interrupt slate immediately before
    it spawns its child process, so a stale bit that landed on this thread
    during the blocking approval-wait cannot SIGINT the just-approved run
    (exit 130 + "[Command interrupted]").  Single-thread ordering on this tid
    keeps the DO-NOT-BREAK invariant intact: a *genuine* interrupt arriving
    after this call re-sets the bit on the same thread and is still observed by
    the executor's poll loop.  Call this directly, never via the
    _interrupt_event proxy (its .clear() binds to whatever thread runs it).
    """
    set_interrupt(False)  # thread_id=None -> current thread (see set_interrupt)


# ---------------------------------------------------------------------------
# Backward-compatible _interrupt_event proxy
# ---------------------------------------------------------------------------
# Some legacy call sites (code_execution_tool, process_registry, tests)
# import _interrupt_event directly and call .is_set() / .set() / .clear().
# This shim maps those calls to the per-thread functions above so existing
# code keeps working while the underlying mechanism is thread-scoped.

class _ThreadAwareEventProxy:
    """Drop-in proxy that maps threading.Event methods to per-thread state."""

    def is_set(self) -> bool:
        return is_interrupted()

    def set(self) -> None:  # noqa: A003
        set_interrupt(True)

    def clear(self) -> None:
        set_interrupt(False)

    def wait(self, timeout: float | None = None) -> bool:
        """Not truly supported — returns current state immediately."""
        return self.is_set()


_interrupt_event = _ThreadAwareEventProxy()
