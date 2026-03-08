"""
改进的管理命令处理器
解决封装性和DRY原则问题
"""
from astrbot.api import logger
from astrbot.core.platform.astr_message_event import AstrMessageEvent

from .plugin_api import PluginPublicAPI, require_admin


class AdminCommands:
    """管理命令处理器 - 改进版本"""

    def __init__(self, plugin):
        self._plugin = plugin
        self._api = PluginPublicAPI(plugin)

    @require_admin("仅管理员可用该命令。")
    async def record_cache_command(
        self,
        event: AstrMessageEvent,
        flag: str | None = None,
    ):
        """命令 `/record_cache [on|off|status]`：开启/关闭/查询状态。仅管理员可调。"""
        file_stub = None
        try:
            if flag is None:
                state = "on" if self._api.cache_enabled else "off"
                yield event.plain_result(
                    f"缓存状态：{state}，容量：每会话 {self._api.cache_limit} 条"
                )
                return

            val = str(flag).strip().lower()
            if val == "status":
                file_stub, name = self._api.get_file_stub_and_name(event)
                session_lines, threads_cnt, total_sessions, hit, miss, hit_rate = (
                    await self._api.collect_session_status(file_stub)
                )
                whitelist_info = f"群白名单：{', '.join(self._api.group_whitelist) if self._api.group_whitelist else '无（记录所有群）'}"
                private_info = f"私聊记录：{'开启' if self._api.record_private_chats else '关闭'}"
                sanitize_info = f"数据脱敏：{'开启' if self._api.enable_sanitization else '关闭'}"
                export_info = f"导出权限：{'仅管理员' if self._api.export_admin_only else '所有人'}"
                yield event.plain_result(
                    f"状态：{'开启' if self._api.cache_enabled else '关闭'}\n"
                    f"容量上限：{self._api.cache_limit}\n"
                    f"当前会话（{name}）：行数={session_lines}，线程数={threads_cnt}\n"
                    f"命中：{hit}，未命中：{miss}，命中率：{hit_rate:.1f}%\n"
                    f"总会话键数：{total_sessions}\n"
                    f"{whitelist_info}\n"
                    f"{private_info}\n"
                    f"{sanitize_info}\n"
                    f"{export_info}"
                )
                return
            if val in ("on", "true", "1"):
                self._api.cache_enabled = True
                await self._api.save_config("cache_enabled", True)
                yield event.plain_result("已开启缓存写入。")
            elif val in ("off", "false", "0"):
                self._api.cache_enabled = False
                await self._api.save_config("cache_enabled", False)
                yield event.plain_result("已关闭缓存写入。")
            else:
                yield event.plain_result(
                    "参数无效，请使用 /record_cache on 或 /record_cache off"
                )
        except (ValueError, TypeError) as exc:
            logger.warning(
                "QQRecord /record_cache 参数错误: %s (flag=%s, stub=%s)",
                exc,
                flag,
                file_stub,
                exc_info=exc,
            )
            yield event.plain_result("参数无效，请检查后重试。")
        except (RuntimeError, OSError) as exc:
            logger.warning(
                "QQRecord /record_cache 命令失败: %s (flag=%s, stub=%s)",
                exc,
                flag,
                file_stub,
                exc_info=exc,
            )
            yield event.plain_result("切换缓存状态失败，请稍后重试。")
        except Exception as exc:
            logger.exception(
                "QQRecord /record_cache 命令异常: %s (flag=%s, stub=%s)",
                exc,
                flag,
                file_stub,
            )
            yield event.plain_result("命令执行失败，请稍后重试。")

    @require_admin("仅管理员可用该命令。")
    async def record_export_command(
        self,
        event: AstrMessageEvent,
        flag: str | None = None,
    ):
        """命令 `/record_export [on|off|status]`：导出权限开关（管理员限定）。"""
        try:
            if flag is None or str(flag).strip().lower() == "status":
                state = "on" if self._api.export_admin_only else "off"
                yield event.plain_result(f"导出权限状态：{state}")
                return
            val = str(flag).strip().lower()
            if val in ("on", "true", "1"):
                self._api.export_admin_only = True
                await self._api.save_config("export_admin_only", True)
                yield event.plain_result("已开启导出管理员限制。")
            elif val in ("off", "false", "0"):
                self._api.export_admin_only = False
                await self._api.save_config("export_admin_only", False)
                yield event.plain_result("已关闭导出管理员限制。")
            else:
                yield event.plain_result(
                    "参数无效，请使用 /record_export on 或 /record_export off"
                )
        except (ValueError, TypeError) as exc:
            logger.warning("QQRecord /record_export 参数错误: %s", exc, exc_info=exc)
            yield event.plain_result("参数无效，请检查后重试。")
        except (RuntimeError, OSError) as exc:
            logger.warning("QQRecord /record_export 命令失败: %s", exc, exc_info=exc)
            yield event.plain_result("切换导出权限失败，请稍后重试。")
        except Exception as exc:
            logger.exception("QQRecord /record_export 命令异常: %s", exc)
            yield event.plain_result("命令执行失败，请稍后重试。")

    @require_admin("仅管理员可用该命令。")
    async def record_admin_command(
        self,
        event: AstrMessageEvent,
        flag: str | None = None,
    ):
        """命令 `/record_admin [on|off|status]`：全局管理员权限开关（管理员限定）。"""
        try:
            if flag is None or str(flag).strip().lower() == "status":
                state = "on" if self._api.admin_only else "off"
                yield event.plain_result(f"全局管理员权限状态：{state}")
                return
            val = str(flag).strip().lower()
            if val in ("on", "true", "1"):
                self._api.admin_only = True
                await self._api.save_config("admin_only", True)
                yield event.plain_result("已开启全局管理员权限限制。")
            elif val in ("off", "false", "0"):
                self._api.admin_only = False
                await self._api.save_config("admin_only", False)
                yield event.plain_result("已关闭全局管理员权限限制。")
            else:
                yield event.plain_result(
                    "参数无效，请使用 /record_admin on 或 /record_admin off"
                )
        except (ValueError, TypeError) as exc:
            logger.warning("QQRecord /record_admin 参数错误: %s", exc, exc_info=exc)
            yield event.plain_result("参数无效，请检查后重试。")
        except (RuntimeError, OSError) as exc:
            logger.warning("QQRecord /record_admin 命令失败: %s", exc, exc_info=exc)
            yield event.plain_result("切换全局管理员权限失败，请稍后重试。")
        except Exception as exc:
            logger.exception("QQRecord /record_admin 命令异常: %s", exc)
            yield event.plain_result("命令执行失败，请稍后重试。")

    @require_admin("仅管理员可用该命令。")
    async def record_limit_command(
        self,
        event: AstrMessageEvent,
        n: int | None = None,
    ):
        """命令 `/record_limit [n]`：设置/查询每会话缓存上限，范围 1~1000。仅管理员可调。"""
        file_stub = None
        try:
            if n is None:
                file_stub, name = self._api.get_file_stub_and_name(event)
                session_lines, threads_cnt, total_sessions, hit, miss, hit_rate = (
                    await self._api.collect_session_status(file_stub)
                )
                whitelist_info = f"群白名单：{', '.join(self._api.group_whitelist) if self._api.group_whitelist else '无（记录所有群）'}"
                private_info = f"私聊记录：{'开启' if self._api.record_private_chats else '关闭'}"
                sanitize_info = f"数据脱敏：{'开启' if self._api.enable_sanitization else '关闭'}"
                export_info = f"导出权限：{'仅管理员' if self._api.export_admin_only else '所有人'}"
                yield event.plain_result(
                    f"当前容量上限：每会话 {self._api.cache_limit} 条（可设置范围 1~{self._api.max_cache_limit}）\n"
                    f"当前会话（{name}）：行数={session_lines}，线程数={threads_cnt}\n"
                    f"命中：{hit}，未命中：{miss}，命中率：{hit_rate:.1f}%\n"
                    f"总会话键数：{total_sessions}\n"
                    f"{whitelist_info}\n"
                    f"{private_info}\n"
                    f"{sanitize_info}\n"
                    f"{export_info}"
                )
                return

            await self._api.reconfigure_cache_limit(int(n))
            await self._api.save_config("cache_limit", int(self._api.cache_limit))

            yield event.plain_result(
                "已将容量上限设为："
                f"每会话 {self._api.cache_limit} 条，现有会话已按新上限裁剪"
            )
        except (ValueError, TypeError) as exc:
            logger.warning(
                "QQRecord /record_limit 参数错误: %s (n=%s, stub=%s)",
                exc,
                n,
                file_stub,
                exc_info=exc,
            )
            yield event.plain_result("参数无效，请检查后重试。")
        except (RuntimeError, OSError) as exc:
            logger.warning(
                "QQRecord /record_limit 命令失败: %s (n=%s, stub=%s)",
                exc,
                n,
                file_stub,
                exc_info=exc,
            )
            yield event.plain_result("调整容量上限失败，请稍后重试。")
        except Exception as exc:
            logger.exception(
                "QQRecord /record_limit 命令异常: %s (n=%s, stub=%s)",
                exc,
                n,
                file_stub,
            )
            yield event.plain_result("命令执行失败，请稍后重试。")

    @require_admin("仅管理员可用该命令。")
    async def record_private_command(
        self,
        event: AstrMessageEvent,
        flag: str | None = None,
    ):
        """命令 `/record_private [on|off|status]`：私聊记录开关（管理员限定）。"""
        try:
            if flag is None or str(flag).strip().lower() == "status":
                state = "on" if self._api.record_private_chats else "off"
                yield event.plain_result(f"私聊记录状态：{state}")
                return
            val = str(flag).strip().lower()
            if val in ("on", "true", "1"):
                self._api.record_private_chats = True
                await self._api.save_config("record_private_chats", True)
                yield event.plain_result("已开启私聊记录。")
            elif val in ("off", "false", "0"):
                self._api.record_private_chats = False
                await self._api.save_config("record_private_chats", False)
                yield event.plain_result("已关闭私聊记录。")
            else:
                yield event.plain_result(
                    "参数无效，请使用 /record_private on 或 /record_private off"
                )
        except (ValueError, TypeError) as exc:
            logger.warning("QQRecord /record_private 参数错误: %s", exc, exc_info=exc)
            yield event.plain_result("参数无效，请检查后重试。")
        except (RuntimeError, OSError) as exc:
            logger.warning("QQRecord /record_private 命令失败: %s", exc, exc_info=exc)
            yield event.plain_result("切换私聊记录失败，请稍后重试。")
        except Exception as exc:
            logger.exception("QQRecord /record_private 命令异常: %s", exc)
            yield event.plain_result("命令执行失败，请稍后重试。")

    @require_admin("仅管理员可用该命令。")
    async def record_whitelist_command(
        self,
        event: AstrMessageEvent,
        action: str | None = None,
        *args: str,
    ):
        """命令 `/record_whitelist [add|remove|list|clear] [群号...]`：群白名单管理（管理员限定）。"""
        try:
            if action is None or action.lower() == "list":
                if not self._api.group_whitelist:
                    yield event.plain_result("群白名单为空（记录所有群聊）。")
                else:
                    yield event.plain_result(f"群白名单：{', '.join(self._api.group_whitelist)}")
                return
            action_lower = action.lower()
            if action_lower == "clear":
                self._api.group_whitelist = []
                await self._api.save_config("group_whitelist", [])
                yield event.plain_result("已清空群白名单（将记录所有群聊）。")
                return
            if action_lower in ("add", "remove"):
                if not args:
                    yield event.plain_result(f"请提供群号，例如：/record_whitelist {action_lower} 123456789")
                    return
                group_ids = [arg.strip() for arg in args if arg.strip()]
                if action_lower == "add":
                    added = []
                    for gid in group_ids:
                        if gid not in self._api.group_whitelist:
                            self._api.group_whitelist.append(gid)
                            added.append(gid)
                    if added:
                        await self._api.save_config("group_whitelist", self._api.group_whitelist)
                        yield event.plain_result(f"已添加群号到白名单：{', '.join(added)}")
                    else:
                        yield event.plain_result("所有群号已在白名单中。")
                else:
                    removed = []
                    for gid in group_ids:
                        if gid in self._api.group_whitelist:
                            self._api.group_whitelist.remove(gid)
                            removed.append(gid)
                    if removed:
                        await self._api.save_config("group_whitelist", self._api.group_whitelist)
                        yield event.plain_result(f"已从白名单移除群号：{', '.join(removed)}")
                    else:
                        yield event.plain_result("未找到匹配的群号。")
                return
            yield event.plain_result(
                "参数无效，用法：\n"
                "/record_whitelist list - 查看白名单\n"
                "/record_whitelist add <群号...> - 添加群号\n"
                "/record_whitelist remove <群号...> - 移除群号\n"
                "/record_whitelist clear - 清空白名单"
            )
        except (ValueError, TypeError) as exc:
            logger.warning("QQRecord /record_whitelist 参数错误: %s", exc, exc_info=exc)
            yield event.plain_result("参数无效，请检查后重试。")
        except (RuntimeError, OSError) as exc:
            logger.warning("QQRecord /record_whitelist 命令失败: %s", exc, exc_info=exc)
            yield event.plain_result("群白名单操作失败，请稍后重试。")
        except Exception as exc:
            logger.exception("QQRecord /record_whitelist 命令异常: %s", exc)
            yield event.plain_result("命令执行失败，请稍后重试。")

    @require_admin("仅管理员可用该命令。")
    async def record_sanitize_command(
        self,
        event: AstrMessageEvent,
        flag: str | None = None,
    ):
        """命令 `/record_sanitize [on|off|status]`：数据脱敏开关（管理员限定）。"""
        try:
            if flag is None or str(flag).strip().lower() == "status":
                state = "on" if self._api.enable_sanitization else "off"
                yield event.plain_result(f"数据脱敏状态：{state}")
                return
            val = str(flag).strip().lower()
            if val in ("on", "true", "1"):
                self._api.enable_sanitization = True
                await self._api.save_config("enable_sanitization", True)
                yield event.plain_result("已开启数据脱敏（手机号、token、cookie等将被脱敏）。")
            elif val in ("off", "false", "0"):
                self._api.enable_sanitization = False
                await self._api.save_config("enable_sanitization", False)
                yield event.plain_result("已关闭数据脱敏（警告：可能记录敏感信息）。")
            else:
                yield event.plain_result(
                    "参数无效，请使用 /record_sanitize on 或 /record_sanitize off"
                )
        except (ValueError, TypeError) as exc:
            logger.warning("QQRecord /record_sanitize 参数错误: %s", exc, exc_info=exc)
            yield event.plain_result("参数无效，请检查后重试。")
        except (RuntimeError, OSError) as exc:
            logger.warning("QQRecord /record_sanitize 命令失败: %s", exc, exc_info=exc)
            yield event.plain_result("切换数据脱敏失败，请稍后重试。")
        except Exception as exc:
            logger.exception("QQRecord /record_sanitize 命令异常: %s", exc)
            yield event.plain_result("命令执行失败，请稍后重试。")
