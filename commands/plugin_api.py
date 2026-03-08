"""
改进的插件公共接口
为命令处理器提供清晰的公共API，避免直接访问私有成员
"""
from typing import Callable, Optional
from functools import wraps
from astrbot.core.platform.astr_message_event import AstrMessageEvent


def require_admin(denied_message: str = "仅管理员可用该命令。"):
    """
    权限检查装饰器 - 遵循DRY原则
    
    Args:
        denied_message: 权限不足时的提示信息
        
    Returns:
        装饰器函数
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(self, event: AstrMessageEvent, *args, **kwargs):
            # 检查权限
            denied = await self._plugin.check_admin_permission(event, denied_message)
            if denied:
                yield event.plain_result(denied)
                return
            
            # 执行原方法
            async for result in func(self, event, *args, **kwargs):
                yield result
        
        return wrapper
    return decorator


class PluginPublicAPI:
    """
    插件公共接口封装
    提供对命令处理器友好的公共API，避免直接访问私有成员
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
