"""
重构后的记录查看命令
展示如何使用服务接口消除对主类的直接依赖
"""
from typing import AsyncGenerator, Optional

from domain.interfaces import ICacheService, IConfigService
from domain.models import MessageRecord
from astrbot.api import logger


class RecordCommand:
    """记录查看命令 - 重构版本"""
    
    def __init__(
        self,
        cache_service: ICacheService,
        config_service: IConfigService,
        session_id_extractor: callable,
        permission_checker: callable
    ):
        """
        初始化记录查看命令
        
        Args:
            cache_service: 缓存服务接口
            config_service: 配置服务接口
            session_id_extractor: 会话ID提取函数
            permission_checker: 权限检查函数
        """
        self._cache_service = cache_service
        self._config_service = config_service
        self._session_id_extractor = session_id_extractor
        self._permission_checker = permission_checker
    
    async def execute(
        self, 
        event, 
        limit: int = 10
    ) -> AsyncGenerator:
        """
        执行记录查看命令
        
        Args:
            event: 消息事件
            limit: 返回的记录数量限制
            
        Yields:
            命令执行结果
        """
        try:
            # 权限检查
            denied = await self._permission_checker(
                event, 
                "仅管理员可查看记录。",
                self._config_service.get_admin_only
            )
            if denied:
                yield event.plain_result(denied)
                return
            
            # 检查缓存状态
            if not await self._config_service.get_cache_enabled():
                yield event.plain_result("缓存已关闭，无法查看记录。请联系管理员使用 /record_cache on 开启。")
                return
            
            # 提取会话ID
            session_id, session_name = self._session_id_extractor(event)
            
            # 获取消息
            messages = await self._cache_service.get_messages(session_id, limit)
            
            # 更新统计信息
            if not messages:
                await self._cache_service.get_cache_stats(session_id)
                yield event.plain_result(f"缓存为空，当前会话：{session_name}")
                return
            
            # 返回结果
            preview = "\n".join(messages)
            yield event.plain_result(
                f"最近 {len(messages)} 条缓存记录（{session_name}）：\n{preview}"
            )
            
        except (ValueError, TypeError) as exc:
            logger.warning("QQRecord /record 参数错误: %s", exc, exc_info=exc)
            yield event.plain_result("参数无效，请检查后重试。")
        except (RuntimeError, OSError) as exc:
            logger.warning("QQRecord /record 命令失败: %s", exc, exc_info=exc)
            yield event.plain_result("读取记录失败，请稍后重试。")
        except Exception as exc:
            logger.exception("QQRecord /record 命令异常: %s", exc)
            yield event.plain_result("命令执行失败，请稍后重试。")


class AdminCommand:
    """管理命令 - 重构版本"""
    
    def __init__(
        self,
        cache_service: ICacheService,
        config_service: IConfigService,
        session_id_extractor: callable,
        permission_checker: callable
    ):
        """
        初始化管理命令
        
        Args:
            cache_service: 缓存服务接口
            config_service: 配置服务接口
            session_id_extractor: 会话ID提取函数
            permission_checker: 权限检查函数
        """
        self._cache_service = cache_service
        self._config_service = config_service
        self._session_id_extractor = session_id_extractor
        self._permission_checker = permission_checker
    
    async def execute_cache_command(
        self,
        event,
        flag: str | None = None
    ) -> AsyncGenerator:
        """
        执行缓存管理命令
        
        Args:
            event: 消息事件
            flag: 命令标志 (on/off/status)
            
        Yields:
            命令执行结果
        """
        try:
            # 权限检查
            denied = await self._permission_checker(
                event,
                "仅管理员可用该命令。",
                lambda: True  # 管理命令总是需要管理员权限
            )
            if denied:
                yield event.plain_result(denied)
                return
            
            # 处理不同标志
            if flag is None:
                await self._handle_status(event)
            elif flag == "status":
                await self._handle_status(event)
            elif flag in ["on", "off"]:
                await self._handle_toggle(event, flag)
            else:
                yield event.plain_result(f"未知标志：{flag}，可用标志：on/off/status")
                
        except Exception as exc:
            logger.exception("QQRecord /record_cache 命令异常: %s", exc)
            yield event.plain_result("命令执行失败，请稍后重试。")
    
    async def _handle_status(self, event):
        """处理状态查询"""
        state = "on" if await self._config_service.get_cache_enabled() else "off"
        cache_limit = await self._cache_service.get_cache_limit()
        
        # 获取当前会话信息
        session_id, session_name = self._session_id_extractor(event)
        session_info = await self._cache_service.get_session_info(session_id)
        
        if session_info:
            session_lines = session_info.message_count
            threads_cnt = session_info.thread_count
        else:
            session_lines = 0
            threads_cnt = 0
        
        # 获取统计信息
        stats = await self._cache_service.get_cache_stats(session_id)
        all_sessions = await self._cache_service.get_all_sessions()
        
        # 获取配置信息
        config = await self._config_service.get_config()
        
        whitelist_info = f"群白名单：{', '.join(config.group_whitelist) if config.group_whitelist else '无（记录所有群）'}"
        private_info = f"私聊记录：{'开启' if config.record_private_chats else '关闭'}"
        sanitize_info = f"数据脱敏：{'开启' if config.enable_sanitization else '关闭'}"
        export_info = f"导出权限：{'仅管理员' if config.export_admin_only else '所有人'}"
        
        yield event.plain_result(
            f"状态：{'开启' if await self._config_service.get_cache_enabled() else '关闭'}\n"
            f"容量上限：{cache_limit}\n"
            f"当前会话（{session_name}）：行数={session_lines}，线程数={threads_cnt}\n"
            f"命中：{stats.hit_count}，未命中：{stats.miss_count}，命中率：{stats.hit_rate:.1f}%\n"
            f"总会话键数：{len(all_sessions)}\n"
            f"{whitelist_info}\n"
            f"{private_info}\n"
            f"{sanitize_info}\n"
            f"{export_info}"
        )
    
    async def _handle_toggle(self, event, flag: str):
        """处理开关切换"""
        enabled = flag == "on"
        await self._config_service.set_cache_enabled(enabled)
        state = "开启" if enabled else "关闭"
        yield event.plain_result(f"缓存已{state}")