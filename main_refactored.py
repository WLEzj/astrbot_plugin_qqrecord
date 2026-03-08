"""
重构后的主插件类
展示如何通过依赖注入和服务接口实现松耦合架构
"""
import asyncio
import re
import os
import itertools
from datetime import datetime, timedelta
from typing import Optional

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.event.filter import EventMessageType, PlatformAdapterType
from astrbot.api.star import Context, Star
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.message.components import Reply, BaseMessageComponent
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

from domain.models import MessageRecord, PluginConfig
from domain.interfaces import ICacheService, IConfigService
from services import ThreadEntry, ThreadCache, StatsTracker, Exporter, CleanupScheduler, ConfigStore
from services.cache_service import CacheService
from services.config_service import ConfigService
from commands.refactored_commands import RecordCommand, AdminCommand


class QQRecordPlugin(Star):
    """QQ记录插件 - 重构版本
    
    重构改进：
    1. 通过服务接口实现松耦合
    2. 依赖注入提高可测试性
    3. 清晰的职责分离
    4. 遵循SOLID原则
    """

    TEMP_FILE_PREFIX = "astrbot_qqrecord_"
    DEFAULT_CACHE_LIMIT = 500
    MAX_CACHE_LIMIT = 1000
    DEFAULT_SEGMENT_LEN = 2000
    DEFAULT_SEGMENT_DELAY = 0.5
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
        
        # 初始化基础设施
        self._write_lock = asyncio.Lock()
        self._auto_anchor_counter = itertools.count(1)
        
        # 初始化服务层（通过依赖注入）
        self._init_services()
        
        # 初始化命令处理器
        self._init_commands()

    def _init_services(self):
        """初始化服务层 - 核心改进点"""
        # 1. 配置存储服务
        self._config_store = ConfigStore(
            plugin=self,
            plugin_id=self._get_plugin_id(),
            log_debug_exception=self._log_debug_exception,
        )
        
        # 2. 配置管理服务
        self._config_service: IConfigService = ConfigService(self._config_store)
        
        # 3. 线程缓存和统计服务
        self._stats_tracker = StatsTracker()
        self._thread_cache = ThreadCache(
            log_debug_exception=self._log_debug_exception,
            auto_anchor_counter=self._auto_anchor_counter,
            uuid_suffix_len=self.DEFAULT_UUID_SUFFIX_LEN,
            stats_tracker=self._stats_tracker,
        )
        
        # 4. 缓存服务（封装ThreadCache逻辑）
        self._cache_service: ICacheService = CacheService(
            thread_cache=self._thread_cache,
            stats_tracker=self._stats_tracker,
            write_lock=self._write_lock,
            initial_cache_limit=self.DEFAULT_CACHE_LIMIT,
            max_sessions=500
        )
        
        # 5. 导出服务
        self._exporter = Exporter(
            is_safe_path=self._is_safe_path,
            temp_dir_provider=self._temp_dir_provider,
            temp_file_prefix=self.TEMP_FILE_PREFIX,
            split_text_segments=self._split_text_segments,
            log_debug_exception=self._log_debug_exception,
        )
        
        # 6. 清理调度服务
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

    def _init_commands(self):
        """初始化命令处理器 - 核心改进点"""
        # 通过依赖注入，命令处理器不再直接访问主类
        
        # 1. 记录查看命令
        self._record_command = RecordCommand(
            cache_service=self._cache_service,
            config_service=self._config_service,
            session_id_extractor=self._get_file_stub_and_name,
            permission_checker=self._check_permission
        )
        
        # 2. 管理命令
        self._admin_command = AdminCommand(
            cache_service=self._cache_service,
            config_service=self._config_service,
            session_id_extractor=self._get_file_stub_and_name,
            permission_checker=self._check_permission
        )

    # ==================== 插件生命周期 ====================
    
    async def on_load(self):
        """插件加载时的初始化"""
        await self._load_settings()
        self._cleanup_scheduler.start(
            max_age_hours=self.DEFAULT_CLEANUP_MAX_AGE_HOURS,
            temp_cleanup_hours=self.DEFAULT_TEMP_CLEANUP_HOURS,
        )
        logger.info("QQRecord 插件已加载")

    async def on_unload(self):
        """插件卸载时的清理"""
        await self._cleanup_scheduler.stop()
        logger.info("QQRecord 插件已卸载")

    # ==================== 消息处理 ====================
    
    @filter.event_message_type(EventMessageType.ALL)
    async def record_message(self, event: AstrMessageEvent):
        """监听所有消息类型并记录到缓存"""
        try:
            # 检查是否应该记录
            if not await self._should_record_message(event):
                return
            
            # 提取消息内容
            message_record = await self._extract_message_record(event)
            if not message_record:
                return
            
            # 获取会话ID
            session_id, _ = self._get_file_stub_and_name(event)
            
            # 添加到缓存（通过服务接口）
            await self._cache_service.add_message(session_id, message_record)
            
        except Exception as exc:
            logger.exception("QQRecord 消息记录异常: %s", exc)

    async def _should_record_message(self, event: AstrMessageEvent) -> bool:
        """检查是否应该记录消息"""
        # 检查缓存状态
        if not await self._config_service.get_cache_enabled():
            return False
        
        # 检查会话
        session = event.session
        if not session:
            return False
        
        # 检查平台和群组权限
        try:
            platform = session.adapter_type
            if platform == PlatformAdapterType.onebot:
                config = await self._config_service.get_config()
                
                group_id = session.group_id
                if group_id:
                    return config.is_group_allowed(group_id)
                else:
                    return config.record_private_chats
        except AttributeError:
            # 处理测试环境或其他情况下缺少属性的情况
            return True
        
        return True

    async def _extract_message_record(self, event: AstrMessageEvent) -> Optional[MessageRecord]:
        """从事件中提取消息记录"""
        try:
            message_chain = event.message
            if not message_chain:
                return None
            
            components = message_chain.chain
            if not components:
                return None
            
            # 提取文本内容
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
                return None
            
            content = "".join(text_parts)
            content = self._sanitize_message(content)
            if not content.strip():
                return None
            
            # 提取回复信息
            reply_component = next(
                (c for c in components if isinstance(c, Reply)), None
            )
            
            return MessageRecord(
                content=content,
                timestamp=datetime.now(),
                is_reply=reply_component is not None,
                anchor_id=getattr(reply_component, "id", None) if reply_component else None
            )
            
        except AttributeError:
            # 处理测试环境或其他情况下缺少 message 属性的情况
            return None

    def _sanitize_message(self, content: str) -> str:
        """消息脱敏处理"""
        config = asyncio.run(self._config_service.get_config())
        if not config.enable_sanitization or not content:
            return content
        
        sanitized = content
        sanitized = re.sub(r'1[3-9]\d{9}', '[手机号]', sanitized)
        sanitized = re.sub(r'(token|access_token|auth_token|api_key|secret|session_id|jwt)[\s:=]+[A-Za-z0-9\-_\.]{10,}', r'\1=[脱敏]', sanitized, flags=re.IGNORECASE)
        sanitized = re.sub(r'(cookie|set-cookie)[\s:=]+[^\s;]{10,}', r'\1=[脱敏]', sanitized, flags=re.IGNORECASE)
        sanitized = re.sub(r'https?://[^\s\?]+\?[^\s]*?(?:token|key|secret|password|pwd|auth)[^&\s]*', '[URL含敏感参数]', sanitized, flags=re.IGNORECASE)
        return sanitized

    # ==================== 命令处理器 ====================
    
    @filter.command("record")
    async def record_command_handler(self, event, limit: int = 10):
        """处理record命令"""
        async for result in self._record_command.execute(event, limit):
            yield result

    @filter.command("record_cache")
    async def record_cache_command_handler(self, event, flag: str | None = None):
        """处理record_cache命令"""
        async for result in self._admin_command.execute_cache_command(event, flag):
            yield result

    # ==================== 辅助方法 ====================
    
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

    async def _load_settings(self):
        """加载配置设置"""
        config = await self._config_service.get_config()
        await self._cache_service.update_cache_limit(config.cache_limit)

    def _get_file_stub_and_name(self, event: AstrMessageEvent) -> tuple[str, str]:
        """获取文件存根和名称"""
        session = event.session
        if not session:
            return ("unknown", "未知会话")
        
        try:
            platform = session.adapter_type
        except AttributeError:
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
                return ("unknown", "未知会话")
        else:
            try:
                session_id = session.session_id
                return (f"{platform.value}_{session_id}", f"{platform.value}会话 {session_id}")
            except AttributeError:
                return ("unknown", "未知会话")

    async def _check_permission(
        self, 
        event, 
        denied_message: str, 
        get_permission_flag: callable
    ) -> Optional[str]:
        """检查权限"""
        if await get_permission_flag():
            denied = await self._admin_denied_message(event, denied_message)
            return denied
        return None