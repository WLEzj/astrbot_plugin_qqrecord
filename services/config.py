from astrbot.api import sp


class ConfigStore:
    def __init__(self, *, plugin, plugin_id: str, log_debug_exception):
        self._plugin = plugin
        self._plugin_id = plugin_id
        self._log_debug_exception = log_debug_exception

    async def get(self, key: str, default):
        getter = getattr(self._plugin, "get_kv_data", None)
        if callable(getter):
            try:
                return await getter(key, default)
            except (AttributeError, TypeError, RuntimeError) as exc:
                self._log_debug_exception("QQRecord KV 读取异常", exc, key=key)
        try:
            return await sp.get_async("plugin", self._plugin_id, key, default)
        except Exception as exc:
            self._log_debug_exception("QQRecord KV 读取异常", exc, key=key)
            return default

    async def put(self, key: str, value):
        setter = getattr(self._plugin, "put_kv_data", None)
        if callable(setter):
            try:
                await setter(key, value)
                return
            except (AttributeError, TypeError, RuntimeError) as exc:
                self._log_debug_exception("QQRecord KV 写入异常", exc, key=key)
        try:
            await sp.put_async("plugin", self._plugin_id, key, value)
        except Exception as exc:
            self._log_debug_exception("QQRecord KV 写入异常", exc, key=key)
