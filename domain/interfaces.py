"""
缓存服务接口定义
定义缓存操作的标准接口，实现依赖倒置原则
"""
from abc import ABC, abstractmethod
from typing import List, Optional
from domain.models import MessageRecord, SessionInfo, CacheStats


class ICacheService(ABC):
    """缓存服务接口"""
    
    @abstractmethod
    async def add_message(self, session_id: str, message: MessageRecord) -> None:
        """添加消息到缓存"""
        pass
    
    @abstractmethod
    async def get_messages(self, session_id: str, limit: int) -> List[str]:
        """获取会话消息"""
        pass
    
    @abstractmethod
    async def get_session_info(self, session_id: str) -> Optional[SessionInfo]:
        """获取会话信息"""
        pass
    
    @abstractmethod
    async def get_all_sessions(self) -> List[SessionInfo]:
        """获取所有会话信息"""
        pass
    
    @abstractmethod
    async def cleanup_inactive_sessions(self, max_age_hours: int) -> int:
        """清理不活跃会话，返回清理的会话数量"""
        pass
    
    @abstractmethod
    async def get_cache_stats(self, session_id: str) -> CacheStats:
        """获取缓存统计信息"""
        pass
    
    @abstractmethod
    async def update_cache_limit(self, new_limit: int) -> None:
        """更新缓存限制"""
        pass
    
    @abstractmethod
    async def get_cache_limit(self) -> int:
        """获取当前缓存限制"""
        pass