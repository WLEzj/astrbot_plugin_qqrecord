import asyncio
import os
import re
from collections import deque
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.event.filter import EventMessageType, PlatformAdapterType
from astrbot.api.star import Context, Star, StarTools, register
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
        # 数据落盘位置：遵循框架工具方法，避免对目录结构的硬编码。
        self._data_dir = StarTools.get_data_dir("astrbot_plugin_qqrecord")
        self._write_lock = asyncio.Lock()

    def _get_file_stub_and_name(self, event: AstrMessageEvent) -> tuple[str, str]:
        """根据事件类型返回文件后缀与展示名称。"""
        group = event.message_obj.group
        if group:
            name = group.group_name or group.group_id or "未命名群"
            file_stub = f"group-{(group.group_id or 'unknown').strip()}"
        else:
            sender_name = (
                event.get_sender_name() or event.get_sender_id() or "未命名用户"
            )
            name = sender_name
            file_stub = f"private-{(event.get_sender_id() or 'unknown').strip()}"

        safe_stub = self._sanitize_stub(file_stub)
        return safe_stub, name

    @staticmethod
    def _sanitize_stub(stub: str) -> str:
        """仅保留字母数字下划线，避免路径遍历，空结果回退 unknown。"""
        cleaned = re.sub(r"[^0-9A-Za-z_-]+", "_", stub)
        cleaned = cleaned.strip("_-")
        return cleaned or "unknown"

    @staticmethod
    def _ensure_secure_permissions(path: Path) -> None:
        """在非 Windows 环境下将日志权限收紧为 600，避免泄露。"""
        if os.name != "nt":
            try:
                path.chmod(0o600)
            except OSError:
                # 权限收紧失败时记录但不阻断写入。
                logger.warning("QQRecord 无法设置安全权限: %s", path)

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
                is_new_file = not file_path.exists()
                with file_path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
                if is_new_file:
                    self._ensure_secure_permissions(file_path)
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
            file_stub, name = self._get_file_stub_and_name(event)
            content = event.message_str
            line = self._format_line(content, name)
            await self._write_line(line, file_stub)
        except Exception as exc:  # noqa: BLE001 - 兜底避免插件崩溃
            logger.warning("QQRecord 处理消息失败: %s", exc)

    @filter.command("record")
    async def record_command(self, event: AstrMessageEvent, limit: int = 20):
        """命令 `/record [limit]`：返回当前会话对应日志的最近 N 行。"""
        try:
            file_stub, name = self._get_file_stub_and_name(event)

            log_path = self._data_dir / f"qqrecord-{file_stub}.log"
            if not log_path.exists():
                yield event.plain_result(f"未找到日志文件，当前会话：{name}")
                return

            # 读取末尾若干行，limit 兜底范围。
            safe_limit = max(1, min(limit, 200))
            lines = deque(maxlen=safe_limit)
            with log_path.open("r", encoding="utf-8") as f:
                for line in f:
                    lines.append(line.rstrip("\n"))

            if not lines:
                yield event.plain_result(f"日志为空，当前会话：{name}")
                return

            # 组织输出，避免超长；最多显示 safe_limit 行。
            preview = "\n".join(lines)
            yield event.plain_result(f"最近 {len(lines)} 条记录（{name}）：\n{preview}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("QQRecord /record 命令失败: %s", exc)
            yield event.plain_result("读取记录时出现错误，请稍后重试。")

    async def terminate(self):
        logger.info("QQRecord 插件已卸载。")
