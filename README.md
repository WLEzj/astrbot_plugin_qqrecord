# astrbot_plugin_qqrecord

AstrBot QQ群消息记录插件

以标准格式记录QQ群消息到本地文档中

## 功能特点

- 📝 自动记录所有QQ群消息
- 📅 按日期分组存储消息记录
- 🔍 采用 JSONL 格式，易于查询和分析
- 🎛️ 支持启用/禁用记录功能
- 📊 记录详细的消息信息，包括时间戳、发送者、消息内容等

## 安装

将本插件放置在 AstrBot 的插件目录中即可。

## 使用方法

### 消息记录格式

插件会自动记录所有QQ群消息，并以 JSONL（JSON Lines）格式存储在 `qq_message_records` 目录中。

每条记录包含以下字段：
- `timestamp`: 消息时间戳（ISO 8601格式）
- `session_id`: 会话ID（群号）
- `sender_id`: 发送者ID
- `sender_name`: 发送者昵称
- `message_text`: 纯文本消息内容
- `message_chain`: 消息链（包含图片、表情等复杂消息组件）

示例记录：
```json
{
  "timestamp": "2026-01-16T10:30:15.123456",
  "session_id": "123456789",
  "sender_id": "987654321",
  "sender_name": "张三",
  "message_text": "Hello, World!",
  "message_chain": [
    {"type": "Plain", "text": "Hello, World!"}
  ]
}
```

### 命令

- `/qqrecord` 或 `/qqrecord status` - 查看插件状态和统计信息
- `/qqrecord enable` - 启用消息记录功能
- `/qqrecord disable` - 禁用消息记录功能

### 记录文件

- 记录文件按日期命名，格式为 `YYYY-MM-DD.jsonl`
- 文件位置：`qq_message_records/` 目录
- 采用 JSONL 格式，每行一条JSON记录，便于逐行读取和处理

## 数据处理示例

### Python 读取记录

```python
import json

# 读取某一天的消息记录
with open('qq_message_records/2026-01-16.jsonl', 'r', encoding='utf-8') as f:
    for line in f:
        record = json.loads(line)
        print(f"{record['timestamp']} - {record['sender_name']}: {record['message_text']}")
```

### 统计分析

```python
import json
from collections import Counter

# 统计发言次数
sender_count = Counter()
with open('qq_message_records/2026-01-16.jsonl', 'r', encoding='utf-8') as f:
    for line in f:
        record = json.loads(line)
        sender_count[record['sender_name']] += 1

# 显示前10名最活跃用户
for sender, count in sender_count.most_common(10):
    print(f"{sender}: {count}条消息")
```

## 注意事项

- 消息记录文件可能包含个人隐私信息，请妥善保管
- 长期使用可能产生大量数据文件，建议定期清理或归档
- JSONL 格式支持流式处理，适合处理大文件

## 支持

- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
- [项目仓库](https://github.com/WLEzj/astrbot_plugin_qqrecord)

## 许可证

本项目使用 MIT 许可证。
