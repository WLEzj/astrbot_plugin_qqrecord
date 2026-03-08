"""
领域模型定义
定义插件的核心数据结构和业务概念
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List


@dataclass
class MessageRecord:
    """消息记录领域模型"""
    content: str
    timestamp: datetime
    is_reply: bool = False
    anchor_id: Optional[str] = None
    
    def to_text(self) -> str:
        """转换为文本格式"""
        return f"[{self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}] {self.content}"


@dataclass
class SessionInfo:
    """会话信息领域模型"""
    session_id: str
    name: str
    message_count: int
    thread_count: int
    last_seen: datetime
    
    def is_active(self, max_age_hours: int = 24) -> bool:
        """检查会话是否活跃"""
        cutoff = datetime.now() - datetime.timedelta(hours=max_age_hours)
        return self.last_seen >= cutoff


@dataclass
class CacheStats:
    """缓存统计信息"""
    hit_count: int
    miss_count: int
    hit_rate: float
    
    @classmethod
    def from_counts(cls, hit: int, miss: int) -> "CacheStats":
        """从命中次数创建统计信息"""
        total = hit + miss
        hit_rate = (hit / total * 100) if total > 0 else 0.0
        return cls(hit_count=hit, miss_count=miss, hit_rate=hit_rate)


@dataclass
class PluginConfig:
    """插件配置领域模型"""
    cache_enabled: bool = True
    cache_limit: int = 500
    admin_only: bool = False
    export_admin_only: bool = True
    record_private_chats: bool = False
    group_whitelist: List[str] = None
    enable_sanitization: bool = True
    
    def __post_init__(self):
        if self.group_whitelist is None:
            self.group_whitelist = []
    
    def is_group_allowed(self, group_id: str) -> bool:
        """检查群组是否允许记录"""
        if not self.group_whitelist:
            return True
        return str(group_id) in self.group_whitelist
