from astrbot.api import logger
from astrbot.core.platform.astr_message_event import AstrMessageEvent


class RecordCommands:
    """记录查看相关命令处理器"""

    def __init__(self, plugin):
        self._plugin = plugin

    async def record_command(self, event: AstrMessageEvent, limit: int = 10):
        """命令 `/record [limit]`：返回当前会话内存缓存的最近 N 行。"""
        try:
            if self._plugin._admin_only:
                denied = await self._plugin._admin_denied_message(
                    event,
                    "仅管理员可查看记录。",
                )
                if denied:
                    yield event.plain_result(denied)
                    return
            file_stub, name = self._plugin._get_file_stub_and_name(event)
            if not self._plugin._cache_enabled:
                yield event.plain_result(self._plugin._cache_disabled_message())
                return
            safe_limit = self._plugin._clamp_limit(limit)
            async with self._plugin._write_lock:
                lines = self._plugin._get_session_lines_unlocked(file_stub)
                lines = lines[-safe_limit:]

            if not lines:
                self._plugin._bump_stat(file_stub, False)
                yield event.plain_result(f"缓存为空，当前会话：{name}")
                return

            self._plugin._bump_stat(file_stub, True)
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
        try:
            if self._plugin._admin_only:
                denied = await self._plugin._admin_denied_message(
                    event,
                    "仅管理员可导出记录。",
                )
                if denied:
                    yield event.plain_result(denied)
                    return
            if self._plugin._export_admin_only:
                denied = await self._plugin._admin_denied_message(
                    event,
                    "仅管理员可导出记录。",
                )
                if denied:
                    yield event.plain_result(denied)
                    return
            file_stub, name = self._plugin._get_file_stub_and_name(event)
            if not self._plugin._cache_enabled:
                yield event.plain_result(self._plugin._cache_disabled_message())
                return
            safe_limit = self._plugin._clamp_limit(limit)
            async with self._plugin._write_lock:
                lines = self._plugin._get_session_lines_unlocked(file_stub)
                lines = lines[-safe_limit:]

            if fmt and str(fmt).lower() == "md":
                lines = [f"# 记录（{name}）", "", *lines]

            await self._plugin._send_cache_as_file(
                event, lines, name, file_stub,
                segment_len=self._plugin.DEFAULT_SEGMENT_LEN,
                segment_delay=self._plugin.DEFAULT_SEGMENT_DELAY,
            )
            self._plugin._bump_stat(file_stub, bool(lines))
        except (ValueError, TypeError) as exc:
            logger.warning("QQRecord /record_file 参数错误: %s", exc, exc_info=exc)
            yield event.plain_result("参数无效，请检查后重试。")
        except (RuntimeError, OSError) as exc:
            logger.warning("QQRecord /record_file 命令失败: %s", exc, exc_info=exc)
            try:
                file_stub, name = self._plugin._get_file_stub_and_name(event)
                safe_limit = self._plugin._clamp_limit(limit)
                async with self._plugin._write_lock:
                    lines = self._plugin._get_session_lines_unlocked(file_stub)
                    lines = lines[-safe_limit:]
                if fmt and str(fmt).lower() == "md":
                    lines = [f"# 记录（{name}）", "", *lines]
                content = "\n".join(lines)
                await self._plugin._send_text_segments(
                    event,
                    f"记录文件发送失败（可能未配置回调地址或平台不支持文件），改为文本：\n{content}",
                    segment_len=self._plugin.DEFAULT_SEGMENT_LEN,
                    delay=self._plugin.DEFAULT_SEGMENT_DELAY,
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
