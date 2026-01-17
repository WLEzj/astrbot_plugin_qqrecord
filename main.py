import asyncio
import re
import os
from datetime import datetime
from collections import defaultdict, deque

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.event import filter
from astrbot.api.event.filter import EventMessageType, PlatformAdapterType
from astrbot.api.star import Context, Star, register
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.message.components import File
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path


@register(
    "astrbot_plugin_qqrecord",
    "WLEzj",
    "记录 QQ 群消息到本地数据目录，格式为 内容【群昵称】",
    "1.1.0",
)
class QQRecordPlugin(Star):
    """记录 QQ 会话消息到内存缓存（默认每会话最近 500 条）。"""

    _SANITIZE_PATTERN = re.compile(r"[^0-9A-Za-z_-]+")
    _DEFAULT_CACHE_LIMIT = 500
    _MAX_CACHE_LIMIT = 5000

    def __init__(self, context: Context):
        super().__init__(context)
        self._cache_enabled: bool = True
        self._cache_limit: int = self._DEFAULT_CACHE_LIMIT

        # 使用动态工厂方法，确保新会话遵循当前容量
        def _factory():
            return deque(maxlen=self._cache_limit)

        self._cache: defaultdict[str, deque[str]] = defaultdict(_factory)
        self._write_lock = asyncio.Lock()

    def _get_file_stub_and_name(self, event: AstrMessageEvent) -> tuple[str, str]:
        """根据事件类型返回文件后缀与展示名称。"""
        group_obj = getattr(event.message_obj, "group", None) if event.message_obj else None
        group_id_raw = event.get_group_id() or (getattr(group_obj, "group_id", None) if group_obj else None)

        if group_id_raw:
            group_id_str = str(group_id_raw).strip()
            group_name = getattr(group_obj, "group_name", None) if group_obj else None
            name = group_name or group_id_str or "未命名群"
            file_stub = f"group-{group_id_str or 'unknown'}"
        else:
            sender_name = (
                event.get_sender_name() or event.get_sender_id() or "未命名用户"
            )
            name = sender_name
            sender_id = event.get_sender_id() or "unknown"
            sender_id_str = str(sender_id).strip()
            file_stub = f"private-{sender_id_str or 'unknown'}"

        safe_stub = self._sanitize_stub(file_stub)
        return safe_stub, name

    @staticmethod
    def _sanitize_stub(stub: str) -> str:
        """仅保留字母数字下划线，避免路径遍历，空结果回退 unknown。"""
        cleaned = QQRecordPlugin._SANITIZE_PATTERN.sub("_", stub)
        cleaned = cleaned.strip("_-")
        return cleaned or "unknown"

    async def initialize(self):
        logger.info(
            "QQRecord 插件已初始化，缓存开关=%s，容量：每会话 %s 条",
            self._cache_enabled,
            self._cache_limit,
        )

    def _format_line(self, content: str, group_name: str) -> str:
        """将消息格式化为“内容【群昵称】”行。"""
        safe_content = content.strip() if content else ""
        safe_group = group_name.strip() if group_name else "未命名群"
        return f"{safe_content}【{safe_group}】"

    async def _write_line(self, line: str, file_stub: str):
        """线程安全写入内存缓存，按会话维度存储最近 N 条。"""
        stub = (file_stub or "unknown").strip()
        if not self._cache_enabled:
            return
        async with self._write_lock:
            self._cache[stub].append(line)

    async def _send_cache_as_file(
        self,
        event: AstrMessageEvent,
        lines: list[str],
        name: str,
        file_stub: str,
    ):
        """将缓存行写入临时文件并以文件消息发送，随后删除临时文件。

        文件名采用 qqrecord-<stub>-<timestamp>.txt，写入 UTF-8 文本。
        """
        if not lines:
            await event.send(
                MessageChain(chain=[f"缓存为空，当前会话：{name}"])
            )
            return

        safe_stub = (file_stub or "unknown").strip()
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        fname = f"qqrecord-{safe_stub}-{ts}.txt"
        temp_dir = get_astrbot_temp_path()
        os.makedirs(temp_dir, exist_ok=True)
        fpath = os.path.join(temp_dir, fname)

        content = "\n".join(lines)
        try:
            with open(fpath, "w", encoding="utf-8") as fp:
                fp.write(content)

            await event.send(
                MessageChain(chain=[File(name=fname, file=fpath)])
            )
        except Exception as exc:
            logger.warning("QQRecord 文件发送失败: %s", exc)
            await event.send(
                MessageChain(chain=["记录文件生成或发送失败，请稍后重试。"])
            )
        finally:
            try:
                if os.path.exists(fpath):
                    os.remove(fpath)
            except Exception as _:
                # 清理失败不影响会话，忽略
                pass

    @filter.platform_adapter_type(PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(EventMessageType.ALL)
    async def record_message(self, event: AstrMessageEvent):
        """监听所有消息（群聊与私聊）并落盘为“内容【名称】”。

        - 群聊：名称为群昵称（优先群名，退化群号），缓存键 group-<group_id>。
        - 私聊：名称为发送者昵称（退化 user_id），缓存键 private-<user_id>。
        """
        try:
            if not self._cache_enabled:
                return
            file_stub, name = self._get_file_stub_and_name(event)
            content = event.message_str
            line = self._format_line(content, name)
            await self._write_line(line, file_stub)
        except Exception as exc:  # noqa: BLE001 - 兜底避免插件崩溃
            logger.warning("QQRecord 处理消息失败: %s", exc)

    @filter.command("record")
    async def record_command(self, event: AstrMessageEvent, limit: int = 10):
        """命令 `/record [limit]`：返回当前会话内存缓存的最近 N 行。"""
        try:
            file_stub, name = self._get_file_stub_and_name(event)
            if not self._cache_enabled:
                yield event.plain_result("缓存未启用，请先 /record_cache on")
                return

            safe_limit = max(1, min(limit, self._cache_limit))
            async with self._write_lock:
                lines = list(self._cache.get(file_stub, []))[-safe_limit:]

            if not lines:
                yield event.plain_result(f"缓存为空，当前会话：{name}")
                return

            preview = "\n".join(lines)
            yield event.plain_result(f"最近 {len(lines)} 条缓存记录（{name}）：\n{preview}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("QQRecord /record 命令失败: %s", exc)
            yield event.plain_result("读取记录时出现错误，请稍后重试。")

    @filter.command("record_file")
    async def record_file_command(self, event: AstrMessageEvent, limit: int = 10):
        """命令 `/record_file [limit]`：将最近 N 行记录打包为文本文件并发送。"""
        try:
            file_stub, name = self._get_file_stub_and_name(event)
            if not self._cache_enabled:
                await event.send(MessageChain(chain=["缓存未启用，请先 /record_cache on"]))
                return

            safe_limit = max(1, min(limit, self._cache_limit))
            async with self._write_lock:
                lines = list(self._cache.get(file_stub, []))[-safe_limit:]

            await self._send_cache_as_file(event, lines, name, file_stub)
        except Exception as exc:  # noqa: BLE001
            logger.warning("QQRecord /record_file 命令失败: %s", exc)
            await event.send(
                MessageChain(chain=["导出记录为文件时出现错误，请稍后重试。"])
            )

    def _reconfigure_cache_limit(self, new_limit: int):
        """调整所有会话缓存容量，保留最近 new_limit 条内容。"""
        new_limit = max(1, min(new_limit, self._MAX_CACHE_LIMIT))
        self._cache_limit = new_limit
        # 更新默认工厂
        self._cache.default_factory = lambda: deque(maxlen=self._cache_limit)

        # 重建已有 deque，保留尾部
        for key, dq in list(self._cache.items()):
            recent = list(dq)[-new_limit:]
            self._cache[key] = deque(recent, maxlen=new_limit)

    @filter.command("record_cache")
    async def record_cache_command(self, event: AstrMessageEvent, flag: str | None = None):
        """命令 `/record_cache [on|off]`：开启/关闭缓存写入；不带参数则查询状态。"""
        try:
            if flag is None:
                state = "on" if self._cache_enabled else "off"
                yield event.plain_result(f"缓存状态：{state}，容量：每会话 {self._cache_limit} 条")
                return

            val = str(flag).strip().lower()
            if val in ("on", "true", "1"):
                self._cache_enabled = True
                yield event.plain_result("已开启缓存写入。")
            elif val in ("off", "false", "0"):
                self._cache_enabled = False
                yield event.plain_result("已关闭缓存写入。")
            else:
                yield event.plain_result("参数无效，请使用 /record_cache on 或 /record_cache off")
        except Exception as exc:  # noqa: BLE001
            logger.warning("QQRecord /record_cache 命令失败: %s", exc)
            yield event.plain_result("切换缓存状态时出现错误，请稍后重试。")

    @filter.command("record_limit")
    async def record_limit_command(self, event: AstrMessageEvent, n: int | None = None):
        """命令 `/record_limit [n]`：设置/查询每会话缓存上限，范围 1~5000。"""
        try:
            if n is None:
                yield event.plain_result(
                    f"当前容量上限：每会话 {self._cache_limit} 条（可设置范围 1~{self._MAX_CACHE_LIMIT}）"
                )
                return

            async with self._write_lock:
                self._reconfigure_cache_limit(int(n))

            yield event.plain_result(
                f"已将容量上限设为：每会话 {self._cache_limit} 条，现有会话已按新上限裁剪"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("QQRecord /record_limit 命令失败: %s", exc)
            yield event.plain_result("调整容量上限时出现错误，请稍后重试。")

    async def terminate(self):
        logger.info("QQRecord 插件已卸载。")
