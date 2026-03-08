"""
扩展的插件公共接口
为record.py和thread.py提供完整的公共API
"""
from typing import Optional, List
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from collections import OrderedDict


class ExtendedPluginAPI:
    """
    扩展的插件公共接口封装
    为所有命令处理器提供完整的公共API
    """
    
    def __init__(self, plugin):
        self._plugin = plugin
    
    # ==================== 配置属性访问 ====================
    
    @property
    def cache_enabled(self) -> bool:
        """缓存是否启用"""
        return self._plugin._cache_enabled
    
    @cache_enabled.setter
    def cache_enabled(self, value: bool):
        """设置缓存启用状态"""
        self._plugin._cache_enabled = value
    
    @property
    def cache_limit(self) -> int:
        """缓存容量限制"""
        return self._plugin._cache_limit
    
    @cache_limit.setter
    def cache_limit(self, value: int):
        """设置缓存容量限制"""
        self._plugin._cache_limit = value
    
    @property
    def admin_only(self) -> bool:
        """是否仅管理员可用"""
        return self._plugin._admin_only
    
    @admin_only.setter
    def admin_only(self, value: bool):
        """设置管理员限制"""
        self._plugin._admin_only = value
    
    @property
    def export_admin_only(self) -> bool:
        """导出是否仅管理员可用"""
        return self._plugin._export_admin_only
    
    @export_admin_only.setter
    def export_admin_only(self, value: bool):
        """设置导出管理员限制"""
        self._plugin._export_admin_only = value
    
    @property
    def record_private_chats(self) -> bool:
        """是否记录私聊"""
        return self._plugin._record_private_chats
    
    @record_private_chats.setter
    def record_private_chats(self, value: bool):
        """设置私聊记录"""
        self._plugin._record_private_chats = value
    
    @property
    def group_whitelist(self) -> list[str]:
        """群白名单"""
        return self._plugin._group_whitelist
    
    @group_whitelist.setter
    def group_whitelist(self, value: list[str]):
        """设置群白名单"""
        self._plugin._group_whitelist = value
    
    @property
    def enable_sanitization(self) -> bool:
        """是否启用数据脱敏"""
        return self._plugin._enable_sanitization
    
    @enable_sanitization.setter
    def enable_sanitization(self, value: bool):
        """设置数据脱敏"""
        self._plugin._enable_sanitization = value
    
    @property
    def max_cache_limit(self) -> int:
        """最大缓存限制"""
        return self._plugin.MAX_CACHE_LIMIT
    
    @property
    def default_segment_len(self) -> int:
        """默认分段长度"""
        return self._plugin.DEFAULT_SEGMENT_LEN
    
    @property
    def default_segment_delay(self) -> float:
        """默认分段延迟"""
        return self._plugin.DEFAULT_SEGMENT_DELAY
    
    @property
    def thread_main_preview_limit(self) -> int:
        """线程主消息预览限制"""
        return self._plugin.THREAD_MAIN_PREVIEW_LIMIT
    
    # ==================== 公共方法 ====================
    
    async def check_admin_permission(self, event: AstrMessageEvent, reason: str) -> Optional[str]:
        """
        检查管理员权限
        
        Args:
            event: 消息事件
            reason: 拒绝原因
            
        Returns:
            如果权限不足返回拒绝消息，否则返回None
        """
        return await self._plugin._admin_denied_message(event, reason)
    
    def get_file_stub_and_name(self, event: AstrMessageEvent) -> tuple[str, str]:
        """
        获取文件存根和名称
        
        Args:
            event: 消息事件
            
        Returns:
            (文件存根, 会话名称)
        """
        return self._plugin._get_file_stub_and_name(event)
    
    async def collect_session_status(self, file_stub: str) -> tuple[int, int, int, int, int, float]:
        """
        收集会话状态
        
        Args:
            file_stub: 文件存根
            
        Returns:
            (会话行数, 线程数, 总会话数, 命中数, 未命中数, 命中率)
        """
        return await self._plugin._collect_session_status(file_stub)
    
    async def save_config(self, key: str, value):
        """
        保存配置到持久化存储
        
        Args:
            key: 配置键
            value: 配置值
        """
        await self._plugin._put_kv_value(key, value)
    
    async def reconfigure_cache_limit(self, new_limit: int):
        """
        重新配置缓存限制
        
        Args:
            new_limit: 新的缓存限制
        """
        async with self._plugin._write_lock:
            self._plugin._reconfigure_cache_limit(new_limit)
    
    def cache_disabled_message(self) -> str:
        """
        获取缓存禁用消息
        
        Returns:
            禁用消息文本
        """
        return self._plugin._cache_disabled_message()
    
    # ==================== 缓存操作方法（封装并发控制） ====================
    
    async def get_session_lines(self, file_stub: str, limit: int) -> List[str]:
        """
        获取会话行（封装并发控制）
        
        Args:
            file_stub: 文件存根
            limit: 限制数量
            
        Returns:
            会话行列表
        """
        safe_limit = self._clamp_limit(limit)
        async with self._plugin._write_lock:
            lines = self._plugin._get_session_lines_unlocked(file_stub)
            return lines[-safe_limit:]
    
    async def get_thread_lines(self, file_stub: str, anchor_id: str, limit: Optional[int] = None) -> List[str]:
        """
        获取线程行（封装并发控制）
        
        Args:
            file_stub: 文件存根
            anchor_id: 锚点ID
            limit: 限制数量
            
        Returns:
            线程行列表
        """
        key = str(anchor_id).strip()
        async with self._plugin._write_lock:
            threads = self._plugin._thread_cache.threads.get(file_stub, OrderedDict())
            entry = threads.get(key)
            if not entry:
                return []
            
            lines = self._plugin._get_thread_lines(entry)
            if isinstance(limit, int) and limit > 0:
                lines = lines[-limit:]
            return lines
    
    async def get_threads_summary(self, file_stub: str, limit: int = 10) -> List[tuple]:
        """
        获取线程摘要（封装并发控制）
        
        Args:
            file_stub: 文件存根
            limit: 限制数量
            
        Returns:
            线程摘要列表 [(key, entry), ...]
        """
        from datetime import datetime
        
        safe_limit = max(1, min(limit, 100))
        async with self._plugin._write_lock:
            threads = self._plugin._thread_cache.threads.get(file_stub, OrderedDict())
            return sorted(
                threads.items(),
                key=lambda item: item[1].last_ts or item[1].first_ts or datetime.min,
                reverse=True,
            )[:safe_limit]
    
    async def send_cache_as_file(
        self,
        event,
        lines: List[str],
        name: str,
        file_stub: str,
        segment_len: int = None,
        segment_delay: float = None
    ):
        """
        发送缓存为文件（封装并发控制）
        
        Args:
            event: 消息事件
            lines: 行列表
            name: 名称
            file_stub: 文件存根
            segment_len: 分段长度
            segment_delay: 分段延迟
        """
        segment_len = segment_len or self.default_segment_len
        segment_delay = segment_delay or self.default_segment_delay
        
        await self._plugin._send_cache_as_file(
            event, lines, name, file_stub,
            segment_len=segment_len,
            segment_delay=segment_delay,
        )
    
    async def send_text_segments(
        self,
        event,
        text: str,
        segment_len: int = None,
        delay: float = None
    ):
        """
        发送文本分段（封装并发控制）
        
        Args:
            event: 消息事件
            text: 文本内容
            segment_len: 分段长度
            delay: 延迟时间
        """
        segment_len = segment_len or self.default_segment_len
        delay = delay or self.default_segment_delay
        
        await self._plugin._send_text_segments(
            event, text,
            segment_len=segment_len,
            delay=delay,
        )
    
    async def bump_stat(self, file_stub: str, hit: bool):
        """
        更新统计信息（封装并发控制）
        
        Args:
            file_stub: 文件存根
            hit: 是否命中
        """
        async with self._plugin._write_lock:
            self._plugin._bump_stat(file_stub, hit)
    
    def _clamp_limit(self, limit: int) -> int:
        """
        限制数量在合理范围内
        
        Args:
            limit: 原始限制
            
        Returns:
            限制后的数量
        """
        return self._plugin._clamp_limit(limit)
