import os
import uuid
import time
import asyncio

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.core.message.components import File


class Exporter:
    def __init__(
        self,
        *,
        is_safe_path,
        temp_dir_provider,
        temp_file_prefix: str,
        split_text_segments,
        log_debug_exception,
    ):
        self._is_safe_path = is_safe_path
        self._temp_dir_provider = temp_dir_provider
        self._temp_file_prefix = temp_file_prefix
        self._split_text_segments = split_text_segments
        self._log_debug_exception = log_debug_exception

    async def send_cache_as_file(
        self,
        event,
        lines: list[str],
        name: str,
        file_stub: str,
        *,
        segment_len: int,
        segment_delay: float,
    ):
        """将缓存行写入临时文件并以文件消息发送，随后删除临时文件。

        文件名采用 {TEMP_FILE_PREFIX}<stub>-<timestamp>.txt，写入 UTF-8 文本。
        """
        if not lines:
            await event.send(
                MessageChain(chain=[f"缓存为空，当前会话：{name}"])
            )
            return

        safe_stub = (file_stub or "unknown").strip()
        ts = time.time()
        fname = f"{self._temp_file_prefix}{safe_stub}-{ts}-{uuid.uuid4().hex}.txt"
        temp_dir = self._temp_dir_provider()
        os.makedirs(temp_dir, exist_ok=True)
        fpath = os.path.join(temp_dir, fname)
        final_path = os.path.normpath(fpath)
        if not self._is_safe_path(temp_dir, final_path):
            logger.warning("QQRecord 临时文件路径非法: %s", final_path)
            await event.send(
                MessageChain(chain=["导出失败：临时文件路径不安全。请稍后重试。"])
            )
            return

        content = "\n".join(lines)
        try:
            with open(final_path, "w", encoding="utf-8") as fp:
                fp.write(content)

            await event.send(
                MessageChain(chain=[File(name=fname, file=final_path)])
            )
        except Exception as exc:
            logger.warning("QQRecord 文件发送失败，回退为文本：%s", exc)
            await self.send_text_segments(
                event,
                f"记录文件发送失败（可能未配置回调地址或平台不支持文件），改为文本：\n{content}",
                segment_len=segment_len,
                delay=segment_delay,
            )
        finally:
            try:
                if os.path.exists(final_path):
                    os.remove(final_path)
            except OSError as exc:
                logger.warning("临时文件清理失败 %s: %s", final_path, exc)

    async def send_text_segments(
        self,
        event,
        content: str,
        *,
        segment_len: int,
        delay: float,
    ):
        """将长文本按段发送，避免平台消息长度限制。"""
        try:
            text = content or ""
            if not text:
                return
            for seg in self._split_text_segments(text, segment_len):
                await event.send(MessageChain(chain=[seg]))
                if delay > 0:
                    await asyncio.sleep(delay)
        except Exception as exc:
            logger.warning("QQRecord 文本分段发送失败: %s", exc, exc_info=exc)
