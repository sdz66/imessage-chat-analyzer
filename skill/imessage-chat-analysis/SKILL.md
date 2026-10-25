---
name: imessage-chat-analysis
description: 导出 iMessage/短信聊天记录并做统计分析与 AI 文字分析（关系/感情状态分析、聊天年度报告、沟通模式总结）。当用户想分析 iMessage 或短信聊天记录、做聊天数据年度报告、分析两个人聊天记录的感情状态或沟通习惯，或提到 imessage-exporter、chat.db、聊天记录导出时使用。仅限 macOS/iOS 备份数据。
---

# iMessage 聊天记录分析

流程：chat.db →（imessage-exporter 导出 txt）→ imsg_analyzer 统计 + 精简转写 → 按 prompts/ 模板产出分析报告。

## 1. 确保已有导出

- 若 `~/imessage_export/` 已存在 txt，直接使用。
- 否则（仅 macOS，需用户配合）：
  1. 检查终端 App 是否有「完全磁盘访问权限」（系统设置 → 隐私与安全性）。没有时 `chat.db` 不可读，会报 `unable to open database file`；授权后必须完全退出并重启终端 App。
  2. `brew install imessage-exporter`
  3. `imessage-exporter -d` 验证权限通了。
  4. 导出：单会话 `imessage-exporter -f txt -t "手机号或邮箱"`；全量 `imessage-exporter -f txt`。默认输出 `~/imessage_export/`。
  5. iOS 备份场景：`imessage-exporter -f txt -p /path/to/backup`（加密备份会提示输密码）。
- 不可代用户授权系统权限；`brew` 安装失败时提示用户手动执行。

## 2. 运行解析器

解析器随本仓库分发：本 SKILL.md 位于 `<仓库根>/skill/imessage-chat-analysis/`，向上两级即仓库根；若独立安装，先 `git clone` 本仓库。纯标准库，无需安装依赖。

```bash
cd <仓库根>
# 单会话
python3 -m imsg_analyzer ~/imessage_export/某会话.txt -o imsg_analysis --name "会话别名"
# 批量（整个导出目录）
python3 -m imsg_analyzer ~/imessage_export -o imsg_analysis
```

可选：`--map "原名=显示名"` 脱敏别名（可重复）、`--gap` 会话切分小时数（默认 4）、`--top` emoji 展示数。

每个会话产出三件套：`*.stats.md`（统计报告）、`*.stats.json`（机器可读）、`*.condensed.txt`（LLM 友好转写：媒体折叠为 `[图×N]` 标签，按天分节）。批量模式额外产出 `index.md` 总览。

## 3. 产出分析

1. 读 `stats.md` 拿数字，读 `condensed.txt` 拿内容；转写很大时分段读（单次 >25k tokens 就用 offset 分页），优先读完整个时间跨度再下结论。
2. 按用户目的选模板：
   - 关系/感情状态/两人互动分析 → `prompts/relationship-analysis.md`
   - 年度报告/阶段性总结 → `prompts/annual-report.md`
   - 其他（找信息、总结事项）→ 直接基于转写分析。
3. 报告中每个关键结论必须引用带时间戳的原文证据；没有证据的推断要明确标注为推测。

## 4. 隐私红线（必须遵守）

- 只处理用户本人参与、且用户有权处理的会话；不要主动批量读取与本次请求无关的会话内容。
- 把聊天原文发给云端 LLM/外部服务前，必须提醒用户内容含第三方隐私，建议先脱敏（`--map` 别名、`--name` 去掉手机号文件名）或获得用户明确同意。
- 输出中不复述验证码、精确家庭/学校地址、他人完整手机号等敏感字段。
- 分析结论是文本层面的"势能"，不是对人的判决；涉及感情分析时始终保留不确定性说明。
