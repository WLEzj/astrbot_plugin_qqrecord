"""
改进的记录查看命令处理器
解决封装性和并发控制问题
"""
from astrbot.api import logger
from astrbot.core.platform.astr_message_event import AstrMessageEvent

from .extended_plugin_api import ExtendedPluginAPI
from .plugin_api import require_admin


class RecordCommands:
    """记录查看相关命令处理器 - 改进版本"""

    def __init__(self, plugin):
        self._plugin = plugin
        self._api = ExtendedPluginAPI(plugin)

    @require_admin("仅管理员可查看记录。")
    async def record_command(self, event: AstrMessageEvent, limit: int = 10):
        """命令 `/record [limit]`：返回当前会话内存缓存的最近 N 行。"""
        file_stub = None
        try:
            file_stub, name = self._api.get_file_stub_and_name(event)
            if not self._api.cache_enabled:
                yield event.plain_result(self._api.cache_disabled_message())
                return
            
            lines = await self._api.get_session_lines(file_stub, limit)

            if not lines:
                await self._api.bump_stat(file_stub, False)
                yield event.plain_result(f"缓存为空，当前会话：{name}")
                return

            await self._api.bump_stat(file_stub, True)
            preview = "\n".join(lines)
            yield event.plain_result(
                f"最近 {len(lines)} 条缓存记录（{name}）：\n{preview}"
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

    async def record_file_command(
        self,
        event: AstrMessageEvent,
        limit: int = 10,
        fmt: str | None = None,
    ):
        """命令 `/record_file [limit] [fmt]`：导出当前会话缓存为文件。"""
        file_stub = None
        try:
            if self._api.admin_only:
                denied = await self._api.check_admin_permission(
                    event,
                    "仅管理员可导出记录。",
                )
                if denied:
                    yield event.plain_result(denied)
                    return
            
            if self._api.export_admin_only:
                denied = await self._api.check_admin_permission(
                    event,
                    "仅管理员可导出记录。",
                )
                if denied:
                    yield event.plain_result(denied)
                    return
            
            file_stub, name = self._api.get_file_stub_and_name(event)
            if not self._api.cache_enabled:
                yield event.plain_result(self._api.cache_disabled_message())
                return
            
            lines = await self._api.get_session_lines(file_stub, limit)

            if fmt and str(fmt).lower() == "md":
                lines = [f"# 记录（{name}）", "", *lines]

            await self._api.send_cache_as_file(
                event, lines, name, file_stub,
                segment_len=self._api.default_segment_len,
                segment_delay=self._api.default_segment_delay,
            )
            await self._api.bump_stat(file_stub, bool(lines))
        except (ValueError, TypeError) as exc:
            logger.warning("QQRecord /record_file 参数错误: %s", exc, exc_info=exc)
            yield event.plain_result("参数无效，请检查后重试。")
        except (RuntimeError, OSError) as exc:
            logger.warning("QQRecord /record_file 命令失败: %s", exc, exc_info=exc)
            try:
                file_stub, name = self._api.get_file_stub_and_name(event)
                lines = await self._api.get_session_lines(file_stub, limit)
                
                if fmt and str(fmt).lower() == "md":
                    lines = [f"# 记录（{name}）", "", *lines]
                
                content = "\n".join(lines)
                await self._api.send_text_segments(
                    event,
                    f"记录文件发送失败（可能未配置回调地址或平台不支持文件），改为文本：\n{content}",
                    segment_len=self._api.default_segment_len,
                    delay=self._api.default_segment_delay,
                )
            except Exception as fallback_exc:
                self._plugin._log_debug_exception(
                    "QQRecord /record_file 兜底失败",
                    fallback_exc,
                    stub=file_stub if "file_stub" in locals() else None,
                    limit=limit,
                )
                yield event.plain_result("导出记录为文件时出现错误，请稍后重试。")
        except Exception as exc:
            logger.exception("QQRecord /record_file 命令异常: %s", exc)
            yield event.plain_result("命令执行失败，请稍后重试。")
