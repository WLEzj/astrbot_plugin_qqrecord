import asyncio
import re
import os
import uuid
import itertools
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict, deque, OrderedDict

from astrbot.api import logger, sp
from astrbot.api.event import MessageChain
from astrbot.api.event import filter
from astrbot.api.event.filter import EventMessageType, PlatformAdapterType
from astrbot.api.star import Context, Star
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.message.components import File, Reply, BaseMessageComponent
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path


@dataclass
class ThreadEntry:
    main: str | None = None
    replies: deque[str] = field(default_factory=deque)
    first_ts: datetime = field(default_factory=datetime.now)
    last_ts: datetime | None = None


class ThreadCache:
    def __init__(
        self,
        *,
        log_debug_exception,
        auto_anchor_counter,
        uuid_suffix_len: int,
        stats_tracker: "StatsTracker",
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


class StatsTracker:
    def __init__(self):
        self._stats: defaultdict[str, dict[str, int]] = defaultdict(lambda: {"hit": 0, "miss": 0})

    @property
    def stats(self) -> defaultdict[str, dict[str, int]]:
        return self._stats

    def bump(self, stub: str, hit: bool):
        stat = self._stats[stub]
        if hit:
            stat["hit"] = stat.get("hit", 0) + 1
        else:
            stat["miss"] = stat.get("miss", 0) + 1

    def get(self, stub: str) -> dict[str, int]:
        return self._stats.get(stub, {"hit": 0, "miss": 0})


class Exporter:
    def __init__(
        self,
        *,
        is_safe_path,
        temp_dir_provider,
        temp_file_prefix: str,
        split_text_segments,
        log_debug_exception,
    ):
        self._is_safe_path = is_safe_path
        self._temp_dir_provider = temp_dir_provider
        self._temp_file_prefix = temp_file_prefix
        self._split_text_segments = split_text_segments
        self._log_debug_exception = log_debug_exception

    async def send_cache_as_file(
        self,
        event: AstrMessageEvent,
        lines: list[str],
        name: str,
        file_stub: str,
        *,
        segment_len: int,
        segment_delay: float,
    ):
        """将缓存行写入临时文件并以文件消息发送，随后删除临时文件。

        文件名采用 {TEMP_FILE_PREFIX}<stub>-<timestamp>.txt，写入 UTF-8 文本。
        """
        if not lines:
            await event.send(
                MessageChain(chain=[f"缓存为空，当前会话：{name}"])
            )
            return

        safe_stub = (file_stub or "unknown").strip()
        now = datetime.now()
        ts = now.strftime("%Y%m%d-%H%M%S-%f")
        fname = f"{self._temp_file_prefix}{safe_stub}-{ts}-{uuid.uuid4().hex}.txt"
        temp_dir = self._temp_dir_provider()
        os.makedirs(temp_dir, exist_ok=True)
        fpath = os.path.join(temp_dir, fname)
        final_path = os.path.normpath(fpath)
        if not self._is_safe_path(temp_dir, final_path):
            logger.warning("QQRecord 临时文件路径非法: %s", final_path)
            await event.send(
                MessageChain(chain=["导出失败：临时文件路径不安全。请稍后重试。"])
            )
            return

        content = "\n".join(lines)
        try:
            with open(final_path, "w", encoding="utf-8") as fp:
                fp.write(content)

            await event.send(
                MessageChain(chain=[File(name=fname, file=final_path)])
            )
        except Exception as exc:
            logger.warning("QQRecord 文件发送失败，回退为文本：%s", exc)
            await self.send_text_segments(
                event,
                f"记录文件发送失败（可能未配置回调地址或平台不支持文件），改为文本：\n{content}",
                segment_len=segment_len,
                delay=segment_delay,
            )
        finally:
            try:
                if os.path.exists(final_path):
                    os.remove(final_path)
            except OSError as exc:
                logger.warning("临时文件清理失败 %s: %s", final_path, exc)

    async def send_text_segments(
        self,
        event: AstrMessageEvent,
        content: str,
        *,
        segment_len: int,
        delay: float,
    ):
        """将长文本按段发送，避免平台消息长度限制。"""
        try:
            text = content or ""
            if not text:
                return
            for seg in self._split_text_segments(text, segment_len):
                await event.send(MessageChain(chain=[seg]))
                if delay > 0:
                    await asyncio.sleep(delay)
        except Exception as exc:
            logger.warning("QQRecord 文本分段发送失败: %s", exc, exc_info=exc)


class CleanupScheduler:
    def __init__(
        self,
        *,
        thread_cache: ThreadCache,
        is_safe_path,
        temp_dir_provider,
        temp_file_prefix: str,
        cleanup_hour: int,
        cleanup_minute: int,
        cleanup_preview_limit: int,
        cleanup_backoff_seconds: int,
        cleanup_backoff_max_seconds: int,
        log_debug_exception,
    ):
        self._thread_cache = thread_cache
        self._is_safe_path = is_safe_path
        self._temp_dir_provider = temp_dir_provider
        self._temp_file_prefix = temp_file_prefix
        self._cleanup_hour = cleanup_hour
        self._cleanup_minute = cleanup_minute
        self._cleanup_preview_limit = cleanup_preview_limit
        self._cleanup_backoff_seconds = cleanup_backoff_seconds
        self._cleanup_backoff_max_seconds = cleanup_backoff_max_seconds
        self._log_debug_exception = log_debug_exception
        self._task: asyncio.Task | None = None

    def start(self, *, max_age_hours: int, temp_cleanup_hours: int):
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(
            self._cleanup_loop(
                max_age_hours=max_age_hours,
                temp_cleanup_hours=temp_cleanup_hours,
            )
        )

    async def stop(self):
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    def seconds_until_next_6am(self) -> float:
        now = datetime.now()
        next_run = now.replace(
            hour=self._cleanup_hour,
            minute=self._cleanup_minute,
            second=0,
            microsecond=0,
        )
        if now >= next_run:
            next_run += timedelta(days=1)
        return max(1.0, (next_run - now).total_seconds())

    async def _cleanup_loop(self, *, max_age_hours: int, temp_cleanup_hours: int):
        failures = 0
        while True:
            try:
                delay = self.seconds_until_next_6am()
                await asyncio.sleep(delay)
                await self.cleanup_inactive(max_age_hours=max_age_hours)
                await self.cleanup_temp_files(max_age_hours=temp_cleanup_hours)
                failures = 0
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("QQRecord 清理循环异常: %s", exc)
                failures += 1
                backoff = min(
                    self._cleanup_backoff_seconds * (2 ** min(failures - 1, 5)),
                    self._cleanup_backoff_max_seconds,
                )
                await asyncio.sleep(backoff)

    async def cleanup_temp_files(self, max_age_hours: int):
        """清理过期的临时导出文件，避免目录堆积。"""
        try:
            temp_dir = self._temp_dir_provider()
            if not temp_dir or not os.path.isdir(temp_dir):
                return
            cutoff = datetime.now().timestamp() - (max_age_hours * 3600)
            removed = 0
            for name in os.listdir(temp_dir):
                if not name.startswith(self._temp_file_prefix):
                    continue
                path = os.path.join(temp_dir, name)
                if not self._is_safe_path(temp_dir, path):
                    continue
                try:
                    if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                        os.remove(path)
                        removed += 1
                except OSError as exc:
                    self._log_debug_exception("QQRecord 清理临时文件失败", exc, path=path)
            if removed:
                logger.info("QQRecord 临时文件清理完成：%s 个", removed)
        except (OSError, ValueError) as exc:
            self._log_debug_exception("QQRecord 临时文件清理异常", exc)

    async def cleanup_inactive(self, max_age_hours: int):
        """清理长时间未更新的会话缓存键，默认 24 小时未活动即清理。"""
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        removed = 0
        removed_keys: list[str] = []
        for key in sorted(self._thread_cache.all_session_keys()):
            last = self._thread_cache.last_seen.get(key)
            if last is None or last < cutoff:
                try:
                    self._thread_cache.last_seen.pop(key, None)
                    self._thread_cache.threads.pop(key, None)
                    self._thread_cache.stats_tracker.stats.pop(key, None)
                    removed += 1
                    removed_keys.append(key)
                except Exception as exc:
                    self._log_debug_exception(
                        "QQRecord 清理会话失败",
                        exc,
                        stub=key,
                    )
        if removed:
            try:
                preview = ", ".join(removed_keys[: self._cleanup_preview_limit])
                more = "..." if len(removed_keys) > self._cleanup_preview_limit else ""
                now = datetime.now()
                next_run = now.replace(
                    hour=self._cleanup_hour,
                    minute=self._cleanup_minute,
                    second=0,
                    microsecond=0,
                )
                if now >= next_run:
                    next_run += timedelta(days=1)
                next_str = next_run.strftime("%Y-%m-%d %H:%M:%S")
                logger.info(
                    "QQRecord 每日清理完成：移除不活跃会话 %s 项（阈值 %s 小时）。"
                    "示例：%s%s；下次预计 %s 运行",
                    removed,
                    max_age_hours,
                    preview,
                    more,
                    next_str,
                )
            except Exception:
                logger.info(
                    "QQRecord 每日清理完成：移除不活跃会话 %s 项（阈值 %s 小时）",
                    removed,
                    max_age_hours,
                )


class ConfigStore:
    def __init__(self, *, plugin, plugin_id: str, log_debug_exception):
        self._plugin = plugin
        self._plugin_id = plugin_id
        self._log_debug_exception = log_debug_exception

    async def get(self, key: str, default):
        getter = getattr(self._plugin, "get_kv_data", None)
        if callable(getter):
            try:
                return await getter(key, default)
            except (AttributeError, TypeError, RuntimeError) as exc:
                self._log_debug_exception("QQRecord KV 读取异常", exc, key=key)
        try:
            return await sp.get_async("plugin", self._plugin_id, key, default)
        except Exception as exc:
            self._log_debug_exception("QQRecord KV 读取异常", exc, key=key)
            return default

    async def put(self, key: str, value):
        setter = getattr(self._plugin, "put_kv_data", None)
        if callable(setter):
            try:
                await setter(key, value)
                return
            except (AttributeError, TypeError, RuntimeError) as exc:
                self._log_debug_exception("QQRecord KV 写入异常", exc, key=key)
        try:
            await sp.put_async("plugin", self._plugin_id, key, value)
        except Exception as exc:
            self._log_debug_exception("QQRecord KV 写入异常", exc, key=key)


class QQRecordPlugin(Star):
    """记录 QQ 会话消息到内存缓存（默认每会话最近 500 条）。"""

    SANITIZE_PATTERN = re.compile(r"[^0-9A-Za-z_-]+")
    URL_PATTERN = re.compile(r"https?://\S+")
    WHITESPACE_SPLIT_PATTERN = re.compile(r"(\s+)")
    DEFAULT_CACHE_LIMIT = 500
    MAX_CACHE_LIMIT = 1000
    DEFAULT_CLEANUP_HOURS = 24
    DEFAULT_SEGMENT_LEN = 1000
    DEFAULT_SEGMENT_DELAY = 0.5
    TEMP_FILE_PREFIX = "qqrecord-"
    TEMP_CLEANUP_HOURS = 24
    MAX_SESSIONS = 500
    CLEANUP_BACKOFF_SECONDS = 60
    CLEANUP_BACKOFF_MAX_SECONDS = 3600
    CLEANUP_HOUR = 6
    CLEANUP_MINUTE = 0
    CLEANUP_PREVIEW_LIMIT = 10
    THREAD_MAIN_PREVIEW_LIMIT = 40
    UUID_SUFFIX_LEN = 8
    HIT_RATE_MULTIPLIER = 100
    _AUTO_ANCHOR_COUNTER = itertools.count(1)

    def __init__(self, context: Context):
        super().__init__(context)
        # 用于插件KV存储的标识
        self.plugin_id = "astrbot_plugin_qqrecord"
        self._cache_enabled: bool = True
        self._cache_limit: int = self.DEFAULT_CACHE_LIMIT
        self._admin_only: bool = False
        self._export_admin_only: bool = False

        self._stats_tracker = StatsTracker()
        self._thread_cache = ThreadCache(
            log_debug_exception=self._log_debug_exception,
            auto_anchor_counter=self._AUTO_ANCHOR_COUNTER,
            uuid_suffix_len=self.UUID_SUFFIX_LEN,
            stats_tracker=self._stats_tracker,
        )
        self._config_store = ConfigStore(
            plugin=self,
            plugin_id=self.plugin_id,
            log_debug_exception=self._log_debug_exception,
        )
        self._exporter = Exporter(
            is_safe_path=self._is_safe_path,
            temp_dir_provider=get_astrbot_temp_path,
            temp_file_prefix=self.TEMP_FILE_PREFIX,
            split_text_segments=self._split_text_segments,
            log_debug_exception=self._log_debug_exception,
        )
        self._cleanup_scheduler = CleanupScheduler(
            thread_cache=self._thread_cache,
            is_safe_path=self._is_safe_path,
            temp_dir_provider=get_astrbot_temp_path,
            temp_file_prefix=self.TEMP_FILE_PREFIX,
            cleanup_hour=self.CLEANUP_HOUR,
            cleanup_minute=self.CLEANUP_MINUTE,
            cleanup_preview_limit=self.CLEANUP_PREVIEW_LIMIT,
            cleanup_backoff_seconds=self.CLEANUP_BACKOFF_SECONDS,
            cleanup_backoff_max_seconds=self.CLEANUP_BACKOFF_MAX_SECONDS,
            log_debug_exception=self._log_debug_exception,
        )
        # 线程化缓存：按会话维护锚点到线程的数据结构（保持插入有序）
        self._threads = self._thread_cache.threads
        self._last_seen = self._thread_cache.last_seen
        self._write_lock = asyncio.Lock()
        # 访问统计：按会话记录读取命中/未命中次数
        self._stats = self._stats_tracker.stats

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        """尝试判断是否管理员；若平台不支持该判断，则默认拒绝。"""
        try:
            # 优先使用事件对象上的权限判断方法（如存在）
            if hasattr(event, "is_admin"):
                return bool(event.is_admin())
            if hasattr(event, "is_group_admin"):
                return bool(event.is_group_admin())
            if hasattr(event, "is_super_user"):
                return bool(event.is_super_user())
        except (AttributeError, TypeError, RuntimeError) as exc:
            self._log_debug_exception("QQRecord 管理员判断异常", exc)
        return False

    @staticmethod
    def _admin_check_notice() -> str:
        return "平台不支持管理员判断时会默认拒绝。"

    def _admin_denied_message(self, event: AstrMessageEvent, required: bool, base_message: str) -> str | None:
        if required and not self._is_admin(event):
            return f"{base_message}{self._admin_check_notice()}"
        return None

    def _cache_disabled_message(self) -> str:
        return "缓存未启用，请先 /record_cache on"

    def _clamp_limit(self, limit: int) -> int:
        return max(1, min(limit, self._cache_limit))

    async def _collect_session_status(self, file_stub: str) -> tuple[int, int, int, int, int, float]:
        async with self._write_lock:
            session_lines = len(self._get_session_lines_unlocked(file_stub))
            threads_cnt = len(self._threads.get(file_stub, OrderedDict()))
            total_sessions = len(self._all_session_keys())
            stat = self._stats_tracker.get(file_stub)
        hit = stat.get("hit", 0)
        miss = stat.get("miss", 0)
        total_req = hit + miss
        hit_rate = (hit / total_req * self.HIT_RATE_MULTIPLIER) if total_req else 0.0
        return session_lines, threads_cnt, total_sessions, hit, miss, hit_rate

    def _get_file_stub_and_name(self, event: AstrMessageEvent) -> tuple[str, str]:
        """根据事件类型返回文件后缀与展示名称。"""
        group_obj = getattr(event.message_obj, "group", None) if event.message_obj else None
        group_id_raw = event.get_group_id() or (getattr(group_obj, "group_id", None) if group_obj else None)

        if group_id_raw:
            group_id_str = str(group_id_raw).strip()
            group_name = getattr(group_obj, "group_name", None) if group_obj else None
            name = group_name or group_id_str or "未命名群"
            file_stub = f"group-{group_id_str or 'unknown'}"
        else:
            sender_name = (
                event.get_sender_name() or event.get_sender_id() or "未命名用户"
            )
            name = sender_name
            sender_id = event.get_sender_id() or "unknown"
            sender_id_str = str(sender_id).strip()
            file_stub = f"private-{sender_id_str or 'unknown'}"

        safe_stub = self._sanitize_stub(file_stub)
        return safe_stub, name

    @staticmethod
    def _sanitize_stub(stub: str) -> str:
        """仅保留字母数字下划线，避免路径遍历，空结果回退 unknown。"""
        cleaned = QQRecordPlugin.SANITIZE_PATTERN.sub("_", stub)
        cleaned = cleaned.strip("_-")
        return cleaned or "unknown"

    async def initialize(self):
        # 从持久化KV恢复配置
        try:
            enabled = await self._get_kv_value("cache_enabled", True)
            if isinstance(enabled, bool):
                self._cache_enabled = enabled
            limit = await self._get_kv_value("cache_limit", self.DEFAULT_CACHE_LIMIT)
            try:
                limit_int = int(limit) if limit is not None else self.DEFAULT_CACHE_LIMIT
            except Exception:
                limit_int = self.DEFAULT_CACHE_LIMIT
            admin_only = await self._get_kv_value("admin_only", False)
            try:
                self._admin_only = bool(admin_only)
            except Exception:
                self._admin_only = False
            export_admin_only = await self._get_kv_value("export_admin_only", False)
            try:
                self._export_admin_only = bool(export_admin_only)
            except Exception:
                self._export_admin_only = False
            # 夹紧范围并应用到现有会话
            async with self._write_lock:
                self._reconfigure_cache_limit(max(1, min(limit_int, self.MAX_CACHE_LIMIT)))
        except Exception as exc:
            logger.exception("QQRecord 恢复配置失败，使用默认值: %s", exc)

        logger.info(
            "QQRecord 插件已初始化，缓存开关=%s，容量：每会话 %s 条",
            self._cache_enabled,
            self._cache_limit,
        )
        # 启动每日 6 点的清理任务
        try:
            self._cleanup_scheduler.start(
                max_age_hours=self.DEFAULT_CLEANUP_HOURS,
                temp_cleanup_hours=self.TEMP_CLEANUP_HOURS,
            )
        except Exception as exc:
            logger.exception("QQRecord 清理任务启动失败: %s", exc)

    def _format_line(self, content: str, session_name: str) -> str:
        """将消息格式化为“内容【会话名称】”行。"""
        safe_content = content.strip() if content else ""
        safe_session = session_name.strip() if session_name else "未命名会话"
        return f"{safe_content}【{safe_session}】"

    def _get_reply_anchor_id(
        self,
        event: AstrMessageEvent,
    ) -> tuple[str | int | None, bool]:
        """从消息链中提取引用锚点 ID。

        返回 (anchor_id, is_reply)。如果不存在 Reply 段，则使用当前消息 ID 作为锚点并标记为非回复。
        """
        try:
            comps: list[BaseMessageComponent] = event.get_messages()
            for comp in comps:
                if isinstance(comp, Reply):
                    anchor_id = getattr(comp, "id", None)
                    if anchor_id is None:
                        anchor_id = getattr(event.message_obj, "reply_id", None)
                    if anchor_id is None:
                        anchor_id = getattr(event.message_obj, "message_id", None)
                    return anchor_id, True
        except Exception as exc:
            self._log_debug_exception("QQRecord 读取 Reply 锚点异常", exc)
        # 无 Reply 段时，使用自身消息 ID
        mid = getattr(event.message_obj, "message_id", None)
        return mid, False

    @staticmethod
    def _log_debug_exception(message: str, exc: Exception, **context):
        try:
            if context:
                detail = ", ".join(f"{key}={value}" for key, value in context.items())
                logger.warning("%s: %s (%s)", message, exc, detail, exc_info=exc)
            else:
                logger.warning("%s: %s", message, exc, exc_info=exc)
        except Exception:
            pass

    def _bump_stat(self, stub: str, hit: bool):
        self._thread_cache.bump_stat(stub, hit)

    async def _get_kv_value(self, key: str, default):
        return await self._config_store.get(key, default)

    async def _put_kv_value(self, key: str, value):
        await self._config_store.put(key, value)

    def _get_session_lines_unlocked(self, file_stub: str) -> list[str]:
        return self._thread_cache.get_session_lines_unlocked(file_stub)

    async def _get_session_lines(self, file_stub: str) -> list[str]:
        async with self._write_lock:
            return self._get_session_lines_unlocked(file_stub)

    @staticmethod
    def _get_thread_lines(entry: ThreadEntry) -> list[str]:
        return ThreadCache.get_thread_lines(entry)

    def _ensure_threads_session(self, stub: str):
        self._thread_cache.ensure_threads_session(stub)

    def _enforce_session_count(self):
        """确保会话数量不超过 MAX_SESSIONS，淘汰最久未活跃会话。"""
        self._thread_cache.enforce_session_count(self.MAX_SESSIONS)

    def _generate_anchor_key(self, anchor_id: str | int | None) -> str:
        return self._thread_cache.generate_anchor_key(anchor_id)

    @staticmethod
    def _is_safe_path(base_dir: str, target_path: str) -> bool:
        try:
            if not base_dir or not target_path:
                return False
            base = os.path.abspath(os.path.normpath(base_dir))
            if os.path.isabs(target_path):
                target = os.path.abspath(os.path.normpath(target_path))
            else:
                target = os.path.abspath(os.path.normpath(os.path.join(base, target_path)))
            return os.path.commonpath([base]) == os.path.commonpath([base, target])
        except Exception:
            return False

    def _get_or_create_thread_entry(self, stub: str, key: str) -> ThreadEntry:
        return self._thread_cache.get_or_create_thread_entry(stub, key)

    def _append_thread_reply(self, entry: ThreadEntry, stub: str, key: str, line: str):
        self._thread_cache.append_thread_reply(entry, stub, key, line)

    def _set_thread_main_or_reply(self, entry: ThreadEntry, stub: str, key: str, line: str):
        self._thread_cache.set_thread_main_or_reply(entry, stub, key, line)

    def _enforce_session_capacity(self, stub: str):
        """确保每会话的总记录条数不超过 _cache_limit（按线程单位裁剪）。"""
        self._thread_cache.enforce_session_capacity(stub, self._cache_limit)

    def _write_threaded(self, stub: str, anchor_id: str | int | None, is_reply: bool, line: str):
        """写入线程化缓存。

        - 无 Reply：作为新的锚点 main 记录。
        - 有 Reply：追加到对应锚点的 replies。
        """
        self._thread_cache.write_threaded(
            stub=stub,
            anchor_id=anchor_id,
            is_reply=is_reply,
            line=line,
            cache_limit=self._cache_limit,
            max_sessions=self.MAX_SESSIONS,
        )

    def _all_session_keys(self) -> set[str]:
        return self._thread_cache.all_session_keys()

    async def _send_cache_as_file(
        self,
        event: AstrMessageEvent,
        lines: list[str],
        name: str,
        file_stub: str,
    ):
        await self._exporter.send_cache_as_file(
            event,
            lines,
            name,
            file_stub,
            segment_len=self.DEFAULT_SEGMENT_LEN,
            segment_delay=self.DEFAULT_SEGMENT_DELAY,
        )

    @filter.platform_adapter_type(PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(EventMessageType.ALL)
    async def record_message(self, event: AstrMessageEvent):
        """监听所有消息（群聊与私聊）并落盘为“内容【名称】”。

        - 群聊：名称为群昵称（优先群名，退化群号），缓存键 group-<group_id>。
        - 私聊：名称为发送者昵称（退化 user_id），缓存键 private-<user_id>。
        """
        try:
            if not self._cache_enabled:
                return
            sender_id = None
            if event.message_obj and getattr(event.message_obj, "sender", None):
                sender_id = getattr(event.message_obj.sender, "user_id", None)
            self_id = getattr(event.message_obj, "self_id", None)
            if self_id and sender_id and str(self_id) == str(sender_id):
                return
            file_stub, name = self._get_file_stub_and_name(event)
            content = event.message_str
            line = self._format_line(content, name)
            # 线程化写入（按 Reply 锚点归类）
            anchor_id, is_reply = self._get_reply_anchor_id(event)
            async with self._write_lock:
                self._write_threaded(file_stub, anchor_id, is_reply, line)
        except Exception as exc:  # noqa: BLE001 - 兜底避免插件崩溃
            logger.exception("QQRecord 处理消息失败: %s", exc)

    def _seconds_until_next_6am(self) -> float:
        return self._cleanup_scheduler.seconds_until_next_6am()

    async def _cleanup_loop(self):
        await self._cleanup_scheduler._cleanup_loop(
            max_age_hours=self.DEFAULT_CLEANUP_HOURS,
            temp_cleanup_hours=self.TEMP_CLEANUP_HOURS,
        )

    async def _cleanup_temp_files(self, max_age_hours: int = TEMP_CLEANUP_HOURS):
        await self._cleanup_scheduler.cleanup_temp_files(max_age_hours=max_age_hours)

    async def _cleanup_inactive(self, max_age_hours: int = DEFAULT_CLEANUP_HOURS):
        async with self._write_lock:
            await self._cleanup_scheduler.cleanup_inactive(max_age_hours=max_age_hours)

    @filter.command("record")
    async def record_command(self, event: AstrMessageEvent, limit: int = 10):
        """命令 `/record [limit]`：返回当前会话内存缓存的最近 N 行。"""
        try:
            denied = self._admin_denied_message(
                event,
                self._export_admin_only,
                "仅管理员可导出记录。",
            )
            if denied:
                yield event.plain_result(denied)
                return
            file_stub, name = self._get_file_stub_and_name(event)
            if not self._cache_enabled:
                yield event.plain_result(self._cache_disabled_message())
                return
            safe_limit = self._clamp_limit(limit)
            async with self._write_lock:
                lines = self._get_session_lines_unlocked(file_stub)
                lines = lines[-safe_limit:]

            if not lines:
                yield event.plain_result(f"缓存为空，当前会话：{name}")
                return

            self._bump_stat(file_stub, True)
            preview = "\n".join(lines)
            yield event.plain_result(
                f"最近 {len(lines)} 条缓存记录（{name}）：\n{preview}"
            )
        except (ValueError, TypeError) as exc:
            logger.warning("QQRecord /record 参数错误: %s", exc, exc_info=exc)
            yield event.plain_result("参数无效，请检查后重试。")
        except (RuntimeError, OSError) as exc:
            logger.warning("QQRecord /record 命令失败: %s", exc, exc_info=exc)
            yield event.plain_result("读取记录失败，请稍后重试。")
        except Exception as exc:  # noqa: BLE001
            logger.exception("QQRecord /record 命令异常: %s", exc)
            yield event.plain_result("命令执行失败，请稍后重试。")

    @filter.command("record_thread")
    async def record_thread_command(self, event: AstrMessageEvent, anchor_id: str, limit: int | None = None):
        """命令 `/record_thread <anchor_id> [limit]`：按锚点查看该线程内容。"""
        try:
            file_stub, name = self._get_file_stub_and_name(event)
            key = str(anchor_id).strip()
            async with self._write_lock:
                threads = self._threads.get(file_stub, OrderedDict())
                entry = threads.get(key)
                if not entry:
                    yield event.plain_result(f"未找到锚点 {key} 的线程（{name}）。")
                    self._bump_stat(file_stub, False)
                    return
                lines = self._get_thread_lines(entry)
                if isinstance(limit, int) and limit > 0:
                    lines = lines[-limit:]

            if not lines:
                yield event.plain_result(f"该线程暂无记录（{name}）。")
                return

            self._bump_stat(file_stub, True)
            preview = "\n".join(lines)
            yield event.plain_result(
                f"线程 {key} 最近 {len(lines)} 条（{name}）：\n{preview}"
            )
        except (ValueError, TypeError) as exc:
            logger.warning("QQRecord /record_thread 参数错误: %s", exc, exc_info=exc)
            yield event.plain_result("参数无效，请检查后重试。")
        except (RuntimeError, OSError) as exc:
            logger.warning("QQRecord /record_thread 命令失败: %s", exc, exc_info=exc)
            yield event.plain_result("读取线程记录失败，请稍后重试。")
        except Exception as exc:  # noqa: BLE001
            logger.exception("QQRecord /record_thread 命令异常: %s", exc)
            yield event.plain_result("命令执行失败，请稍后重试。")

    @filter.command("record_threads")
    async def record_threads_command(self, event: AstrMessageEvent, limit: int = 10):
        """命令 `/record_threads [limit]`：列出最近的线程摘要。"""
        try:
            file_stub, name = self._get_file_stub_and_name(event)
            async with self._write_lock:
                threads = self._threads.get(file_stub, OrderedDict())
                # 按最近活跃时间排序
                items = sorted(
                    threads.items(),
                    key=lambda item: item[1].last_ts or item[1].first_ts or datetime.min,
                    reverse=True,
                )[: max(1, limit)]
            if not items:
                yield event.plain_result(f"当前会话暂无线程（{name}）。")
                return

            def _short(s: str, n: int = self.THREAD_MAIN_PREVIEW_LIMIT) -> str:
                s = (s or "").strip()
                return s if len(s) <= n else s[:n] + "…"

            lines = []
            for key, entry in items:
                main = entry.main or "(无主消息)"
                cnt = 1 if entry.main else 0
                cnt += len(entry.replies)
                ts = entry.last_ts or entry.first_ts
                ts_str = ts.strftime("%Y-%m-%d %H:%M:%S") if ts else "-"
                lines.append(
                    f"anchor={key} | count={cnt} | last={ts_str} | main={_short(main)}"
                )

            self._bump_stat(file_stub, True)
            preview = "\n".join(lines)
            tip = "\n提示：使用 /record_thread <anchor_id> 查看该线程详情。"
            yield event.plain_result(
                f"最近 {len(items)} 个线程摘要（{name}）：\n{preview}{tip}"
            )
        except (ValueError, TypeError) as exc:
            logger.warning("QQRecord /record_threads 参数错误: %s", exc, exc_info=exc)
            yield event.plain_result("参数无效，请检查后重试。")
        except (RuntimeError, OSError) as exc:
            logger.warning("QQRecord /record_threads 命令失败: %s", exc, exc_info=exc)
            yield event.plain_result("列出线程摘要失败，请稍后重试。")
        except Exception as exc:  # noqa: BLE001
            logger.exception("QQRecord /record_threads 命令异常: %s", exc)
            yield event.plain_result("命令执行失败，请稍后重试。")

    @filter.command("record_file")
    async def record_file_command(
        self,
        event: AstrMessageEvent,
        limit: int = 10,
        fmt: str | None = None,
    ):
        try:
            denied = self._admin_denied_message(
                event,
                self._export_admin_only,
                "仅管理员可导出记录。",
            )
            if denied:
                yield event.plain_result(denied)
                return
            file_stub, name = self._get_file_stub_and_name(event)
            if not self._cache_enabled:
                yield event.plain_result(self._cache_disabled_message())
                return
            safe_limit = self._clamp_limit(limit)
            async with self._write_lock:
                lines = self._get_session_lines_unlocked(file_stub)
                lines = lines[-safe_limit:]

            # 根据格式简单适配内容（md 与 txt 目前仅在标题/分隔上有轻度差异）
            if fmt and str(fmt).lower() == "md":
                # 为 markdown 添加标题与分隔
                lines = [f"# 记录（{name}）", "", *lines]

            await self._send_cache_as_file(event, lines, name, file_stub)
            self._bump_stat(file_stub, bool(lines))
        except (ValueError, TypeError) as exc:
            logger.warning("QQRecord /record_file 参数错误: %s", exc, exc_info=exc)
            yield event.plain_result("参数无效，请检查后重试。")
        except (RuntimeError, OSError) as exc:
            logger.warning("QQRecord /record_file 命令失败: %s", exc, exc_info=exc)
            # 发送失败时退化为分段文本消息
            try:
                file_stub, name = self._get_file_stub_and_name(event)
                async with self._write_lock:
                    lines = self._get_session_lines_unlocked(file_stub)
                    lines = lines[-self._clamp_limit(limit):]
                content = "\n".join(lines)
                await self._send_text_segments(
                    event,
                    f"记录文件发送失败，改为文本：\n{content}",
                )
                self._bump_stat(file_stub, bool(lines))
            except (RuntimeError, OSError, ValueError) as fallback_exc:
                self._log_debug_exception(
                    "QQRecord /record_file 兜底失败",
                    fallback_exc,
                    stub=file_stub if "file_stub" in locals() else None,
                    limit=limit,
                )
                yield event.plain_result("导出记录为文件时出现错误，请稍后重试。")
        except Exception as exc:  # noqa: BLE001
            logger.exception("QQRecord /record_file 命令异常: %s", exc)
            yield event.plain_result("命令执行失败，请稍后重试。")

    def _reconfigure_cache_limit(self, new_limit: int):
        """调整所有会话缓存容量，保留最近 new_limit 条内容。"""
        new_limit = max(1, min(new_limit, self.MAX_CACHE_LIMIT))
        self._cache_limit = new_limit
        for stub in list(self._threads.keys()):
            self._enforce_session_capacity(stub)

    @filter.command("record_cache")
    async def record_cache_command(
        self,
        event: AstrMessageEvent,
        flag: str | None = None,
    ):
        """命令 `/record_cache [on|off|status]`：开启/关闭/查询状态。仅管理员可调。"""
        file_stub = None
        try:
            denied = self._admin_denied_message(
                event,
                self._admin_only,
                "仅管理员可用该命令。",
            )
            if denied:
                yield event.plain_result(denied)
                return
            if flag is None:
                state = "on" if self._cache_enabled else "off"
                yield event.plain_result(
                    f"缓存状态：{state}，容量：每会话 {self._cache_limit} 条"
                )
                return

            val = str(flag).strip().lower()
            if val == "status":
                # 输出更详尽状态
                file_stub, name = self._get_file_stub_and_name(event)
                session_lines, threads_cnt, total_sessions, hit, miss, hit_rate = (
                    await self._collect_session_status(file_stub)
                )
                yield event.plain_result(
                    f"状态：{'开启' if self._cache_enabled else '关闭'}\n"
                    f"容量上限：{self._cache_limit}\n"
                    f"当前会话（{name}）：行数={session_lines}，线程数={threads_cnt}\n"
                    f"命中：{hit}，未命中：{miss}，命中率：{hit_rate:.1f}%\n"
                    f"总会话键数：{total_sessions}"
                )
                return
            if val in ("on", "true", "1"):
                self._cache_enabled = True
                # 持久化
                await self._put_kv_value("cache_enabled", True)
                yield event.plain_result("已开启缓存写入。")
            elif val in ("off", "false", "0"):
                self._cache_enabled = False
                await self._put_kv_value("cache_enabled", False)
                yield event.plain_result("已关闭缓存写入。")
            else:
                yield event.plain_result(
                    "参数无效，请使用 /record_cache on 或 /record_cache off"
                )
        except (ValueError, TypeError) as exc:
            logger.warning(
                "QQRecord /record_cache 参数错误: %s (flag=%s, stub=%s)",
                exc,
                flag,
                file_stub,
                exc_info=exc,
            )
            yield event.plain_result("参数无效，请检查后重试。")
        except (RuntimeError, OSError) as exc:
            logger.warning(
                "QQRecord /record_cache 命令失败: %s (flag=%s, stub=%s)",
                exc,
                flag,
                file_stub,
                exc_info=exc,
            )
            yield event.plain_result("切换缓存状态失败，请稍后重试。")
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "QQRecord /record_cache 命令异常: %s (flag=%s, stub=%s)",
                exc,
                flag,
                file_stub,
            )
            yield event.plain_result("命令执行失败，请稍后重试。")

    @filter.command("record_export")
    async def record_export_command(
        self,
        event: AstrMessageEvent,
        flag: str | None = None,
    ):
        """命令 `/record_export [on|off|status]`：导出权限开关（管理员限定）。"""
        try:
            denied = self._admin_denied_message(
                event,
                True,
                "仅管理员可用该命令。",
            )
            if denied:
                yield event.plain_result(denied)
                return
            if flag is None or str(flag).strip().lower() == "status":
                state = "on" if self._export_admin_only else "off"
                yield event.plain_result(f"导出权限状态：{state}")
                return
            val = str(flag).strip().lower()
            if val in ("on", "true", "1"):
                self._export_admin_only = True
                await self._put_kv_value("export_admin_only", True)
                yield event.plain_result("已开启导出管理员限制。")
            elif val in ("off", "false", "0"):
                self._export_admin_only = False
                await self._put_kv_value("export_admin_only", False)
                yield event.plain_result("已关闭导出管理员限制。")
            else:
                yield event.plain_result(
                    "参数无效，请使用 /record_export on 或 /record_export off"
                )
        except (ValueError, TypeError) as exc:
            logger.warning("QQRecord /record_export 参数错误: %s", exc, exc_info=exc)
            yield event.plain_result("参数无效，请检查后重试。")
        except (RuntimeError, OSError) as exc:
            logger.warning("QQRecord /record_export 命令失败: %s", exc, exc_info=exc)
            yield event.plain_result("切换导出权限失败，请稍后重试。")
        except Exception as exc:  # noqa: BLE001
            logger.exception("QQRecord /record_export 命令异常: %s", exc)
            yield event.plain_result("命令执行失败，请稍后重试。")

    @filter.command("record_limit")
    async def record_limit_command(
        self,
        event: AstrMessageEvent,
        n: int | None = None,
    ):
        """命令 `/record_limit [n]`：设置/查询每会话缓存上限，范围 1~1000。仅管理员可调。"""
        file_stub = None
        try:
            denied = self._admin_denied_message(
                event,
                self._admin_only,
                "仅管理员可用该命令。",
            )
            if denied:
                yield event.plain_result(denied)
                return
            if n is None:
                # 无参时输出更详尽状态
                file_stub, name = self._get_file_stub_and_name(event)
                session_lines, threads_cnt, total_sessions, hit, miss, hit_rate = (
                    await self._collect_session_status(file_stub)
                )
                yield event.plain_result(
                    f"当前容量上限：每会话 {self._cache_limit} 条（可设置范围 1~{self.MAX_CACHE_LIMIT}）\n"
                    f"当前会话（{name}）：行数={session_lines}，线程数={threads_cnt}\n"
                    f"命中：{hit}，未命中：{miss}，命中率：{hit_rate:.1f}%\n"
                    f"总会话键数：{total_sessions}"
                )
                return

            async with self._write_lock:
                self._reconfigure_cache_limit(int(n))

            # 持久化
            await self._put_kv_value("cache_limit", int(self._cache_limit))

            yield event.plain_result(
                "已将容量上限设为："
                f"每会话 {self._cache_limit} 条，现有会话已按新上限裁剪"
            )
        except (ValueError, TypeError) as exc:
            logger.warning(
                "QQRecord /record_limit 参数错误: %s (n=%s, stub=%s)",
                exc,
                n,
                file_stub,
                exc_info=exc,
            )
            yield event.plain_result("参数无效，请检查后重试。")
        except (RuntimeError, OSError) as exc:
            logger.warning(
                "QQRecord /record_limit 命令失败: %s (n=%s, stub=%s)",
                exc,
                n,
                file_stub,
                exc_info=exc,
            )
            yield event.plain_result("调整容量上限失败，请稍后重试。")
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "QQRecord /record_limit 命令异常: %s (n=%s, stub=%s)",
                exc,
                n,
                file_stub,
            )
            yield event.plain_result("命令执行失败，请稍后重试。")

    async def terminate(self):
        # 停止清理任务
        try:
            await self._cleanup_scheduler.stop()
        except Exception as exc:
            self._log_debug_exception("QQRecord 终止清理任务异常", exc)
        logger.info("QQRecord 插件已卸载。")

    async def _send_text_segments(
        self,
        event: AstrMessageEvent,
        content: str,
        segment_len: int = DEFAULT_SEGMENT_LEN,
        delay: float = DEFAULT_SEGMENT_DELAY,
    ):
        await self._exporter.send_text_segments(
            event,
            content,
            segment_len=segment_len,
            delay=delay,
        )

    @staticmethod
    def _iter_text_segments(text: str, limit: int):
        for seg in QQRecordPlugin._split_text_segments(text, limit):
            yield seg

    @staticmethod
    def _split_long_line(line: str, limit: int) -> list[str]:
        tokens = QQRecordPlugin.WHITESPACE_SPLIT_PATTERN.split(line)
        segments: list[str] = []
        buffer = ""

        def flush_buffer():
            nonlocal buffer
            if buffer:
                segments.append(buffer)
                buffer = ""

        for tok in tokens:
            if not tok:
                continue
            is_url = bool(QQRecordPlugin.URL_PATTERN.fullmatch(tok.strip()))
            if len(tok) > limit:
                flush_buffer()
                if is_url:
                    segments.append(tok)
                else:
                    segments.extend(
                        [tok[i : i + limit] for i in range(0, len(tok), limit)]
                    )
                continue
            if len(buffer) + len(tok) > limit:
                flush_buffer()
                buffer = tok
            else:
                buffer += tok

        flush_buffer()
        return segments

    @staticmethod
    def _split_text_segments(text: str, limit: int) -> list[str]:
        """按更友好的边界分段，尽量避免切断 URL。"""
        if limit <= 0:
            return [text]
        segments: list[str] = []
        current = ""

        def flush():
            nonlocal current
            if current:
                segments.append(current)
                current = ""

        for line in text.splitlines(keepends=True):
            if len(line) > limit:
                flush()
                segments.extend(QQRecordPlugin._split_long_line(line, limit))
                continue

            if len(current) + len(line) > limit:
                flush()
            current += line

        flush()
        return segments
