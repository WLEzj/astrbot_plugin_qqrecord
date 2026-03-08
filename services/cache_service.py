"""
缓存服务实现
将现有的ThreadCache逻辑封装到服务接口中
"""
import asyncio
from datetime import datetime
from typing import List, Optional

from domain.interfaces import ICacheService
from domain.models import MessageRecord, SessionInfo, CacheStats
from services import ThreadCache, StatsTracker


class CacheService(ICacheService):
    """缓存服务实现"""
    
    def __init__(
        self,
        thread_cache: ThreadCache,
        stats_tracker: StatsTracker,
        write_lock: asyncio.Lock,
        initial_cache_limit: int = 500,
        max_sessions: int = 500
    ):
        self._thread_cache = thread_cache
        self._stats_tracker = stats_tracker
        self._write_lock = write_lock
        self._cache_limit = initial_cache_limit
        self._max_sessions = max_sessions
    
    async def add_message(self, session_id: str, message: MessageRecord) -> None:
        """添加消息到缓存"""
        async with self._write_lock:
            self._thread_cache.write_threaded(
                stub=session_id,
                anchor_id=message.anchor_id,
                is_reply=message.is_reply,
                line=message.content,
                cache_limit=self._cache_limit,
                max_sessions=self._max_sessions,
            )
    
    async def get_messages(self, session_id: str, limit: int) -> List[str]:
        """获取会话消息"""
        async with self._write_lock:
            lines = self._thread_cache.get_session_lines_unlocked(session_id)
            return lines[-limit:] if limit > 0 else lines
    
    async def get_session_info(self, session_id: str) -> Optional[SessionInfo]:
        """获取会话信息"""
        async with self._write_lock:
            threads = self._thread_cache.threads.get(session_id)
            if not threads:
                return None
            
            message_count = 0
            for entry in threads.values():
                if entry.main:
                    message_count += 1
                message_count += len(entry.replies)
            
            last_seen = self._thread_cache.last_seen.get(session_id, datetime.now())
            
            return SessionInfo(
                session_id=session_id,
                name=session_id,  # 可以从外部获取更友好的名称
                message_count=message_count,
                thread_count=len(threads),
                last_seen=last_seen
            )
    
    async def get_all_sessions(self) -> List[SessionInfo]:
        """获取所有会话信息"""
        sessions = []
        async with self._write_lock:
            for session_id in self._thread_cache.all_session_keys():
                threads = self._thread_cache.threads.get(session_id)
                if threads:
                    message_count = sum(
                        (1 if entry.main else 0) + len(entry.replies)
                        for entry in threads.values()
                    )
                    last_seen = self._thread_cache.last_seen.get(session_id, datetime.now())
                    
                    sessions.append(SessionInfo(
                        session_id=session_id,
                        name=session_id,
                        message_count=message_count,
                        thread_count=len(threads),
                        last_seen=last_seen
                    ))
        return sessions
    
    async def cleanup_inactive_sessions(self, max_age_hours: int) -> int:
        """清理不活跃会话"""
        removed = 0
        removed_keys: List[str] = []
        
        cutoff = datetime.now() - datetime.timedelta(hours=max_age_hours)
        
        async with self._write_lock:
            for key in sorted(self._thread_cache.all_session_keys()):
                last = self._thread_cache.last_seen.get(key)
                if last is None or last < cutoff:
                    try:
                        self._thread_cache.last_seen.pop(key, None)
                        self._thread_cache.threads.pop(key, None)
                        self._stats_tracker.stats.pop(key, None)
                        removed += 1
                        removed_keys.append(key)
                    except Exception as exc:
                        # 记录错误但不中断清理过程
                        pass
        
        return removed
    
    async def get_cache_stats(self, session_id: str) -> CacheStats:
        """获取缓存统计信息"""
        stats = self._stats_tracker.get(session_id)
        return CacheStats.from_counts(
            hit=stats.get("hit", 0),
            miss=stats.get("miss", 0)
        )
    
    async def update_cache_limit(self, new_limit: int) -> None:
        """更新缓存限制"""
        async with self._write_lock:
            self._cache_limit = new_limit
            # 更新现有会话的容量限制
            for stub in self._thread_cache.threads.keys():
                self._thread_cache.enforce_session_capacity(stub, new_limit)
    
    async def get_cache_limit(self) -> int:
        """获取当前缓存限制"""
        return self._cache_limit