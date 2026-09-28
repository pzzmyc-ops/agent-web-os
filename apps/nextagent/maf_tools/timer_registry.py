"""定时任务注册表 —— 持久化的「到点唤起 agent」表。

每条定时任务 = 一个触发时机 + 一条**写给到点时的 agent 自己的指令**(message)。
到点后 server 的 ticker 会用 message 当提示词跑完整一轮(见 recall_runner.run_recall),
用户看到的是 agent 那一轮的回复,不是 message 原文。

对 agent 暴露的是结构化时机(repeat/at/weekday/date/every_minutes),**cron 只是本模块
内部的实现细节** —— agent 不需要、也不应该知道 cron 长什么样。

存储为 JSON 文件(data/hermes/schedule.json),和 processes.json 同目录同模式。
CRUD 都是同步的(文件很小,没性能问题),由 server 级的 _cron_ticker 驱动。
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

from ._constants import get_hermes_home

Schedules_PATH = get_hermes_home() / "schedule.json"

#: 触发落后于计划时间超过这么多秒就不补跑了,只把 next_at 推到下一次。
#: 场景:服务停机三天后启动,「每天 09:00」不该在下午两点连炸三次。
DEFAULT_GRACE_SECONDS = 600.0


# ---------------------------------------------------------------------------
# Cron(内部实现细节,不对 agent 暴露)
# ---------------------------------------------------------------------------

_CRON_FIELD_NAMES = ("minute", "hour", "day", "month", "weekday")
_CRON_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
_CRON_ALIASES = {
    "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *", "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}


def _parse_cron_field(raw: str, lo: int, hi: int) -> set[int]:
    """Parse a single cron field (minute / hour / day / month / weekday)."""
    if raw == "*":
        return set(range(lo, hi + 1))
    result: set[int] = set()
    for part in raw.split(","):
        step = 1
        if "/" in part:
            part, step_str = part.split("/", 1)
            step = int(step_str)
        if part == "*":
            result.update(range(lo, hi + 1, step))
        elif "-" in part:
            a, b = part.split("-", 1)
            result.update(range(int(a), int(b) + 1, step))
        else:
            v = int(part)
            if lo <= v <= hi:
                result.add(v)
    return result


def _next_cron(cron: str, after: float | None = None) -> float:
    """Return the NEXT match after *after*(default now) as a unix timestamp.

    标准 5 字段(minute hour day month weekday),外加 @daily 等别名。
    不支持 L / W / # / ? 这些扩展 —— 内部用不到。
    """
    expr = _CRON_ALIASES.get(cron.strip().lower(), cron.strip())
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"cron 表达式需要 5 个字段,收到 {len(fields)}: {cron!r}")

    sets = []
    for i, raw in enumerate(fields):
        lo, hi = _CRON_RANGES[i]
        s = _parse_cron_field(raw, lo, hi)
        if not s:
            raise ValueError(
                f"cron 字段 {_CRON_FIELD_NAMES[i]}={raw!r} 匹配不到值"
            )
        sets.append(s)

    # Walk forward minute by minute (max ~4 years for * * * * *, but typically
    # seconds in practice — this is a ticker that fires every 20s, not a
    # high-frequency scheduler).
    t = time.localtime(after) if after is not None else time.localtime()
    ts = int(time.mktime((
        t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, 0, 0, 0, -1,
    ))) + 60

    for _ in range(525600):  # safety cap: ~1 year of minutes
        dt = time.localtime(ts)
        minute, hour, mday, month, wday = dt.tm_min, dt.tm_hour, dt.tm_mday, dt.tm_mon, dt.tm_wday
        cron_wday = (wday + 1) % 7  # Python tm_wday(0=Mon) → cron wday(0=Sun)
        if (
            minute in sets[0]
            and hour in sets[1]
            and mday in sets[2]
            and month in sets[3]
            and cron_wday in sets[4]
        ):
            return float(ts)
        ts += 60

    raise ValueError(f"cron {cron!r}: 一年内找不到下次触发时间")


# ---------------------------------------------------------------------------
# 触发时机(agent 面向的这一层)
# ---------------------------------------------------------------------------

REPEATS = ("once", "hourly", "daily", "weekly", "interval")

#: 星期名 → cron weekday(0=周日)。中英都收,模型两种都可能写。
_WEEKDAYS = {
    "mon": 1, "monday": 1, "周一": 1, "星期一": 1, "1": 1,
    "tue": 2, "tuesday": 2, "周二": 2, "星期二": 2, "2": 2,
    "wed": 3, "wednesday": 3, "周三": 3, "星期三": 3, "3": 3,
    "thu": 4, "thursday": 4, "周四": 4, "星期四": 4, "4": 4,
    "fri": 5, "friday": 5, "周五": 5, "星期五": 5, "5": 5,
    "sat": 6, "saturday": 6, "周六": 6, "星期六": 6, "6": 6,
    "sun": 0, "sunday": 0, "周日": 0, "星期日": 0, "星期天": 0, "0": 0, "7": 0,
}
_CRON_WDAY_CN = {0: "周日", 1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六"}

MAX_INTERVAL_MINUTES = 1440


def _parse_hhmm(at: str, *, minute_only_ok: bool = False) -> tuple[int, int]:
    """"09:00" → (9, 0)。minute_only_ok 时允许裸分钟("5" → (0, 5))。"""
    raw = str(at or "").strip().replace("：", ":")
    if not raw:
        raise ValueError('at 不能为空,格式 "HH:MM"(24 小时制),例 at="09:00"')
    if ":" not in raw:
        if not minute_only_ok:
            raise ValueError(f'at={at!r} 格式不对,要 "HH:MM"(24 小时制),例 at="09:00"')
        try:
            m = int(raw)
        except ValueError:
            raise ValueError(f'at={at!r} 看不懂,repeat="hourly" 时写分钟数,例 at="05" 或 at="00:05"') from None
        if not 0 <= m <= 59:
            raise ValueError(f"at={at!r} 的分钟要在 0-59 之间")
        return 0, m
    h_raw, m_raw = raw.split(":", 1)
    try:
        h, m = int(h_raw), int(m_raw)
    except ValueError:
        raise ValueError(f'at={at!r} 格式不对,要 "HH:MM"(24 小时制),例 at="09:00"') from None
    if not 0 <= h <= 23:
        raise ValueError(f"at={at!r} 的小时要在 0-23 之间(24 小时制,下午 3 点写 15:00)")
    if not 0 <= m <= 59:
        raise ValueError(f"at={at!r} 的分钟要在 0-59 之间")
    return h, m


def _parse_date(date: str) -> tuple[int, int, int]:
    raw = str(date or "").strip().replace("/", "-")
    if not raw:
        raise ValueError('repeat="once" 时 date 必填,格式 "YYYY-MM-DD",例 date="2026-08-12"')
    parts = raw.split("-")
    if len(parts) != 3:
        raise ValueError(f'date={date!r} 格式不对,要 "YYYY-MM-DD",例 date="2026-08-12"')
    try:
        y, mo, d = (int(p) for p in parts)
    except ValueError:
        raise ValueError(f'date={date!r} 格式不对,要 "YYYY-MM-DD",例 date="2026-08-12"') from None
    return y, mo, d


@dataclass(frozen=True)
class Spec:
    """一个规范化后的触发时机。cron 为空表示不靠 cron 推进(once / interval)。"""

    repeat: str
    when_text: str
    next_at: float
    cron: str = ""
    at: str = ""
    weekday: str = ""
    date: str = ""
    interval_minutes: int = 0


def build_spec(
    repeat: str,
    *,
    at: str = "",
    weekday: str = "",
    date: str = "",
    every_minutes: int = 0,
    now: float | None = None,
) -> Spec:
    """把 agent 填的结构化时机规范化成 Spec。参数不对时抛 ValueError,消息是**能照着改对**的话。"""
    rep = str(repeat or "").strip().lower()
    if rep not in REPEATS:
        raise ValueError(
            f"repeat={repeat!r} 不认识。可用: " + " / ".join(REPEATS)
            + '(once=只跑一次, hourly=每小时, daily=每天, weekly=每周, interval=每 N 分钟)'
        )
    now_ts = time.time() if now is None else now

    if rep == "interval":
        try:
            n = int(every_minutes)
        except (TypeError, ValueError):
            raise ValueError('repeat="interval" 时 every_minutes 要是整数分钟,例 every_minutes=10') from None
        if not 1 <= n <= MAX_INTERVAL_MINUTES:
            raise ValueError(
                f"every_minutes={every_minutes!r} 超范围,要在 1-{MAX_INTERVAL_MINUTES} 之间"
                "(超过一天的间隔请用 daily/weekly)"
            )
        return Spec(
            repeat=rep, when_text=f"每 {n} 分钟", next_at=now_ts + n * 60,
            interval_minutes=n,
        )

    if rep == "once":
        y, mo, d = _parse_date(date)
        h, m = _parse_hhmm(at)
        try:
            ts = time.mktime((y, mo, d, h, m, 0, 0, 0, -1))
        except (OverflowError, ValueError):
            raise ValueError(f"date={date!r} at={at!r} 不是一个合法时间") from None
        if ts <= now_ts:
            raise ValueError(
                f"{y:04d}-{mo:02d}-{d:02d} {h:02d}:{m:02d} 已经过去了,"
                f"once 的时间必须在将来(现在是 {time.strftime('%Y-%m-%d %H:%M', time.localtime(now_ts))})"
            )
        return Spec(
            repeat=rep, when_text=f"{y:04d}-{mo:02d}-{d:02d} {h:02d}:{m:02d}(仅一次)",
            next_at=ts, at=f"{h:02d}:{m:02d}", date=f"{y:04d}-{mo:02d}-{d:02d}",
        )

    if rep == "hourly":
        _, m = _parse_hhmm(at or "00", minute_only_ok=True)
        cron = f"{m} * * * *"
        return Spec(
            repeat=rep, when_text=f"每小时第 {m} 分钟", next_at=_next_cron(cron, after=now_ts),
            cron=cron, at=f"{m:02d}",
        )

    if rep == "daily":
        h, m = _parse_hhmm(at)
        cron = f"{m} {h} * * *"
        return Spec(
            repeat=rep, when_text=f"每天 {h:02d}:{m:02d}", next_at=_next_cron(cron, after=now_ts),
            cron=cron, at=f"{h:02d}:{m:02d}",
        )

    # weekly
    wd_raw = str(weekday or "").strip().lower()
    if not wd_raw:
        raise ValueError('repeat="weekly" 时 weekday 必填,可用 mon/tue/wed/thu/fri/sat/sun,例 weekday="mon"')
    if wd_raw not in _WEEKDAYS:
        raise ValueError(f'weekday={weekday!r} 不认识,可用 mon/tue/wed/thu/fri/sat/sun,例 weekday="mon"')
    wd = _WEEKDAYS[wd_raw]
    h, m = _parse_hhmm(at)
    cron = f"{m} {h} * * {wd}"
    return Spec(
        repeat="weekly", when_text=f"每{_CRON_WDAY_CN[wd]} {h:02d}:{m:02d}",
        next_at=_next_cron(cron, after=now_ts), cron=cron,
        at=f"{h:02d}:{m:02d}", weekday=wd_raw,
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

@dataclass
class Timer:
    id: str
    thread_id: str
    message: str
    #: once / hourly / daily / weekly / interval,以及历史遗留的 "cron"
    repeat: str = "cron"
    when_text: str = ""
    #: 内部实现细节:daily/weekly/hourly/legacy 靠它算下一次;once/interval 为空
    cron: str = ""
    at: str = ""
    weekday: str = ""
    date: str = ""
    interval_minutes: int = 0
    mode: str = "agent"
    command: str = ""
    created_at: float = field(default_factory=time.time)
    last_at: Optional[float] = None
    next_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "message": self.message,
            "repeat": self.repeat,
            "when_text": self.when_text,
            "cron": self.cron,
            "at": self.at,
            "weekday": self.weekday,
            "date": self.date,
            "interval_minutes": self.interval_minutes,
            "mode": self.mode,
            "command": self.command,
            "created_at": self.created_at,
            "last_at": self.last_at,
            "next_at": self.next_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Timer":
        cron = str(d.get("cron") or "")
        # 老格式只有 cron + message。标成 repeat="cron",继续按 cron 推进,能 list / 能触发 / 能取消。
        repeat = str(d.get("repeat") or ("cron" if cron else "")) or "cron"
        when_text = str(d.get("when_text") or "")
        if not when_text:
            when_text = f"cron: {cron}" if cron else "—"
        return cls(
            id=d["id"],
            thread_id=d["thread_id"],
            message=d["message"],
            repeat=repeat,
            when_text=when_text,
            cron=cron,
            at=str(d.get("at") or ""),
            weekday=str(d.get("weekday") or ""),
            date=str(d.get("date") or ""),
            interval_minutes=int(d.get("interval_minutes") or 0),
            mode=str(d.get("mode") or "agent").strip().lower(),
            command=str(d.get("command") or ""),
            created_at=d.get("created_at", 0.0),
            last_at=d.get("last_at"),
            next_at=d.get("next_at", 0.0),
        )


class TimerRegistry:
    """进程级定时任务注册表,持久化到 schedule.json。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[str, Timer] = {}
        self._loaded = False

    def _load(self) -> dict[str, Timer]:
        if self._loaded:
            return self._cache
        try:
            raw = json.loads(Schedules_PATH.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        timers = raw.get("timers", {})
        parsed: dict[str, Timer] = {}
        for k, v in timers.items():
            try:
                parsed[k] = Timer.from_dict(v)
            except Exception:
                continue  # 一条坏记录不该让整张表加载失败
        self._cache = parsed
        self._loaded = True
        return self._cache

    def _save(self) -> None:
        Schedules_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {"timers": {k: v.to_dict() for k, v in self._cache.items()}}
        Schedules_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # -- public API --

    def create(
        self,
        *,
        thread_id: str,
        message: str,
        repeat: str,
        at: str = "",
        weekday: str = "",
        date: str = "",
        every_minutes: int = 0,
        mode: str = "agent",
        command: str = "",
    ) -> Timer:
        m = str(mode or "agent").strip().lower()
        if m not in ("agent", "program", "exec"):
            raise ValueError(f"mode={mode!r} 不认识。可用: agent / program / exec")
        cmd = str(command or "").strip()
        msg = str(message or "").strip()
        if m == "exec":
            if not cmd:
                raise ValueError("command 不能为空 —— 它是到点时在工作区里执行的命令")
            if not msg:
                msg = cmd
        elif not msg:
            if m == "program":
                raise ValueError("message 不能为空 —— 它是到点时写进本对话的正文，可用 {now} 表示当时时间")
            raise ValueError("message 不能为空 —— 它是到点时用来唤起你自己的指令")
        spec = build_spec(
            repeat, at=at, weekday=weekday, date=date, every_minutes=every_minutes,
        )
        tid = f"timer_{uuid.uuid4().hex[:12]}"
        timer = Timer(
            id=tid, thread_id=thread_id, message=msg,
            repeat=spec.repeat, when_text=spec.when_text, cron=spec.cron,
            at=spec.at, weekday=spec.weekday, date=spec.date,
            interval_minutes=spec.interval_minutes, next_at=spec.next_at,
            mode=m, command=cmd,
        )
        with self._lock:
            self._load()
            self._cache[tid] = timer
            self._save()
        return timer

    def list_all(self) -> list[Timer]:
        with self._lock:
            return sorted(
                self._load().values(),
                key=lambda t: (t.next_at or 0, t.created_at or 0),
            )

    def list_for_thread(self, thread_id: str) -> list[Timer]:
        with self._lock:
            return sorted(
                (t for t in self._load().values() if t.thread_id == thread_id),
                key=lambda t: (t.next_at or 0, t.created_at or 0),
            )

    def cancel(self, timer_id: str, *, thread_id: str | None = None) -> bool:
        with self._lock:
            self._load()
            t = self._cache.get(timer_id)
            if t is None:
                return False
            if thread_id is not None and t.thread_id != thread_id:
                return False
            del self._cache[timer_id]
            self._save()
            return True

    def cancel_for_thread(self, thread_id: str) -> int:
        tid = str(thread_id or "").strip()
        if not tid:
            raise RuntimeError("thread_id 不能为空")
        with self._lock:
            self._load()
            ids = [k for k, t in self._cache.items() if t.thread_id == tid]
            if not ids:
                return 0
            for k in ids:
                del self._cache[k]
            self._save()
            return len(ids)

    def get(self, timer_id: str) -> Timer | None:
        with self._lock:
            return self._load().get(timer_id)

    def claim_due(
        self, *, grace: float = DEFAULT_GRACE_SECONDS, now: float | None = None
    ) -> tuple[list[Timer], list[Timer]]:
        """认领所有到点的定时任务:推进它们的 next_at,返回 (要跑的, 过期跳过的)。

        「认领」是一步完成的 —— 取出来的同时 next_at 就已经推到下一次并落盘,所以
        ticker 不会因为一轮跑得久而重复触发同一次。

        落后计划时间超过 *grace* 的不跑(服务停机期间错过的触发不补),只推进 next_at。
        """
        now_ts = time.time() if now is None else now
        due: list[Timer] = []
        skipped: list[Timer] = []
        with self._lock:
            self._load()
            dirty = False
            for t in list(self._cache.values()):
                if not t.next_at or t.next_at > now_ts:
                    continue
                snapshot = replace(t)  # 推进前的样子给调用方(next_at = 本次计划时间)
                self._advance_locked(t, now=now_ts)
                dirty = True
                if now_ts - snapshot.next_at > grace:
                    skipped.append(snapshot)
                else:
                    due.append(snapshot)
            if dirty:
                self._save()
        return due, skipped

    def advance(self, timer_id: str, *, now: float | None = None) -> bool:
        """推进一个定时任务到下一次。返回 False 表示它不会再触发了(once 已跑完,已删除)。"""
        now_ts = time.time() if now is None else now
        with self._lock:
            self._load()
            t = self._cache.get(timer_id)
            if t is None:
                return False
            alive = self._advance_locked(t, now=now_ts)
            self._save()
            return alive

    # -- internals(调用方必须已持有 self._lock)--

    def _advance_locked(self, t: Timer, *, now: float) -> bool:
        t.last_at = now
        if t.repeat == "once":
            self._cache.pop(t.id, None)  # 只跑一次,跑完就没了
            return False
        if t.repeat == "interval":
            t.next_at = now + max(1, t.interval_minutes) * 60
            return True
        try:
            t.next_at = _next_cron(t.cron, after=now)
        except Exception:
            # cron 坏了(手改过文件之类)→ 停掉它,别让 ticker 每轮都撞同一个异常
            t.next_at = 0.0
            return False
        return True


timer_registry = TimerRegistry()


# ---------------------------------------------------------------------------
# 到点时的唤醒提示词
# ---------------------------------------------------------------------------

def build_wakeup_prompt(timer: Timer, *, now: float | None = None) -> tuple[str, str]:
    """→ (发给模型的 prompt, 前端 recall 卡片显示的正文)。

    卡片显示 message 原文(用户想看的是「这个定时任务是干什么的」),
    prompt 则在 message 外面套一层交代,让 agent 知道:是定时任务在叫它、
    这条指令是它自己当初写给自己的、现在该直接对用户说话。

    没有这层交代,agent 醒来看到的就是一句裸消息,和用户发来的话无法区分。
    """
    now_ts = time.time() if now is None else now
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(now_ts))
    prompt = (
        f"[定时任务触发] 现在是 {stamp},你之前创建的定时任务({timer.when_text})到点了。\n"
        f"你当时写给「到点时的自己」的指令是:\n"
        f"{timer.message}\n"
        f"按这条指令行动。可以正常调用工具。直接对用户说话 —— "
        f"不要复述这段方括号里的交代,也不要跟用户解释「定时任务触发了」这类系统细节。"
    )
    return prompt, timer.message


def render_program_text(timer: Timer, *, now: float | None = None) -> str:
    now_ts = time.time() if now is None else now
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts))
    return timer.message.replace("{now}", stamp)
