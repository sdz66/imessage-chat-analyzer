# imessage-chat-analyzer

把 [imessage-exporter](https://github.com/ReagentX/imessage-exporter) 导出的 iMessage 聊天记录，变成**统计报告 + LLM 友好转写**，再按提示词模板产出 AI 分析（关系/沟通分析、聊天年度报告等）。

```
chat.db ──imessage-exporter──▶ *.txt ──imsg_analyzer──▶ stats.md / stats.json / condensed.txt
                                                              │
                                                    LLM + prompts/ 模板
                                                              ▼
                                                       分析报告
```

纯 Python 标准库，零依赖，全部本地处理。

## 快速开始（macOS）

### 1. 导出聊天记录

```bash
brew install imessage-exporter

# 终端需要「完全磁盘访问权限」：系统设置 → 隐私与安全性 → 完全磁盘访问权限
# 授权后必须完全退出并重启终端 App
imessage-exporter -d                 # 验证权限
imessage-exporter -f txt             # 全量导出到 ~/imessage_export/
imessage-exporter -f txt -t "手机号"  # 只导某个会话
```

iOS 备份也支持：`imessage-exporter -f txt -p /path/to/backup`（加密备份会提示输密码）。格式细节见 [docs/format.md](docs/format.md)。

### 2. 生成统计与转写

```bash
python3 -m imsg_analyzer ~/imessage_export/某会话.txt -o imsg_analysis

# 常用选项
python3 -m imsg_analyzer ~/imessage_export/某会话.txt \
    -o imsg_analysis \
    --name "和朋友" \                # 报告标题（默认为文件名，通常是手机号）
    --map "+8617xxxxxxxxx=朋友A"     # 发送者脱敏别名，可重复

# 批量分析整个导出目录（额外产出 index.md 总览表）
python3 -m imsg_analyzer ~/imessage_export -o imsg_analysis
```

每个会话产出三件套：

| 文件 | 用途 |
|---|---|
| `*.stats.md` | 人类可读统计：双方消息量/打字量/图片比、回复延迟、提问数、引用回复、会话开启与收尾、深夜会话、小时分布、高频 emoji、特效与 Tapback |
| `*.stats.json` | 同内容的机器可读版 |
| `*.condensed.txt` | LLM 友好转写：媒体折叠为 `[图×N]` 标签、按天分节、引用上下文去重，可直接作为分析上下文 |

### 3. AI 分析

把 `stats.md` + `condensed.txt` 喂给你喜欢的 LLM，按目的选模板：

- [prompts/relationship-analysis.md](prompts/relationship-analysis.md) — 两人关系/感情状态分析：信号清单（主动性、注意力、语言寄生、行为跟随）、自我暴露阶梯、阶段判断、防过度解读检查、行动建议
- [prompts/annual-report.md](prompts/annual-report.md) — 聊天年度报告：时间画像、语言画像、话题年轮、里程碑、温度曲线

样例输入输出见 [examples/](examples/)。

## 本地 Web 控制台（一键导出 + 分析）

不想敲命令的话，一个入口全搞定：

```bash
python3 -m imsg_analyzer.webui                 # 默认读 ~/imessage_export，自动打开浏览器
python3 -m imsg_analyzer.webui <其他导出目录> -p 9000
```

单页界面（只绑定 127.0.0.1）：

- 会话列表按消息量排序，支持搜索
- 「运行全量导出」按钮直接调用 imessage-exporter（同样要求该终端 App 有完全磁盘访问权限）
- 每个会话的统计报告（渲染成表格）/ 转写全文（复制、下载）
- AI 分析：选模板 → 填 OpenAI 兼容 API 地址与 Key → 一键出报告

密钥安全：API Key 只保存在页面内存中，随请求直达你填写的 API 地址；本服务不落盘、不经手任何第三方。

## iPhone 用户流程

iOS 沙盒不允许在手机上直接导出 iMessage——"一键"发生在电脑上，iPhone 用户多一步备份：

1. 用数据线把 iPhone 备份到电脑：Mac 用 Finder（选中设备 → 立即备份），Windows 用 iTunes；**加密备份也可以**，后面能解
2. 导出：`imessage-exporter -f txt -p "<备份目录>"`
   - macOS 备份目录：`~/Library/Application Support/MobileSync/Backup/<设备UDID>`
   - Windows 备份目录：`%USERPROFILE%\Apple\MobileSync\Backup\<设备UDID>`
   - 加密备份会提示输入备份密码
3. 之后与 Mac 用户完全一致：`imsg_analyzer` 转析、Web 控制台、skill 全部通用

## 作为 Agent Skill 使用

本仓库自带一个 [skill](skill/imessage-chat-analysis/SKILL.md)，安装后 agent 会自动处理"导出 → 解析 → 分析"全流程：

```bash
# Codex / ZCode（用户级 skill 目录）
ln -sfn "$(pwd)/skill/imessage-chat-analysis" ~/.codex/skills/imessage-chat-analysis
# 或 ZCode：
ln -sfn "$(pwd)/skill/imessage-chat-analysis" ~/.zcode/skills/imessage-chat-analysis
```

skill 内置隐私红线：只分析用户有权处理的会话、上传云端前先脱敏征求同意、不复述验证码/地址等敏感字段。

## 统计口径速查

| 指标 | 定义 |
|---|---|
| 会话（session） | 相邻消息间隔 ≥ 4h 切分（`--gap` 可调） |
| 回复延迟 | 发送者切换时的间隔，上限 1 个会话阈值 |
| 深夜会话 | 含 23:00–02:00 消息的会话 |
| 提问数 | 含 `？/?` 或以 吗/么/呢/吧/啥 结尾或含 疑问词 的消息 |
| 已读延迟 | 从已读回执 `Read by you/them after ...` 解析 |

## 已知边界

- 依赖 imessage-exporter 处理 typedstream 等底层格式，本工具只消费其 txt 输出；转写保真度以导出器为准。
- 表情回应的"谁回应了谁"在 txt 中只有部分信息，`tapbacks` 仅统计类型计数。
- 群聊文件（导出器按群名落盘）同样支持，统计按发送者分组；`condensed.txt` 不做角色折叠。
- 隐私提醒：`~/imessage_export/` 与分析输出包含全部历史明文，**不要提交进任何仓库**（`.gitignore` 已兜底 `exports/`、`imsg_analysis/`）。本仓库内所有示例（`examples/`、`docs/` 中的对话片段）均为合成数据，不含任何真实聊天内容。

## License

[MIT](LICENSE)
