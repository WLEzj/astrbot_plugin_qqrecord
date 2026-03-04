from astrbot.api import logger
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from collections import OrderedDict
from datetime import datetime


class ThreadCommands:
    """线程相关命令处理器"""

    def __init__(self, plugin):
        self._plugin = plugin

    async def record_thread_command(self, event: AstrMessageEvent, anchor_id: str, limit: int | None = None):
        """命令 `/record_thread <anchor_id> [limit]`：按锚点查看该线程内容。"""
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
            key = str(anchor_id).strip()
            async with self._plugin._write_lock:
                threads = self._plugin._thread_cache.threads.get(file_stub, OrderedDict())
                entry = threads.get(key)
                if not entry:
                    yield event.plain_result(f"未找到锚点 {key} 的线程（{name}）。")
                    self._plugin._bump_stat(file_stub, False)
                    return
                lines = self._plugin._get_thread_lines(entry)
                if isinstance(limit, int) and limit > 0:
                    lines = lines[-limit:]

            if not lines:
                yield event.plain_result(f"该线程暂无记录（{name}）。")
                return

            self._plugin._bump_stat(file_stub, True)
            preview = "\n".join(lines)
            yield event.plain_result(
                f"线程 {key} 最近 {len(lines)} 条（{name}）：\n{preview}"
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

    async def record_threads_command(self, event: AstrMessageEvent, limit: int = 10):
        """命令 `/record_threads [limit]`：列出最近的线程摘要。"""
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
            safe_limit = max(1, min(limit, 100))
            async with self._plugin._write_lock:
                threads = self._plugin._thread_cache.threads.get(file_stub, OrderedDict())
                items = sorted(
                    threads.items(),
                    key=lambda item: item[1].last_ts or item[1].first_ts or datetime.min,
                    reverse=True,
                )[:safe_limit]
            if not items:
                self._plugin._bump_stat(file_stub, False)
                yield event.plain_result(f"当前会话暂无线程（{name}）。")
                return

            def _short(s: str, n: int = self._plugin.THREAD_MAIN_PREVIEW_LIMIT) -> str:
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

            self._plugin._bump_stat(file_stub, True)
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
