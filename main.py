import asyncio
import re
import os
import uuid
from datetime import datetime, timedelta
from collections import defaultdict, deque, OrderedDict
from collections.abc import Callable

from astrbot.api import logger, sp
from astrbot.api.event import MessageChain
from astrbot.api.event import filter
from astrbot.api.event.filter import EventMessageType, PlatformAdapterType
from astrbot.api.star import Context, Star
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.message.components import File, Reply, BaseMessageComponent
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path


class QQRecordPlugin(Star):
    """记录 QQ 会话消息到内存缓存（默认每会话最近 500 条）。"""

    _SANITIZE_PATTERN = re.compile(r"[^0-9A-Za-z_-]+")
    _default_cache_limit = 500
    _max_cache_limit = 1000
    _default_cleanup_hours = 24
    _default_segment_len = 1000
    _default_segment_delay = 0.5

    def __init__(self, context: Context):
        super().__init__(context)
        # 用于插件KV存储的标识
        self.plugin_id = "astrbot_plugin_qqrecord"
        self._cache_enabled: bool = True
        self._cache_limit: int = self._default_cache_limit
        self._last_seen: dict[str, datetime] = {}
        self._cleanup_task: asyncio.Task | None = None
        self._admin_only: bool = False

        # 使用工厂方法确保新会话遵循当前容量（避免 lambda 捕获 self 造成循环引用）
        self._cache: defaultdict[str, deque[str]] = defaultdict(self._deque_factory(self._cache_limit))
        # 线程化缓存：按会话维护锚点到线程的数据结构（保持插入有序）
        self._threads: dict[str, OrderedDict[str, dict]] = {}
        self._write_lock = asyncio.Lock()
        # 访问统计：按会话记录读取命中/未命中次数
        self._stats: defaultdict[str, dict[str, int]] = defaultdict(lambda: {"hit": 0, "miss": 0})

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        """尝试判断是否管理员；若平台不支持该判断，则默认允许。"""
        try:
            # 优先使用事件对象上的权限判断方法（如存在）
            if hasattr(event, "is_admin"):
                return bool(event.is_admin())
            if hasattr(event, "is_group_admin"):
                return bool(event.is_group_admin())
            if hasattr(event, "is_super_user"):
                return bool(event.is_super_user())
        except Exception as exc:
            self._log_debug_exception("QQRecord 管理员判断异常", exc)
        return True

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
        # 从持久化KV恢复配置
        try:
            enabled = await self._get_kv_value("cache_enabled", True)
            if isinstance(enabled, bool):
                self._cache_enabled = enabled
            limit = await self._get_kv_value("cache_limit", self._default_cache_limit)
            try:
                limit_int = int(limit) if limit is not None else self._default_cache_limit
            except Exception:
                limit_int = self._default_cache_limit
            admin_only = await self._get_kv_value("admin_only", False)
            try:
                self._admin_only = bool(admin_only)
            except Exception:
                self._admin_only = False
            # 夹紧范围并应用到现有会话
            async with self._write_lock:
                self._reconfigure_cache_limit(max(1, min(limit_int, self._max_cache_limit)))
        except Exception as exc:
            logger.warning("QQRecord 恢复配置失败，使用默认值: %s", exc)

        logger.info(
            "QQRecord 插件已初始化，缓存开关=%s，容量：每会话 %s 条",
            self._cache_enabled,
            self._cache_limit,
        )
        # 启动每日 6 点的清理任务
        try:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        except Exception as exc:
            logger.warning("QQRecord 清理任务启动失败: %s", exc)

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
            self._last_seen[stub] = datetime.now()

    def _get_reply_anchor_id(self, event: AstrMessageEvent) -> tuple[str | int | None, bool]:
        """从消息链中提取引用锚点 ID。

        返回 (anchor_id, is_reply)。如果不存在 Reply 段，则使用当前消息 ID 作为锚点并标记为非回复。
        """
        try:
            comps: list[BaseMessageComponent] = event.get_messages()
            for comp in comps:
                if isinstance(comp, Reply):
                    return comp.id, True
        except Exception as exc:
            self._log_debug_exception("QQRecord 读取 Reply 锚点异常", exc)
        # 无 Reply 段时，使用自身消息 ID
        mid = getattr(event.message_obj, "message_id", None)
        return mid, False

    @staticmethod
    def _deque_factory(limit: int) -> Callable[[], deque[str]]:
        return lambda: deque(maxlen=limit)

    @staticmethod
    def _log_debug_exception(message: str, exc: Exception):
        try:
            logger.debug("%s: %s", message, exc)
        except Exception:
            pass

    def _bump_stat(self, stub: str, hit: bool):
        stat = self._stats[stub]
        if hit:
            stat["hit"] = stat.get("hit", 0) + 1
        else:
            stat["miss"] = stat.get("miss", 0) + 1

    async def _get_kv_value(self, key: str, default):
        getter = getattr(self, "get_kv_data", None)
        if callable(getter):
            return await getter(key, default)
        try:
            return await sp.get_async("plugin", self.plugin_id, key, default)
        except Exception as exc:
            self._log_debug_exception("QQRecord KV 读取异常", exc)
            return default

    async def _put_kv_value(self, key: str, value):
        setter = getattr(self, "put_kv_data", None)
        if callable(setter):
            await setter(key, value)
            return
        try:
            await sp.put_async("plugin", self.plugin_id, key, value)
        except Exception as exc:
            self._log_debug_exception("QQRecord KV 写入异常", exc)

    def _get_session_lines(self, file_stub: str) -> list[str]:
        lines: list[str] = []
        threads = self._threads.get(file_stub, OrderedDict())
        for entry in threads.values():
            if entry.get("main"):
                lines.append(entry["main"])
            lines.extend(list(entry.get("replies", [])))
        return lines

    @staticmethod
    def _get_thread_lines(entry: dict) -> list[str]:
        lines: list[str] = []
        if entry.get("main"):
            lines.append(entry["main"])
        lines.extend(list(entry.get("replies", [])))
        return lines

    def _ensure_threads_session(self, stub: str):
        if stub not in self._threads:
            self._threads[stub] = OrderedDict()

    @staticmethod
    def _generate_anchor_key(anchor_id: str | int | None) -> str:
        if anchor_id is not None:
            return str(anchor_id)
        return f"auto-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _is_safe_path(base_dir: str, target_path: str) -> bool:
        try:
            base = os.path.normpath(base_dir)
            target = os.path.normpath(target_path)
            return os.path.commonpath([base]) == os.path.commonpath([base, target])
        except Exception:
            return False

    def _get_or_create_thread_entry(self, stub: str, key: str) -> dict:
        self._ensure_threads_session(stub)
        threads = self._threads[stub]
        if key not in threads:
            threads[key] = {"main": None, "replies": deque(), "first_ts": datetime.now(), "last_ts": None}
        return threads[key]

    def _append_thread_reply(self, entry: dict, stub: str, key: str, line: str):
        entry["replies"].append(line)
        entry["last_ts"] = datetime.now()
        try:
            logger.info("QQRecord 追加回复：会话=%s，锚点=%s，累计=%d", stub, key, len(entry["replies"]))
        except Exception as exc:
            self._log_debug_exception("QQRecord 追加回复日志失败", exc)

    def _set_thread_main_or_reply(self, entry: dict, stub: str, key: str, line: str):
        if entry.get("main") is None:
            entry["main"] = line
            entry["last_ts"] = datetime.now()
            try:
                logger.info("QQRecord 新建线程主消息：会话=%s，锚点=%s", stub, key)
            except Exception as exc:
                self._log_debug_exception("QQRecord 主消息日志失败", exc)
            return
        self._append_thread_reply(entry, stub, key, line)

    def _enforce_session_capacity(self, stub: str):
        """确保每会话的总记录条数不超过 _cache_limit（按线程单位裁剪）。"""
        threads = self._threads.get(stub)
        if not threads:
            return
        def _count():
            total = 0
            for entry in threads.values():
                total += 1 if entry.get("main") else 0
                total += len(entry.get("replies", []))
            return total
        total = _count()
        while total > self._cache_limit and threads:
            # 按线程 FIFO 直接移除最早锚点，避免只删回复导致主消息残留
            first_key = next(iter(threads.keys()))
            threads.pop(first_key, None)
            total = _count()

    def _write_threaded(self, stub: str, anchor_id: str | int | None, is_reply: bool, line: str):
        """写入线程化缓存。

        - 无 Reply：作为新的锚点 main 记录。
        - 有 Reply：追加到对应锚点的 replies。
        """
        key = self._generate_anchor_key(anchor_id)
        entry = self._get_or_create_thread_entry(stub, key)
        if is_reply:
            self._append_thread_reply(entry, stub, key, line)
        else:
            self._set_thread_main_or_reply(entry, stub, key, line)
        # 更新活跃时间与裁剪
        self._last_seen[stub] = datetime.now()
        self._enforce_session_capacity(stub)

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
        now = datetime.now()
        ts = now.strftime("%Y%m%d-%H%M%S-%f")
        fname = f"qqrecord-{safe_stub}-{ts}-{uuid.uuid4().hex}.txt"
        temp_dir = get_astrbot_temp_path()
        os.makedirs(temp_dir, exist_ok=True)
        fpath = os.path.join(temp_dir, fname)
        final_path = os.path.normpath(fpath)
        if not self._is_safe_path(temp_dir, final_path):
            raise ValueError("Invalid file path")

        content = "\n".join(lines)
        try:
            with open(final_path, "w", encoding="utf-8") as fp:
                fp.write(content)

            await event.send(
                MessageChain(chain=[File(name=fname, file=final_path)])
            )
        except Exception as exc:
            logger.warning("QQRecord 文件发送失败，回退为文本：%s", exc)
            await self._send_text_segments(
                event,
                f"记录文件发送失败（可能未配置回调地址或平台不支持文件），改为文本：\n{content}"
            )
        finally:
            try:
                if os.path.exists(final_path):
                    os.remove(final_path)
            except Exception as exc:
                logger.warning("临时文件清理失败 %s: %s", final_path, exc)

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
            # 线程化写入（按 Reply 锚点归类）
            anchor_id, is_reply = self._get_reply_anchor_id(event)
            async with self._write_lock:
                self._write_threaded(file_stub, anchor_id, is_reply, line)
                # 兼容旧缓存结构（用于现有测试/简单读取）
                self._cache[file_stub].append(line)
        except Exception as exc:  # noqa: BLE001 - 兜底避免插件崩溃
            logger.warning("QQRecord 处理消息失败: %s", exc)

    def _seconds_until_next_6am(self) -> float:
        now = datetime.now()
        next_run = now.replace(hour=6, minute=0, second=0, microsecond=0)
        if now >= next_run:
            next_run += timedelta(days=1)
        return max(1.0, (next_run - now).total_seconds())

    async def _cleanup_loop(self):
        while True:
            try:
                delay = self._seconds_until_next_6am()
                await asyncio.sleep(delay)
                await self._cleanup_inactive(max_age_hours=self._default_cleanup_hours)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("QQRecord 清理循环异常: %s", exc)

    async def _cleanup_inactive(self, max_age_hours: int = _default_cleanup_hours):
        """清理长时间未更新的会话缓存键，默认 24 小时未活动即清理。"""
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        removed = 0
        removed_keys: list[str] = []
        async with self._write_lock:
            for key in list(self._cache.keys()):
                last = self._last_seen.get(key)
                if last is None or last < cutoff:
                    try:
                        self._cache.pop(key, None)
                        self._last_seen.pop(key, None)
                        self._threads.pop(key, None)
                        self._stats.pop(key, None)
                        removed += 1
                        removed_keys.append(key)
                    except Exception:
                        pass
        if removed:
            try:
                preview = ", ".join(removed_keys[:10])
                more = "..." if len(removed_keys) > 10 else ""
                now = datetime.now()
                next_run = now.replace(hour=6, minute=0, second=0, microsecond=0)
                if now >= next_run:
                    next_run += timedelta(days=1)
                next_str = next_run.strftime("%Y-%m-%d %H:%M:%S")
                logger.info(
                    "QQRecord 每日清理完成：移除不活跃会话 %s 项（阈值 %s 小时）。示例：%s%s；下次预计 %s 运行",
                    removed, max_age_hours, preview, more, next_str
                )
            except Exception:
                logger.info("QQRecord 每日清理完成：移除不活跃会话 %s 项（阈值 %s 小时）", removed, max_age_hours)

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
                lines = self._get_session_lines(file_stub)
                lines = lines[-safe_limit:]

            if not lines:
                yield event.plain_result(f"缓存为空，当前会话：{name}")
                return

            self._bump_stat(file_stub, True)
            preview = "\n".join(lines)
            yield event.plain_result(f"最近 {len(lines)} 条缓存记录（{name}）：\n{preview}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("QQRecord /record 命令失败: %s", exc)
            yield event.plain_result("读取记录时出现错误，请稍后重试。")

    @filter.command("record_thread")
    async def record_thread_command(self, event: AstrMessageEvent, anchor_id: str, limit: int | None = None):
        """命令 `/record_thread <anchor_id> [limit]`：按锚点查看该线程内容。"""
        try:
            file_stub, name = self._get_file_stub_and_name(event)
            key = str(anchor_id).strip()
            async with self._write_lock:
                threads = self._threads.get(file_stub, OrderedDict())
                entry = threads.get(key)
                if not entry:
                    yield event.plain_result(f"未找到锚点 {key} 的线程（{name}）。")
                    self._bump_stat(file_stub, False)
                    return
                lines = self._get_thread_lines(entry)
                if isinstance(limit, int) and limit > 0:
                    lines = lines[-limit:]

            if not lines:
                yield event.plain_result(f"该线程暂无记录（{name}）。")
                return

            self._bump_stat(file_stub, True)
            preview = "\n".join(lines)
            yield event.plain_result(f"线程 {key} 最近 {len(lines)} 条（{name}）：\n{preview}")
        except Exception as exc:
            logger.warning("QQRecord /record_thread 命令失败: %s", exc)
            yield event.plain_result("读取线程记录时出现错误，请稍后重试。")

    @filter.command("record_threads")
    async def record_threads_command(self, event: AstrMessageEvent, limit: int = 10):
        """命令 `/record_threads [limit]`：列出最近的线程摘要。"""
        try:
            file_stub, name = self._get_file_stub_and_name(event)
            async with self._write_lock:
                threads = self._threads.get(file_stub, OrderedDict())
                # 取最后 limit 个锚点摘要（保持插入顺序）
                items = list(threads.items())[-max(1, limit):]
            if not items:
                yield event.plain_result(f"当前会话暂无线程（{name}）。")
                return

            def _short(s: str, n: int = 40) -> str:
                s = (s or "").strip()
                return s if len(s) <= n else s[:n] + "…"

            lines = []
            for key, entry in items:
                main = entry.get("main") or "(无主消息)"
                cnt = 1 if entry.get("main") else 0
                cnt += len(entry.get("replies", []))
                ts = entry.get("last_ts") or entry.get("first_ts")
                ts_str = ts.strftime("%Y-%m-%d %H:%M:%S") if ts else "-"
                lines.append(f"anchor={key} | count={cnt} | last={ts_str} | main={_short(main)}")

            self._bump_stat(file_stub, True)
            preview = "\n".join(lines)
            tip = "\n提示：使用 /record_thread <anchor_id> 查看该线程详情。"
            yield event.plain_result(f"最近 {len(items)} 个线程摘要（{name}）：\n{preview}{tip}")
        except Exception as exc:
            logger.warning("QQRecord /record_threads 命令失败: %s", exc)
            yield event.plain_result("列出线程摘要时出现错误，请稍后重试。")

    @filter.command("record_file")
    async def record_file_command(self, event: AstrMessageEvent, limit: int = 10, fmt: str | None = None):
        """命令 `/record_file [limit] [fmt]`：将最近 N 行记录导出。

        - `fmt` 可选：`txt`（默认）或 `md`。当文件发送失败时，自动退化为分段文本消息。
        """
        try:
            file_stub, name = self._get_file_stub_and_name(event)
            if not self._cache_enabled:
                await event.send(MessageChain(chain=["缓存未启用，请先 /record_cache on"]))
                return
            safe_limit = max(1, min(limit, self._cache_limit))
            async with self._write_lock:
                lines = self._get_session_lines(file_stub)
                lines = lines[-safe_limit:]

            # 根据格式简单适配内容（md 与 txt 目前仅在标题/分隔上有轻度差异）
            if fmt and str(fmt).lower() == "md":
                # 为 markdown 添加标题与分隔
                lines = [f"# 记录（{name}）", "", *lines]

            await self._send_cache_as_file(event, lines, name, file_stub)
            self._bump_stat(file_stub, bool(lines))
        except Exception as exc:  # noqa: BLE001
            logger.warning("QQRecord /record_file 命令失败: %s", exc)
            # 发送失败时退化为分段文本消息
            try:
                file_stub, name = self._get_file_stub_and_name(event)
                async with self._write_lock:
                    lines = self._get_session_lines(file_stub)
                    lines = lines[-max(1, min(limit, self._cache_limit)):]
                content = "\n".join(lines)
                await self._send_text_segments(event, f"记录文件发送失败，改为文本：\n{content}")
                self._bump_stat(file_stub, bool(lines))
            except Exception:
                await event.send(MessageChain(chain=["导出记录为文件时出现错误，请稍后重试。"]))

    def _reconfigure_cache_limit(self, new_limit: int):
        """调整所有会话缓存容量，保留最近 new_limit 条内容。"""
        new_limit = max(1, min(new_limit, self._max_cache_limit))
        self._cache_limit = new_limit
        # 更新默认工厂
        self._cache.default_factory = self._deque_factory(self._cache_limit)

        # 重建已有 deque，保留尾部
        for key, dq in list(self._cache.items()):
            recent = list(dq)[-new_limit:]
            self._cache[key] = deque(recent, maxlen=new_limit)

    @filter.command("record_cache")
    async def record_cache_command(self, event: AstrMessageEvent, flag: str | None = None):
        """命令 `/record_cache [on|off|status]`：开启/关闭/查询状态。仅管理员可调。"""
        try:
            if self._admin_only and not self._is_admin(event):
                yield event.plain_result("仅管理员可用该命令。")
                return
            if flag is None:
                state = "on" if self._cache_enabled else "off"
                yield event.plain_result(f"缓存状态：{state}，容量：每会话 {self._cache_limit} 条")
                return

            val = str(flag).strip().lower()
            if val == "status":
                # 输出更详尽状态
                file_stub, name = self._get_file_stub_and_name(event)
                async with self._write_lock:
                    session_lines = len(self._cache.get(file_stub, []))
                    threads_cnt = len(self._threads.get(file_stub, OrderedDict()))
                    total_sessions = len(self._cache.keys())
                    stat = self._stats.get(file_stub, {"hit": 0, "miss": 0})
                hit = stat.get("hit", 0)
                miss = stat.get("miss", 0)
                total_req = hit + miss
                hit_rate = (hit / total_req * 100) if total_req else 0.0
                yield event.plain_result(
                    f"状态：{'开启' if self._cache_enabled else '关闭'}\n"
                    f"容量上限：{self._cache_limit}\n"
                    f"当前会话（{name}）：行数={session_lines}，线程数={threads_cnt}\n"
                    f"命中：{hit}，未命中：{miss}，命中率：{hit_rate:.1f}%\n"
                    f"总会话键数：{total_sessions}"
                )
                return
            if val in ("on", "true", "1"):
                self._cache_enabled = True
                # 持久化
                await self._put_kv_value("cache_enabled", True)
                yield event.plain_result("已开启缓存写入。")
            elif val in ("off", "false", "0"):
                self._cache_enabled = False
                await self._put_kv_value("cache_enabled", False)
                yield event.plain_result("已关闭缓存写入。")
            else:
                yield event.plain_result("参数无效，请使用 /record_cache on 或 /record_cache off")
        except Exception as exc:  # noqa: BLE001
            logger.warning("QQRecord /record_cache 命令失败: %s", exc)
            yield event.plain_result("切换缓存状态时出现错误，请稍后重试。")

    @filter.command("record_limit")
    async def record_limit_command(self, event: AstrMessageEvent, n: int | None = None):
        """命令 `/record_limit [n]`：设置/查询每会话缓存上限，范围 1~5000。仅管理员可调。"""
        try:
            if self._admin_only and not self._is_admin(event):
                yield event.plain_result("仅管理员可用该命令。")
                return
            if n is None:
                # 无参时输出更详尽状态
                file_stub, name = self._get_file_stub_and_name(event)
                async with self._write_lock:
                    session_lines = len(self._cache.get(file_stub, []))
                    threads_cnt = len(self._threads.get(file_stub, OrderedDict()))
                    stat = self._stats.get(file_stub, {"hit": 0, "miss": 0})
                    total_sessions = len(self._cache.keys())
                hit = stat.get("hit", 0)
                miss = stat.get("miss", 0)
                total_req = hit + miss
                hit_rate = (hit / total_req * 100) if total_req else 0.0
                yield event.plain_result(
                    f"当前容量上限：每会话 {self._cache_limit} 条（可设置范围 1~{self._max_cache_limit}）\n"
                    f"当前会话（{name}）：行数={session_lines}，线程数={threads_cnt}\n"
                    f"命中：{hit}，未命中：{miss}，命中率：{hit_rate:.1f}%\n"
                    f"总会话键数：{total_sessions}"
                )
                return

            async with self._write_lock:
                self._reconfigure_cache_limit(int(n))

            # 持久化
            await self._put_kv_value("cache_limit", int(self._cache_limit))

            yield event.plain_result(
                f"已将容量上限设为：每会话 {self._cache_limit} 条，现有会话已按新上限裁剪"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("QQRecord /record_limit 命令失败: %s", exc)
            yield event.plain_result("调整容量上限时出现错误，请稍后重试。")

    async def terminate(self):
        # 停止清理任务
        try:
            if self._cleanup_task:
                self._cleanup_task.cancel()
                try:
                    await self._cleanup_task
                except asyncio.CancelledError:
                    pass
        except Exception as exc:
            self._log_debug_exception("QQRecord 终止清理任务异常", exc)
        logger.info("QQRecord 插件已卸载。")

    async def _send_text_segments(
        self,
        event: AstrMessageEvent,
        content: str,
        segment_len: int = _default_segment_len,
        delay: float = _default_segment_delay,
    ):
        """将长文本按段发送，避免平台消息长度限制。"""
        try:
            text = content or ""
            if not text:
                return
            segments = self._split_text_segments(text, segment_len)
            for seg in segments:
                await event.send(MessageChain(chain=[seg]))
                if delay > 0:
                    await asyncio.sleep(delay)
        except Exception as exc:
            logger.warning("QQRecord 文本分段发送失败: %s", exc)
            self._log_debug_exception("QQRecord 文本分段异常", exc)

    @staticmethod
    def _split_text_segments(text: str, limit: int) -> list[str]:
        """按更友好的边界分段，尽量避免切断 URL。"""
        if limit <= 0:
            return [text]
        url_pattern = re.compile(r"https?://\S+")
        segments: list[str] = []
        current = ""

        def flush():
            nonlocal current
            if current:
                segments.append(current)
                current = ""

        for line in text.splitlines(keepends=True):
            if len(line) > limit:
                flush()
                tokens = re.split(r"(\s+)", line)
                buffer = ""
                for tok in tokens:
                    if not tok:
                        continue
                    is_url = bool(url_pattern.fullmatch(tok.strip()))
                    if len(tok) > limit:
                        if buffer:
                            segments.append(buffer)
                            buffer = ""
                        if is_url:
                            segments.append(tok)
                        else:
                            segments.extend([tok[i:i+limit] for i in range(0, len(tok), limit)])
                        continue
                    if len(buffer) + len(tok) > limit:
                        if buffer:
                            segments.append(buffer)
                        buffer = tok
                    else:
                        buffer += tok
                if buffer:
                    segments.append(buffer)
                continue

            if len(current) + len(line) > limit:
                flush()
            current += line

        flush()
        return segments
