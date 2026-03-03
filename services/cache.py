import uuid
import itertools
from dataclasses import dataclass, field
from datetime import datetime
from collections import OrderedDict, deque

from astrbot.api import logger


@dataclass
class ThreadEntry:
    main: str | None = None
    replies: deque[str] = field(default_factory=deque)
    first_ts: datetime = field(default_factory=datetime.now)
    last_ts: datetime | None = None


class StatsTracker:
    def __init__(self):
        self._stats: dict[str, dict[str, int]] = {"hit": 0, "miss": 0}

    @property
    def stats(self) -> dict[str, dict[str, int]]:
        return self._stats

    def bump(self, stub: str, hit: bool):
        stat = self._stats.get(stub, {"hit": 0, "miss": 0})
        if hit:
            stat["hit"] = stat.get("hit", 0) + 1
        else:
            stat["miss"] = stat.get("miss", 0) + 1
        self._stats[stub] = stat

    def get(self, stub: str) -> dict[str, int]:
        return self._stats.get(stub, {"hit": 0, "miss": 0})


class ThreadCache:
    def __init__(
        self,
        *,
        log_debug_exception,
        auto_anchor_counter,
        uuid_suffix_len: int,
        stats_tracker: StatsTracker,
    ):
        self.threads: dict[str, OrderedDict[str, ThreadEntry]] = {}
        self.last_seen: dict[str, datetime] = {}
        self.stats_tracker = stats_tracker
        self._log_debug_exception = log_debug_exception
        self._auto_anchor_counter = auto_anchor_counter
        self._uuid_suffix_len = uuid_suffix_len

    def bump_stat(self, stub: str, hit: bool):
        self.stats_tracker.bump(stub, hit)

    def get_session_lines_unlocked(self, file_stub: str) -> list[str]:
        lines: list[str] = []
        threads = self.threads.get(file_stub, OrderedDict())
        for entry in threads.values():
            if entry.main:
                lines.append(entry.main)
            lines.extend(list(entry.replies))
        return lines

    @staticmethod
    def get_thread_lines(entry: ThreadEntry) -> list[str]:
        lines: list[str] = []
        if entry.main:
            lines.append(entry.main)
        lines.extend(list(entry.replies))
        return lines

    def ensure_threads_session(self, stub: str):
        if stub not in self.threads:
            self.threads[stub] = OrderedDict()

    def enforce_session_count(self, max_sessions: int):
        if len(self.threads) <= max_sessions:
            return
        items = sorted(
            self.last_seen.items(),
            key=lambda item: item[1] if item[1] is not None else datetime.min,
        )
        for stub, _ in items:
            if len(self.threads) <= max_sessions:
                break
            self.threads.pop(stub, None)
            self.last_seen.pop(stub, None)
            self.stats_tracker.stats.pop(stub, None)

    def generate_anchor_key(self, anchor_id: str | int | None) -> str:
        if anchor_id is not None:
            return str(anchor_id)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        counter = next(self._auto_anchor_counter)
        return f"auto-{ts}-{counter}-{uuid.uuid4().hex[: self._uuid_suffix_len]}"

    def get_or_create_thread_entry(self, stub: str, key: str) -> ThreadEntry:
        self.ensure_threads_session(stub)
        threads = self.threads[stub]
        if key not in threads:
            threads[key] = ThreadEntry()
        return threads[key]

    def append_thread_reply(self, entry: ThreadEntry, stub: str, key: str, line: str):
        entry.replies.append(line)
        entry.last_ts = datetime.now()
        try:
            self.threads.get(stub, OrderedDict()).move_to_end(key)
        except KeyError:
            pass
        try:
            logger.debug(
                "QQRecord 追加回复：累计=%d",
                len(entry.replies),
            )
        except Exception as exc:
            self._log_debug_exception("QQRecord 追加回复日志失败", exc)

    def set_thread_main_or_reply(self, entry: ThreadEntry, stub: str, key: str, line: str):
        if entry.main is None:
            entry.main = line
            entry.last_ts = datetime.now()
            try:
                self.threads.get(stub, OrderedDict()).move_to_end(key)
            except KeyError:
                pass
            try:
                logger.debug("QQRecord 新建线程主消息")
            except Exception as exc:
                self._log_debug_exception("QQRecord 主消息日志失败", exc)
            return
        self.append_thread_reply(entry, stub, key, line)

    def enforce_session_capacity(self, stub: str, cache_limit: int):
        threads = self.threads.get(stub)
        if not threads:
            return

        def _entry_last_ts(entry: ThreadEntry) -> datetime:
            return entry.last_ts or entry.first_ts or datetime.min

        def _count():
            total = 0
            for entry in threads.values():
                total += 1 if entry.main else 0
                total += len(entry.replies)
            return total

        def _trim_oldest():
            if not threads:
                return 0
            oldest_key = min(
                threads.items(),
                key=lambda item: _entry_last_ts(item[1]),
            )[0]
            entry = threads.get(oldest_key)
            if not entry:
                threads.pop(oldest_key, None)
                return 0
            removed_lines = 0
            if entry.main is not None:
                entry.main = None
                removed_lines += 1
                if entry.replies:
                    entry.main = entry.replies.popleft()
                if entry.main is None and not entry.replies:
                    threads.pop(oldest_key, None)
                return removed_lines
            if entry.replies:
                entry.replies.popleft()
                removed_lines += 1
                if entry.main is None and not entry.replies:
                    threads.pop(oldest_key, None)
                return removed_lines
            threads.pop(oldest_key, None)
            return removed_lines

        total = _count()
        safety = total + len(threads) + 1
        while total > cache_limit and threads and safety > 0:
            trimmed = _trim_oldest()
            total -= trimmed
            safety -= 1

    def write_threaded(
        self,
        *,
        stub: str,
        anchor_id: str | int | None,
        is_reply: bool,
        line: str,
        cache_limit: int,
        max_sessions: int,
    ):
        key = self.generate_anchor_key(anchor_id)
        entry = self.get_or_create_thread_entry(stub, key)
        if is_reply:
            self.append_thread_reply(entry, stub, key, line)
        else:
            self.set_thread_main_or_reply(entry, stub, key, line)
        self.last_seen[stub] = datetime.now()
        self.enforce_session_capacity(stub, cache_limit)
        self.enforce_session_count(max_sessions)

    def all_session_keys(self) -> set[str]:
        return set(self.threads.keys()) | set(self.last_seen.keys())
