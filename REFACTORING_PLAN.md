"""
重构后的架构设计文档

## 当前架构问题

### 1. 职责过重
QQRecordPlugin承担了过多职责，违反单一职责原则：
- 配置管理
- 缓存管理  
- 导出功能
- 清理调度
- 命令路由
- 权限检查
- 消息处理

### 2. 紧耦合
子命令处理器直接访问主类的私有属性，导致：
- 难以单元测试
- 修改影响面大
- 代码复用性差

### 3. 缺乏接口抽象
没有定义清晰的接口和边界，所有组件都依赖具体实现。

## 重构目标

1. **分离关注点**：每个模块只负责一个明确的职责
2. **降低耦合**：通过接口和依赖注入实现松耦合
3. **提高可测试性**：每个组件都可以独立测试
4. **增强可维护性**：修改一个模块不影响其他模块

## 新架构设计

### 1. 领域层 (Domain Layer)

```python
# domain/models.py
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class MessageRecord:
    """消息记录领域模型"""
    content: str
    timestamp: datetime
    is_reply: bool = False
    anchor_id: Optional[str] = None

@dataclass
class SessionInfo:
    """会话信息领域模型"""
    session_id: str
    name: str
    message_count: int
    thread_count: int
    last_seen: datetime

@dataclass
class CacheStats:
    """缓存统计信息"""
    hit_count: int
    miss_count: int
    hit_rate: float
```

### 2. 服务层 (Service Layer)

```python
# services/cache_service.py
from abc import ABC, abstractmethod
from typing import List, Optional

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
    async def cleanup_inactive_sessions(self, max_age_hours: int) -> int:
        """清理不活跃会话"""
        pass

class CacheService(ICacheService):
    """缓存服务实现"""
    
    def __init__(self, cache_limit: int, max_sessions: int):
        self._cache_limit = cache_limit
        self._max_sessions = max_sessions
        self._thread_cache = ThreadCache(...)
        self._write_lock = asyncio.Lock()
    
    async def add_message(self, session_id: str, message: MessageRecord) -> None:
        async with self._write_lock:
            self._thread_cache.write_threaded(
                stub=session_id,
                anchor_id=message.anchor_id,
                is_reply=message.is_reply,
                line=message.content,
                cache_limit=self._cache_limit,
                max_sessions=self._max_sessions,
            )
```

### 3. 配置层 (Configuration Layer)

```python
# services/config_service.py
from abc import ABC, abstractmethod

class IConfigService(ABC):
    """配置服务接口"""
    
    @abstractmethod
    async def get_cache_enabled(self) -> bool:
        pass
    
    @abstractmethod
    async def set_cache_enabled(self, enabled: bool) -> None:
        pass
    
    @abstractmethod
    async def get_cache_limit(self) -> int:
        pass
    
    @abstractmethod
    async def set_cache_limit(self, limit: int) -> None:
        pass

class ConfigService(IConfigService):
    """配置服务实现"""
    
    def __init__(self, config_store: ConfigStore):
        self._config_store = config_store
    
    async def get_cache_enabled(self) -> bool:
        return await self._config_store.get("cache_enabled", True)
    
    async def set_cache_enabled(self, enabled: bool) -> None:
        await self._config_store.put("cache_enabled", enabled)
```

### 4. 导出层 (Export Layer)

```python
# services/export_service.py
from abc import ABC, abstractmethod

class IExportService(ABC):
    """导出服务接口"""
    
    @abstractmethod
    async def export_to_file(
        self, 
        session_id: str, 
        messages: List[str],
        format: str = "txt"
    ) -> str:
        """导出消息到文件"""
        pass

class ExportService(IExportService):
    """导出服务实现"""
    
    def __init__(
        self,
        temp_dir_provider: callable,
        is_safe_path: callable,
        temp_file_prefix: str
    ):
        self._temp_dir_provider = temp_dir_provider
        self._is_safe_path = is_safe_path
        self._temp_file_prefix = temp_file_prefix
    
    async def export_to_file(
        self, 
        session_id: str, 
        messages: List[str],
        format: str = "txt"
    ) -> str:
        # 实现导出逻辑
        pass
```

### 5. 权限层 (Permission Layer)

```python
# services/permission_service.py
from abc import ABC, abstractmethod

class IPermissionService(ABC):
    """权限服务接口"""
    
    @abstractmethod
    async def check_admin_permission(self, event) -> Optional[str]:
        """检查管理员权限"""
        pass
    
    @abstractmethod
    async def check_export_permission(self, event) -> Optional[str]:
        """检查导出权限"""
        pass

class PermissionService(IPermissionService):
    """权限服务实现"""
    
    def __init__(self, config_service: IConfigService):
        self._config_service = config_service
    
    async def check_admin_permission(self, event) -> Optional[str]:
        # 实现权限检查逻辑
        pass
```

### 6. 命令处理器重构

```python
# commands/record_command.py
from typing import AsyncGenerator

class RecordCommand:
    """记录查看命令"""
    
    def __init__(
        self,
        cache_service: ICacheService,
        config_service: IConfigService,
        permission_service: IPermissionService
    ):
        self._cache_service = cache_service
        self._config_service = config_service
        self._permission_service = permission_service
    
    async def execute(
        self, 
        event, 
        limit: int = 10
    ) -> AsyncGenerator:
        """执行记录查看命令"""
        # 权限检查
        denied = await self._permission_service.check_admin_permission(event)
        if denied:
            yield event.plain_result(denied)
            return
        
        # 检查缓存状态
        if not await self._config_service.get_cache_enabled():
            yield event.plain_result("缓存已关闭")
            return
        
        # 获取会话ID
        session_id = self._extract_session_id(event)
        
        # 获取消息
        messages = await self._cache_service.get_messages(session_id, limit)
        
        # 返回结果
        if not messages:
            yield event.plain_result("缓存为空")
            return
        
        yield event.plain_result(f"最近 {len(messages)} 条记录")
    
    def _extract_session_id(self, event) -> str:
        """提取会话ID"""
        # 实现会话ID提取逻辑
        pass
```

### 7. 重构后的主插件类

```python
# main.py
class QQRecordPlugin(Star):
    """QQ记录插件 - 重构版本"""
    
    def __init__(self, context: Context):
        super().__init__(context)
        
        # 初始化服务层
        self._init_services()
        
        # 初始化命令处理器
        self._init_commands()
    
    def _init_services(self):
        """初始化服务层"""
        # 配置服务
        self._config_store = ConfigStore(...)
        self._config_service = ConfigService(self._config_store)
        
        # 缓存服务
        cache_limit = await self._config_service.get_cache_limit()
        self._cache_service = CacheService(
            cache_limit=cache_limit,
            max_sessions=500
        )
        
        # 导出服务
        self._export_service = ExportService(
            temp_dir_provider=self._temp_dir_provider,
            is_safe_path=self._is_safe_path,
            temp_file_prefix=self.TEMP_FILE_PREFIX
        )
        
        # 权限服务
        self._permission_service = PermissionService(self._config_service)
    
    def _init_commands(self):
        """初始化命令处理器"""
        self._record_command = RecordCommand(
            cache_service=self._cache_service,
            config_service=self._config_service,
            permission_service=self._permission_service
        )
        
        self._admin_command = AdminCommand(
            cache_service=self._cache_service,
            config_service=self._config_service,
            permission_service=self._permission_service
        )
    
    @filter.command("record")
    async def record_command_handler(self, event, limit: int = 10):
        """处理record命令"""
        async for result in self._record_command.execute(event, limit):
            yield result
```

## 重构优势

### 1. 单一职责
每个类只负责一个明确的职责，符合SOLID原则。

### 2. 依赖倒置
高层模块不依赖低层模块，都依赖于抽象。

### 3. 可测试性
每个组件都可以独立测试，可以轻松mock依赖。

### 4. 可维护性
修改一个模块不影响其他模块，降低维护成本。

### 5. 可扩展性
新增功能时，只需实现相应的接口，无需修改现有代码。

## 重构步骤

1. **第一阶段：定义接口**
   - 创建所有服务接口
   - 定义领域模型

2. **第二阶段：实现服务**
   - 实现各个服务类
   - 迁移现有逻辑到服务层

3. **第三阶段：重构命令处理器**
   - 重构命令处理器使用服务接口
   - 移除对主类的直接依赖

4. **第四阶段：简化主插件类**
   - 主插件类只负责初始化和路由
   - 移除业务逻辑

5. **第五阶段：测试和优化**
   - 为每个服务编写单元测试
   - 性能优化和代码清理

## 总结

通过这种重构，我们将实现：
- 清晰的架构边界
- 松耦合的组件设计
- 高可测试性的代码
- 易于维护和扩展的代码库
