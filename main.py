import json
from datetime import datetime
from pathlib import Path
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

@register("astrbot_plugin_qqrecord", "WLEzj", "以标准格式记录QQ群消息到本地文档中", "1.0.0")
class QQRecordPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 设置消息记录存储路径
        self.record_dir = Path("qq_message_records")
        self.enabled = True

    async def initialize(self):
        """初始化插件，创建记录目录"""
        try:
            # 创建消息记录目录
            self.record_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"QQ消息记录插件已初始化，记录目录: {self.record_dir.absolute()}")
        except Exception as e:
            logger.error(f"初始化QQ消息记录插件失败: {e}")

    @filter.on_message()
    async def record_message(self, event: AstrMessageEvent):
        """记录所有QQ群消息"""
        if not self.enabled:
            return
        
        try:
            # 获取当前时间（避免多次调用导致的不一致）
            now = datetime.now()
            
            # 获取消息信息
            sender_name = event.get_sender_name()
            sender_id = event.sender_id if hasattr(event, 'sender_id') else "unknown"
            message_str = event.message_str
            
            # 获取群组信息
            session_id = event.session_id if hasattr(event, 'session_id') else "unknown"
            
            # 获取消息链（包含图片、表情等）
            message_chain = event.get_messages()
            
            # 构建记录数据
            record_data = {
                "timestamp": now.isoformat(),
                "session_id": session_id,
                "sender_id": str(sender_id),
                "sender_name": sender_name,
                "message_text": message_str,
                "message_chain": self._serialize_message_chain(message_chain)
            }
            
            # 按日期分组存储
            today = now.strftime("%Y-%m-%d")
            record_file = self.record_dir / f"{today}.jsonl"
            
            # 以追加模式写入记录
            with open(record_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record_data, ensure_ascii=False) + "\n")
                
        except Exception as e:
            logger.error(f"记录消息失败: {e}")

    def _serialize_message_chain(self, message_chain):
        """序列化消息链为可存储的格式"""
        try:
            serialized = []
            for msg in message_chain:
                # 尝试获取消息组件的基本信息
                msg_dict = {
                    "type": type(msg).__name__
                }
                
                # 尝试获取文本内容
                if hasattr(msg, 'text'):
                    msg_dict["text"] = msg.text
                elif hasattr(msg, 'content'):
                    msg_dict["content"] = str(msg.content)
                else:
                    msg_dict["data"] = str(msg)
                    
                serialized.append(msg_dict)
            return serialized
        except Exception as e:
            logger.warning(f"序列化消息链失败: {e}")
            return [{"type": "unknown", "data": str(message_chain)}]

    @filter.command("qqrecord")
    async def qqrecord_command(self, event: AstrMessageEvent):
        """QQ消息记录控制指令"""
        message_str = event.message_str.strip()
        
        # 解析命令参数
        parts = message_str.split()
        
        if len(parts) == 0 or parts[0] == "status":
            # 显示状态
            status = "已启用" if self.enabled else "已禁用"
            total_records = self._count_total_records()
            yield event.plain_result(
                f"QQ消息记录插件状态: {status}\n"
                f"记录目录: {self.record_dir.absolute()}\n"
                f"总记录数: {total_records}"
            )
        elif parts[0] == "enable":
            # 启用记录
            self.enabled = True
            yield event.plain_result("QQ消息记录已启用")
        elif parts[0] == "disable":
            # 禁用记录
            self.enabled = False
            yield event.plain_result("QQ消息记录已禁用")
        else:
            # 显示帮助信息
            yield event.plain_result(
                "QQ消息记录插件命令:\n"
                "/qqrecord status - 查看状态\n"
                "/qqrecord enable - 启用记录\n"
                "/qqrecord disable - 禁用记录"
            )

    def _count_total_records(self):
        """统计总记录数"""
        try:
            total = 0
            for file in self.record_dir.glob("*.jsonl"):
                with open(file, "r", encoding="utf-8") as f:
                    total += sum(1 for _ in f)
            return total
        except Exception as e:
            logger.error(f"统计记录数失败: {e}")
            return 0

    async def terminate(self):
        """插件销毁时的清理操作"""
        logger.info("QQ消息记录插件已停止")
