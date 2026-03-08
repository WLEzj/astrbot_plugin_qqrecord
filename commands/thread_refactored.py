"""
改进的线程命令处理器
解决封装性和并发控制问题
"""
from astrbot.api import logger
from astrbot.core.platform.astr_message_event import AstrMessageEvent

from .extended_plugin_api import ExtendedPluginAPI
from .plugin_api import require_admin


class ThreadCommands:
    """线程相关命令处理器 - 改进版本"""

    def __init__(self, plugin):
        self._plugin = plugin
        self._api = ExtendedPluginAPI(plugin)

    @require_admin("仅管理员可查看记录。")
    async def record_thread_command(self, event: AstrMessageEvent, anchor_id: str, limit: int | None = None):
        """命令 `/record_thread <anchor_id> [limit]`：按锚点查看该线程内容。"""
        file_stub = None
        try:
            file_stub, name = self._api.get_file_stub_and_name(event)
            if not self._api.cache_enabled:
                yield event.plain_result(self._api.cache_disabled_message())
                return
            
            lines = await self._api.get_thread_lines(file_stub, anchor_id, limit)

            if not lines:
                yield event.plain_result(f"未找到锚点 {anchor_id} 的线程（{name}）。")
                await self._api.bump_stat(file_stub, False)
                return

            if not lines:
                yield event.plain_result(f"该线程暂无记录（{name}）。")
                return

            await self._api.bump_stat(file_stub, True)
            preview = "\n".join(lines)
            yield event.plain_result(
                f"线程 {anchor_id} 最近 {len(lines)} 条（{name}）：\n{preview}"
            )
        except (ValueError, TypeError) as exc:
            logger.warning("QQRecord /record_thread 参数错误: %s", exc, exc_info=exc)
            yield event.plain_result("参数无效，请检查后重试。")
        except (RuntimeError, OSError) as exc:
            logger.warning("QQRecord /record_thread 命令失败: %s", exc, exc_info=exc)
            yield event.plain_result("读取线程记录失败，请稍后重试。")
        except Exception as exc:
            logger.exception("QQRecord /record_thread 命令异常: %s", exc)
            yield event.plain_result("命令执行失败，请稍后重试。")

    @require_admin("仅管理员可查看记录。")
    async def record_threads_command(self, event: AstrMessageEvent, limit: int = 10):
        """命令 `/record_threads [limit]`：列出最近的线程摘要。"""
        file_stub = None
        try:
            file_stub, name = self._api.get_file_stub_and_name(event)
            if not self._api.cache_enabled:
                yield event.plain_result(self._api.cache_disabled_message())
                return
            
            items = await self._api.get_threads_summary(file_stub, limit)
            
            if not items:
                await self._api.bump_stat(file_stub, False)
                yield event.plain_result(f"当前会话暂无线程（{name}）。")
                return

            def _short(s: str, n: int = self._api.thread_main_preview_limit) -> str:
                s = (s or "").strip()
                return s if len(s) <= n else s[:n] + "…"

            lines = []
            for key, entry in items:
                main = entry.main or "(无主消息)"
                cnt = 1 if entry.main else 0
                cnt += len(entry.replies)
                ts = entry.last_ts or entry.first_ts
                ts_str = ts.strftime("%Y-%m-%d %H:%M:%S") if ts else "-"
                lines.append(
                    f"anchor={key} | count={cnt} | last={ts_str} | main={_short(main)}"
                )

            await self._api.bump_stat(file_stub, True)
            preview = "\n".join(lines)
            tip = "\n提示：使用 /record_thread <anchor_id> 查看该线程详情。"
            yield event.plain_result(
                f"最近 {len(items)} 个线程摘要（{name}）：\n{preview}{tip}"
            )
        except (ValueError, TypeError) as exc:
            logger.warning("QQRecord /record_threads 参数错误: %s", exc, exc_info=exc)
            yield event.plain_result("参数无效，请检查后重试。")
        except (RuntimeError, OSError) as exc:
            logger.warning("QQRecord /record_threads 命令失败: %s", exc, exc_info=exc)
            yield event.plain_result("列出线程摘要失败，请稍后重试。")
        except Exception as exc:
            logger.exception("QQRecord /record_threads 命令异常: %s", exc)
            yield event.plain_result("命令执行失败，请稍后重试。")
