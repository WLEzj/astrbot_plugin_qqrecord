"""
配置服务接口和实现
将配置管理逻辑封装到独立的服务中
"""
from abc import ABC, abstractmethod
from typing import List, Optional

from domain.models import PluginConfig


class IConfigService(ABC):
    """配置服务接口"""
    
    @abstractmethod
    async def get_config(self) -> PluginConfig:
        """获取完整配置"""
        pass
    
    @abstractmethod
    async def update_config(self, config: PluginConfig) -> None:
        """更新配置"""
        pass
    
    @abstractmethod
    async def get_cache_enabled(self) -> bool:
        """获取缓存启用状态"""
        pass
    
    @abstractmethod
    async def set_cache_enabled(self, enabled: bool) -> None:
        """设置缓存启用状态"""
        pass
    
    @abstractmethod
    async def get_cache_limit(self) -> int:
        """获取缓存限制"""
        pass
    
    @abstractmethod
    async def set_cache_limit(self, limit: int) -> None:
        """设置缓存限制"""
        pass
    
    @abstractmethod
    async def get_admin_only(self) -> bool:
        """获取管理员限制状态"""
        pass
    
    @abstractmethod
    async def set_admin_only(self, admin_only: bool) -> None:
        """设置管理员限制状态"""
        pass
    
    @abstractmethod
    async def get_export_admin_only(self) -> bool:
        """获取导出管理员限制状态"""
        pass
    
    @abstractmethod
    async def set_export_admin_only(self, export_admin_only: bool) -> None:
        """设置导出管理员限制状态"""
        pass


class ConfigService(IConfigService):
    """配置服务实现"""
    
    def __init__(self, config_store):
        self._config_store = config_store
        self._config: Optional[PluginConfig] = None
    
    async def get_config(self) -> PluginConfig:
        """获取完整配置"""
        if self._config is None:
            self._config = PluginConfig(
                cache_enabled=await self._config_store.get("cache_enabled", True),
                cache_limit=await self._config_store.get("cache_limit", 500),
                admin_only=await self._config_store.get("admin_only", False),
                export_admin_only=await self._config_store.get("export_admin_only", True),
                record_private_chats=await self._config_store.get("record_private_chats", False),
                group_whitelist=await self._config_store.get("group_whitelist", []),
                enable_sanitization=await self._config_store.get("enable_sanitization", True)
            )
        return self._config
    
    async def update_config(self, config: PluginConfig) -> None:
        """更新配置"""
        await self._config_store.put("cache_enabled", config.cache_enabled)
        await self._config_store.put("cache_limit", config.cache_limit)
        await self._config_store.put("admin_only", config.admin_only)
        await self._config_store.put("export_admin_only", config.export_admin_only)
        await self._config_store.put("record_private_chats", config.record_private_chats)
        await self._config_store.put("group_whitelist", config.group_whitelist)
        await self._config_store.put("enable_sanitization", config.enable_sanitization)
        self._config = config
    
    async def get_cache_enabled(self) -> bool:
        """获取缓存启用状态"""
        return await self._config_store.get("cache_enabled", True)
    
    async def set_cache_enabled(self, enabled: bool) -> None:
        """设置缓存启用状态"""
        await self._config_store.put("cache_enabled", enabled)
        if self._config:
            self._config.cache_enabled = enabled
    
    async def get_cache_limit(self) -> int:
        """获取缓存限制"""
        return await self._config_store.get("cache_limit", 500)
    
    async def set_cache_limit(self, limit: int) -> None:
        """设置缓存限制"""
        await self._config_store.put("cache_limit", limit)
        if self._config:
            self._config.cache_limit = limit
    
    async def get_admin_only(self) -> bool:
        """获取管理员限制状态"""
        return await self._config_store.get("admin_only", False)
    
    async def set_admin_only(self, admin_only: bool) -> None:
        """设置管理员限制状态"""
        await self._config_store.put("admin_only", admin_only)
        if self._config:
            self._config.admin_only = admin_only
    
    async def get_export_admin_only(self) -> bool:
        """获取导出管理员限制状态"""
        return await self._config_store.get("export_admin_only", True)
    
    async def set_export_admin_only(self, export_admin_only: bool) -> None:
        """设置导出管理员限制状态"""
        await self._config_store.put("export_admin_only", export_admin_only)
        if self._config:
            self._config.export_admin_only = export_admin_only