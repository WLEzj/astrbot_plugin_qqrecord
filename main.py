import asyncio
import re
import os
import itertools
from datetime import datetime, timedelta
from collections import OrderedDict

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.event.filter import EventMessageType, PlatformAdapterType
from astrbot.api.star import Context, Star
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.message.components import Reply, BaseMessageComponent
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

from services import ThreadEntry, ThreadCache, StatsTracker, Exporter, CleanupScheduler, ConfigStore
from commands import RecordCommands, ThreadCommands, AdminCommands


class QQRecordPlugin(Star):
    """记录 QQ 会话消息到内存缓存（默认每会话最近 500 条）。"""

    TEMP_FILE_PREFIX = "astrbot_qqrecord_"
    DEFAULT_CACHE_LIMIT = 500
    MAX_CACHE_LIMIT = 1000
    DEFAULT_SEGMENT_LEN = 2000
    DEFAULT_SEGMENT_DELAY = 0.5
    THREAD_MAIN_PREVIEW_LIMIT = 50
    THREAD_REPLY_PREVIEW_LIMIT = 30
    DEFAULT_CLEANUP_HOUR = 6
    DEFAULT_CLEANUP_MINUTE = 0
    DEFAULT_CLEANUP_MAX_AGE_HOURS = 24
    DEFAULT_TEMP_CLEANUP_HOURS = 2
    DEFAULT_CLEANUP_PREVIEW_LIMIT = 10
    DEFAULT_CLEANUP_BACKOFF_SECONDS = 300
    DEFAULT_CLEANUP_BACKOFF_MAX_SECONDS = 3600
    DEFAULT_AUTO_ANCHOR_THRESHOLD = 3
    DEFAULT_UUID_SUFFIX_LEN = 8

    def __init__(self, context: Context):
        super().__init__(context)
        self._write_lock = asyncio.Lock()
        self._cache_limit = self.DEFAULT_CACHE_LIMIT
        self._cache_enabled = True
        self._admin_only = False
        self._export_admin_only = True
        self._record_private_chats = False
        self._group_whitelist: list[str] = []
        self._enable_sanitization = True
        self._auto_anchor_counter = itertools.count(1)

        self._stats_tracker = StatsTracker()
        self._thread_cache = ThreadCache(
            log_debug_exception=self._log_debug_exception,
            auto_anchor_counter=self._auto_anchor_counter,
            uuid_suffix_len=self.DEFAULT_UUID_SUFFIX_LEN,
            stats_tracker=self._stats_tracker,
        )
        self._config_store = ConfigStore(
            plugin=self,
            plugin_id=self._get_plugin_id(),
            log_debug_exception=self._log_debug_exception,
        )
        self._exporter = Exporter(
            is_safe_path=self._is_safe_path,
            temp_dir_provider=self._temp_dir_provider,
            temp_file_prefix=self.TEMP_FILE_PREFIX,
            split_text_segments=self._split_text_segments,
            log_debug_exception=self._log_debug_exception,
        )
        self._cleanup_scheduler = CleanupScheduler(
            thread_cache=self._thread_cache,
            is_safe_path=self._is_safe_path,
            temp_dir_provider=self._temp_dir_provider,
            temp_file_prefix=self.TEMP_FILE_PREFIX,
            cleanup_hour=self.DEFAULT_CLEANUP_HOUR,
            cleanup_minute=self.DEFAULT_CLEANUP_MINUTE,
            cleanup_preview_limit=self.DEFAULT_CLEANUP_PREVIEW_LIMIT,
            cleanup_backoff_seconds=self.DEFAULT_CLEANUP_BACKOFF_SECONDS,
            cleanup_backoff_max_seconds=self.DEFAULT_CLEANUP_BACKOFF_MAX_SECONDS,
            log_debug_exception=self._log_debug_exception,
            write_lock=self._write_lock,
        )

        self._record_commands = RecordCommands(self)
        self._thread_commands = ThreadCommands(self)
        self._admin_commands = AdminCommands(self)

    def _get_plugin_id(self) -> str:
        return getattr(self, "plugin_id", "astrbot_plugin_qqrecord")

    def _log_debug_exception(self, msg: str, exc: Exception, **kwargs):
        logger.debug("%s: %s (kwargs=%s)", msg, exc, kwargs, exc_info=exc)

    def _is_safe_path(self, base_dir: str, path: str) -> bool:
        try:
            base = os.path.normpath(os.path.abspath(base_dir))
            target = os.path.normpath(os.path.abspath(path))
            return os.path.commonpath([base, target]) == base
        except (ValueError, OSError):
            return False

    def _temp_dir_provider(self) -> str:
        return get_astrbot_temp_path()

    def _split_text_segments(self, text: str, max_len: int) -> list[str]:
        segments: list[str] = []
        for i in range(0, len(text), max_len):
            segments.append(text[i : i + max_len])
        return segments

    def _cache_disabled_message(self) -> str:
        return "缓存已关闭，无法查看记录。请联系管理员使用 /record_cache on 开启。"

    async def _get_kv_value(self, key: str, default):
        return await self._config_store.get(key, default)

    async def _put_kv_value(self, key: str, value):
        await self._config_store.put(key, value)

    async def _cleanup_inactive(self, max_age_hours: int = 24):
        """清理不活跃的会话记录"""
        async with self._write_lock:
            # 直接执行清理逻辑，避免嵌套异步函数
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
                    preview = ", ".join(removed_keys[: self._cleanup_scheduler._cleanup_preview_limit])
                    more = "..." if len(removed_keys) > self._cleanup_scheduler._cleanup_preview_limit else ""
                    now = datetime.now()
                    next_run = now.replace(
                        hour=self._cleanup_scheduler._cleanup_hour,
                        minute=self._cleanup_scheduler._cleanup_minute,
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

    async def _load_settings(self):
        self._cache_enabled = await self._get_kv_value("cache_enabled", True)
        self._cache_limit = self._clamp_limit(await self._get_kv_value("cache_limit", self.DEFAULT_CACHE_LIMIT))
        self._admin_only = await self._get_kv_value("admin_only", False)
        self._export_admin_only = await self._get_kv_value("export_admin_only", True)
        self._record_private_chats = await self._get_kv_value("record_private_chats", False)
        self._group_whitelist = await self._get_kv_value("group_whitelist", [])
        self._enable_sanitization = await self._get_kv_value("enable_sanitization", True)

    def _clamp_limit(self, limit: int) -> int:
        if limit <= 0:
            return 1
        if limit > self.MAX_CACHE_LIMIT:
            return self.MAX_CACHE_LIMIT
        return limit

    def _reconfigure_cache_limit(self, new_limit: int):
        if new_limit <= 0:
            new_limit = 1
        if new_limit > self.MAX_CACHE_LIMIT:
            new_limit = self.MAX_CACHE_LIMIT
        self._cache_limit = new_limit
        for stub, threads in self._thread_cache.threads.items():
            self._thread_cache.enforce_session_capacity(stub, self._cache_limit)

    def _get_file_stub_and_name(self, event: AstrMessageEvent) -> tuple[str, str]:
        session = event.session
        if not session:
            return ("unknown", "未知会话")
        
        try:
            platform = session.adapter_type
        except AttributeError:
            # 处理测试环境或其他情况下缺少 adapter_type 的情况
            return ("unknown", "未知会话")
        
        if platform == PlatformAdapterType.onebot:
            try:
                group_id = session.group_id
                user_id = session.user_id
                if group_id:
                    return (f"qq_group_{group_id}", f"QQ群 {group_id}")
                else:
                    return (f"qq_private_{user_id}", f"QQ私聊 {user_id}")
            except AttributeError:
                # 处理缺少 group_id 或 user_id 的情况
                return ("unknown", "未知会话")
        else:
            try:
                session_id = session.session_id
                return (f"{platform.value}_{session_id}", f"{platform.value}会话 {session_id}")
            except AttributeError:
                # 处理缺少 session_id 或 platform.value 的情况
                return ("unknown", "未知会话")

    def _should_record_message(self, event: AstrMessageEvent) -> bool:
        session = event.session
        if not session:
            return False
        
        try:
            platform = session.adapter_type
        except AttributeError:
            # 处理测试环境或其他情况下缺少 adapter_type 的情况
            return True
        
        if platform == PlatformAdapterType.onebot:
            try:
                group_id = session.group_id
                if group_id:
                    if self._group_whitelist:
                        return str(group_id) in self._group_whitelist
                    return True
                else:
                    return self._record_private_chats
            except AttributeError:
                # 处理缺少 group_id 的情况
                return True
        return True

    def _sanitize_message(self, content: str) -> str:
        if not self._enable_sanitization or not content:
            return content
        sanitized = content
        sanitized = re.sub(r'1[3-9]\d{9}', '[手机号]', sanitized)
        sanitized = re.sub(r'(token|access_token|auth_token|api_key|secret|session_id|jwt)[\s:=]+[A-Za-z0-9\-_\.]{10,}', r'\1=[脱敏]', sanitized, flags=re.IGNORECASE)
        sanitized = re.sub(r'(cookie|set-cookie)[\s:=]+[^\s;]{10,}', r'\1=[脱敏]', sanitized, flags=re.IGNORECASE)
        sanitized = re.sub(r'https?://[^\s\?]+\?[^\s]*?(?:token|key|secret|password|pwd|auth)[^&\s]*', '[URL含敏感参数]', sanitized, flags=re.IGNORECASE)
        return sanitized

    def _get_session_lines_unlocked(self, file_stub: str) -> list[str]:
        return self._thread_cache.get_session_lines_unlocked(file_stub)

    def _get_thread_lines(self, entry: ThreadEntry) -> list[str]:
        lines: list[str] = []
        if entry.main:
            lines.append(entry.main)
        lines.extend(entry.replies)
        return lines

    def _bump_stat(self, stub: str, hit: bool):
        self._thread_cache.bump_stat(stub, hit)

    async def _collect_session_status(self, file_stub: str) -> tuple[int, int, int, int, int, float]:
        threads = self._thread_cache.threads.get(file_stub, OrderedDict())
        session_lines = sum(len(entry.replies) + (1 if entry.main else 0) for entry in threads.values())
        threads_cnt = len(threads)
        total_sessions = len(self._thread_cache.threads)
        stats = self._stats_tracker.stats.get(file_stub, {})
        hit = stats.get("hit", 0)
        miss = stats.get("miss", 0)
        total = hit + miss
        hit_rate = (hit / total * 100) if total > 0 else 0.0
        return (session_lines, threads_cnt, total_sessions, hit, miss, hit_rate)

    async def _admin_denied_message(self, event: AstrMessageEvent, reason: str) -> str | None:
        result = await self._check_admin_permission(event)
        if result["supported"] and not result["is_admin"]:
            return f"{reason}"
        return None

    async def _check_admin_permission(self, event: AstrMessageEvent) -> dict:
        result = {"supported": False, "is_admin": False}
        try:
            if hasattr(event, "is_admin"):
                admin_result = event.is_admin()
                if asyncio.iscoroutine(admin_result):
                    admin_result = await admin_result
                result["is_admin"] = bool(admin_result)
                result["supported"] = True
            if not result["is_admin"] and hasattr(event, "is_group_admin"):
                group_admin_result = event.is_group_admin()
                if asyncio.iscoroutine(group_admin_result):
                    group_admin_result = await group_admin_result
                if group_admin_result:
                    result["is_admin"] = True
                    result["supported"] = True
            if not result["is_admin"] and hasattr(event, "is_super_user"):
                super_user_result = event.is_super_user()
                if asyncio.iscoroutine(super_user_result):
                    super_user_result = await super_user_result
                if super_user_result:
                    result["is_admin"] = True
                    result["supported"] = True
        except Exception as exc:
            self._log_debug_exception("QQRecord 检查管理员权限失败", exc)
        return result

    async def _send_cache_as_file(
        self,
        event,
        lines: list[str],
        name: str,
        file_stub: str,
        *,
        segment_len: int,
        segment_delay: float,
    ):
        await self._exporter.send_cache_as_file(
            event,
            lines,
            name,
            file_stub,
            segment_len=segment_len,
            segment_delay=segment_delay,
        )

    async def _send_text_segments(
        self,
        event,
        content: str,
        *,
        segment_len: int,
        delay: float,
    ):
        await self._exporter.send_text_segments(
            event,
            content,
            segment_len=segment_len,
            delay=delay,
        )

    @filter.event_message_type(EventMessageType.ALL)
    async def record_message(self, event: AstrMessageEvent):
        """监听所有消息类型并记录到缓存。"""
        try:
            if not self._cache_enabled:
                return
            if not self._should_record_message(event):
                return
            session = event.session
            if not session:
                return
            file_stub, _ = self._get_file_stub_and_name(event)
            
            try:
                message_chain = event.message
                if not message_chain:
                    return
                components = message_chain.chain
                if not components:
                    return
            except AttributeError:
                # 处理测试环境或其他情况下缺少 message 属性的情况
                return
            
            text_parts: list[str] = []
            for comp in components:
                if isinstance(comp, str):
                    text_parts.append(comp)
                elif isinstance(comp, BaseMessageComponent):
                    try:
                        text = comp.to_text()
                        if text:
                            text_parts.append(text)
                    except Exception:
                        pass
            if not text_parts:
                return
            content = "".join(text_parts)
            content = self._sanitize_message(content)
            if not content.strip():
                return
            timestamp = datetime.now()
            reply_component = next(
                (c for c in components if isinstance(c, Reply)), None
            )
            async with self._write_lock:
                self._thread_cache.add_message(
                    file_stub,
                    content,
                    timestamp,
                    reply_component,
                )
        except Exception as exc:
            logger.exception("QQRecord 消息记录异常: %s", exc)

    @filter.command("record")
    async def record_command(self, event: AstrMessageEvent, limit: int = 10):
        async for result in self._record_commands.record_command(event, limit):
            yield result

    @filter.command("record_thread")
    async def record_thread_command(self, event: AstrMessageEvent, anchor_id: str, limit: int | None = None):
        async for result in self._thread_commands.record_thread_command(event, anchor_id, limit):
            yield result

    @filter.command("record_threads")
    async def record_threads_command(self, event: AstrMessageEvent, limit: int = 10):
        async for result in self._thread_commands.record_threads_command(event, limit):
            yield result

    @filter.command("record_file")
    async def record_file_command(
        self,
        event: AstrMessageEvent,
        limit: int = 10,
        fmt: str | None = None,
    ):
        async for result in self._record_commands.record_file_command(event, limit, fmt):
            yield result

    @filter.command("record_cache")
    async def record_cache_command(
        self,
        event: AstrMessageEvent,
        flag: str | None = None,
    ):
        async for result in self._admin_commands.record_cache_command(event, flag):
            yield result

    @filter.command("record_export")
    async def record_export_command(
        self,
        event: AstrMessageEvent,
        flag: str | None = None,
    ):
        async for result in self._admin_commands.record_export_command(event, flag):
            yield result

    @filter.command("record_admin")
    async def record_admin_command(
        self,
        event: AstrMessageEvent,
        flag: str | None = None,
    ):
        async for result in self._admin_commands.record_admin_command(event, flag):
            yield result

    @filter.command("record_limit")
    async def record_limit_command(
        self,
        event: AstrMessageEvent,
        n: int | None = None,
    ):
        async for result in self._admin_commands.record_limit_command(event, n):
            yield result

    @filter.command("record_private")
    async def record_private_command(
        self,
        event: AstrMessageEvent,
        flag: str | None = None,
    ):
        async for result in self._admin_commands.record_private_command(event, flag):
            yield result

    @filter.command("record_whitelist")
    async def record_whitelist_command(
        self,
        event: AstrMessageEvent,
        action: str | None = None,
        *args: str,
    ):
        async for result in self._admin_commands.record_whitelist_command(event, action, *args):
            yield result

    @filter.command("record_sanitize")
    async def record_sanitize_command(
        self,
        event: AstrMessageEvent,
        flag: str | None = None,
    ):
        async for result in self._admin_commands.record_sanitize_command(event, flag):
            yield result

    async def on_load(self):
        """插件加载时初始化。"""
        await self._load_settings()
        self._cleanup_scheduler.start(
            max_age_hours=self.DEFAULT_CLEANUP_MAX_AGE_HOURS,
            temp_cleanup_hours=self.DEFAULT_TEMP_CLEANUP_HOURS,
        )
        logger.info(
            "QQRecord 插件已加载。缓存状态：%s，容量：%d 条",
            "开启" if self._cache_enabled else "关闭",
            self._cache_limit,
        )

    async def on_unload(self):
        """插件卸载时清理资源。"""
        await self._cleanup_scheduler.stop()
        logger.info("QQRecord 插件已卸载。")
