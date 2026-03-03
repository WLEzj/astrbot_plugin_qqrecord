import os
import asyncio
from datetime import datetime, timedelta

from astrbot.api import logger


class CleanupScheduler:
    def __init__(
        self,
        *,
        thread_cache,
        is_safe_path,
        temp_dir_provider,
        temp_file_prefix: str,
        cleanup_hour: int,
        cleanup_minute: int,
        cleanup_preview_limit: int,
        cleanup_backoff_seconds: int,
        cleanup_backoff_max_seconds: int,
        log_debug_exception,
    ):
        self._thread_cache = thread_cache
        self._is_safe_path = is_safe_path
        self._temp_dir_provider = temp_dir_provider
        self._temp_file_prefix = temp_file_prefix
        self._cleanup_hour = cleanup_hour
        self._cleanup_minute = cleanup_minute
        self._cleanup_preview_limit = cleanup_preview_limit
        self._cleanup_backoff_seconds = cleanup_backoff_seconds
        self._cleanup_backoff_max_seconds = cleanup_backoff_max_seconds
        self._log_debug_exception = log_debug_exception
        self._task: asyncio.Task | None = None

    def start(self, *, max_age_hours: int, temp_cleanup_hours: int):
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(
            self._cleanup_loop(
                max_age_hours=max_age_hours,
                temp_cleanup_hours=temp_cleanup_hours,
            )
        )

    async def stop(self):
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    def seconds_until_next_cleanup_time(self) -> float:
        now = datetime.now()
        next_run = now.replace(
            hour=self._cleanup_hour,
            minute=self._cleanup_minute,
            second=0,
            microsecond=0,
        )
        if now >= next_run:
            next_run += timedelta(days=1)
        return max(1.0, (next_run - now).total_seconds())

    async def run_once(self, *, max_age_hours: int, temp_cleanup_hours: int):
        await self.cleanup_inactive(max_age_hours=max_age_hours)
        await self.cleanup_temp_files(max_age_hours=temp_cleanup_hours)

    async def run_loop(self, *, max_age_hours: int, temp_cleanup_hours: int):
        await self._cleanup_loop(
            max_age_hours=max_age_hours,
            temp_cleanup_hours=temp_cleanup_hours,
        )

    async def _cleanup_loop(self, *, max_age_hours: int, temp_cleanup_hours: int):
        failures = 0
        while True:
            try:
                delay = self.seconds_until_next_cleanup_time()
                await asyncio.sleep(delay)
                await self.cleanup_inactive(max_age_hours=max_age_hours)
                await self.cleanup_temp_files(max_age_hours=temp_cleanup_hours)
                failures = 0
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("QQRecord 清理循环异常: %s", exc)
                failures += 1
                backoff = min(
                    self._cleanup_backoff_seconds * (2 ** min(failures - 1, 5)),
                    self._cleanup_backoff_max_seconds,
                )
                await asyncio.sleep(backoff)

    async def cleanup_temp_files(self, max_age_hours: int):
        """清理过期的临时导出文件，避免目录堆积。"""
        try:
            temp_dir = self._temp_dir_provider()
            if not temp_dir or not os.path.isdir(temp_dir):
                return
            cutoff = datetime.now().timestamp() - (max_age_hours * 3600)
            removed = 0
            for name in os.listdir(temp_dir):
                if not name.startswith(self._temp_file_prefix):
                    continue
                path = os.path.join(temp_dir, name)
                if not self._is_safe_path(temp_dir, path):
                    continue
                try:
                    if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                        os.remove(path)
                        removed += 1
                except OSError as exc:
                    self._log_debug_exception("QQRecord 清理临时文件失败", exc, path=path)
            if removed:
                logger.info("QQRecord 临时文件清理完成：%s 个", removed)
        except (OSError, ValueError) as exc:
            self._log_debug_exception("QQRecord 临时文件清理异常", exc)

    async def cleanup_inactive(self, max_age_hours: int):
        """清理长时间未更新的会话缓存键，默认 24 小时未活动即清理。"""
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        removed = 0
        removed_keys: list[str] = []
        for key in sorted(self._thread_cache.all_session_keys()):
            last = self._thread_cache.last_seen.get(key)
            if last is None or last < cutoff:
                try:
                    self._thread_cache.last_seen.pop(key, None)
                    self._thread_cache.threads.pop(key, None)
                    self._thread_cache.stats_tracker.stats.pop(key, None)
                    removed += 1
                    removed_keys.append(key)
                except Exception as exc:
                    self._log_debug_exception(
                        "QQRecord 清理会话失败",
                        exc,
                        stub=key,
                    )
        if removed:
            try:
                preview = ", ".join(removed_keys[: self._cleanup_preview_limit])
                more = "..." if len(removed_keys) > self._cleanup_preview_limit else ""
                now = datetime.now()
                next_run = now.replace(
                    hour=self._cleanup_hour,
                    minute=self._cleanup_minute,
                    second=0,
                    microsecond=0,
                )
                if now >= next_run:
                    next_run += timedelta(days=1)
                next_str = next_run.strftime("%Y-%m-%d %H:%M:%S")
                logger.info(
                    "QQRecord 每日清理完成：移除不活跃会话 %s 项（阈值 %s 小时）。"
                    "示例：%s%s；下次预计 %s 运行",
                    removed,
                    max_age_hours,
                    preview,
                    more,
                    next_str,
                )
            except Exception:
                logger.info(
                    "QQRecord 每日清理完成：移除不活跃会话 %s 项（阈值 %s 小时）",
                    removed,
                    max_age_hours,
                )
