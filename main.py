import asyncio
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.event.filter import EventMessageType, PlatformAdapterType
from astrbot.api.star import Context, Star, register
from astrbot.core.platform.astr_message_event import AstrMessageEvent


@register(
    "astrbot_plugin_qqrecord",
    "WLEzj",
    "记录 QQ 群消息到本地数据目录，格式为 内容【群昵称】",
    "1.1.0",
)
class QQRecordPlugin(Star):
    """记录 QQ 群消息到 data/plugin_data/astrbot_plugin_qqrecord。"""

    def __init__(self, context: Context):
        super().__init__(context)
        # 数据落盘位置：统一放在 data/plugin_data 下，避免插件升级被覆盖。
        # main.py 位于 AstrBot/data/plugins/<plugin>/main.py，因此 parents[2] 指向 AstrBot/data。
        self._data_dir = (
            Path(__file__).resolve().parents[2]
            / "plugin_data"
            / "astrbot_plugin_qqrecord"
        )
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._write_lock = asyncio.Lock()

    async def initialize(self):
        logger.info("QQRecord 插件已初始化，记录路径: %s", self._data_dir)

    def _format_line(self, content: str, group_name: str) -> str:
        """将消息格式化为“内容【群昵称】”行。"""
        safe_content = content.strip() if content else ""
        safe_group = group_name.strip() if group_name else "未命名群"
        return f"{safe_content}【{safe_group}】"

    async def _write_line(self, line: str, file_stub: str):
        """线程安全地写入日志，file_stub 用于区分群/私聊文件。

        文件命名：qqrecord-<file_stub>.log
        例如群聊：qqrecord-group-<group_id>.log；私聊：qqrecord-private-<user_id>.log
        """
        stub = (file_stub or "unknown").strip()
        file_path = self._data_dir / f"qqrecord-{stub}.log"
        try:
            async with self._write_lock:
                file_path.parent.mkdir(parents=True, exist_ok=True)
                with file_path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception as exc:  # noqa: BLE001 - 需要兜底保护
            logger.warning("QQRecord 写入失败: %s", exc)

    @filter.platform_adapter_type(PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(EventMessageType.ALL)
    async def record_message(self, event: AstrMessageEvent):
        """监听所有消息（群聊与私聊）并落盘为“内容【名称】”。

        - 群聊：名称为群昵称（优先群名，退化群号），文件 qqrecord-group-<group_id>.log。
        - 私聊：名称为发送者昵称（退化 user_id），文件 qqrecord-private-<user_id>.log。
        """
        try:
            group = event.message_obj.group
            content = event.message_str

            if group:
                name = group.group_name or group.group_id or "未命名群"
                file_stub = f"group-{(group.group_id or 'unknown').strip()}"
            else:
                sender_name = (
                    event.get_sender_name() or event.get_sender_id() or "未命名用户"
                )
                name = sender_name
                file_stub = f"private-{(event.get_sender_id() or 'unknown').strip()}"

            line = self._format_line(content, name)
            await self._write_line(line, file_stub)
        except Exception as exc:  # noqa: BLE001 - 兜底避免插件崩溃
            logger.warning("QQRecord 处理消息失败: %s", exc)

    async def terminate(self):
        logger.info("QQRecord 插件已卸载。")
