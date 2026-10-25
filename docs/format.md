# imessage-exporter txt 转写格式说明

本目录解析器针对 [ReagentX/imessage-exporter](https://github.com/ReagentX/imessage-exporter) 的 `-f txt` 输出。格式要点（也是 `imsg_analyzer/parser.py` 的解析规则）：

## 消息块

```
Jan 9, 2026  7:30:00 PM (Read by them after 2 minutes, 30 seconds)
小李
周六的创意市集去吗
```

- 第一行：时间戳，括号内为已读回执（`Read by you/them after ...`）。
- 第二行：发送者，`Me` 或联系人名（iOS 备份导出可能只有号码）。
- 之后到空行为消息体，可多行。

## 系统事件

事件文本直接挂在时间戳行尾，无发送者行：

```
Jan 10, 2026  8:00:10 AM Me sent a Digital Touch heartbeat.
Jan 10, 2026  8:05:00 AM You unsent a message!
```

解析为 `sender=SYSTEM`，不参与统计。

## 缩进行

- 引用回复的上下文（原时间戳/发送者/正文）以 4 空格缩进重复出现，解析时丢弃。
- `Tapbacks:` 下一行的缩进 `Liked by 某人` 记录表情回应类型。

## 特殊消息体

| 原文特征 | 含义 | 解析结果 |
|---|---|---|
| `Sent with Loud/Echo/...` | 消息特效 | `effects` |
| `This message responded to an earlier message.` | 回复消息 | `is_reply=True` |
| `/Users/<user>/Library/Messages/Attachments/...` | 附件绝对路径 | 按扩展名计图片/视频 |
| `/Users/<user>/Library/Messages/StickerCache/...` 或含 `Sticker from` | 贴纸 | `stickers` |
| `Digital Touch Sketch (N strokes, M points)` | 手绘 | `dtouch` |
| 裸文件名如 `IMG_0001.HEIC`（可混在文字中） | 附件引用 | 抽出计媒体，余下文字保留 |
| `https://maps.apple.com/...` | 位置分享 | `locations` |
| `Jan 11, 2026 11:00:00 PM 原文 Edited 2 minutes later: 新文` | 编辑过的消息 | 去掉前缀时间戳后按文本保留 |

## 转写输出（condensed.txt）

- 媒体折叠为 `[图×N] [视频×N] [贴纸×N] [手绘] [位置]` 标签。
- 回复消息带 `↩` 标记；引用上下文与 Tapback 明细不保留（避免重复占上下文）。
- 按自然日分节，节头含星期；`Me` 默认显示为 `我`，可用 `--map` 改。
