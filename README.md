# astrbot_plugin_github_ops

把 **一个 GitHub 账号交给 AstrBot 自己用**。

主对话只注册一个函数工具 `github_ops(task)`。真正的建仓 / 提交 / issue / fork / PR 在子循环里用 6 个内层工具完成，**不会**把官方 GitHub MCP 那几十个工具灌进每轮上下文。

灵感来自 [GitHub official MCP](https://github.com/github/github-mcp-server) 的能力范围，以及 AstrBot 官方 Multi-Agent（`tool_loop_agent`）写法。实际请求走 GitHub REST（`httpx`）。

## 这个号是谁的

配置里的 PAT 对应的就是 **bot 的号**，不是你日常开发号。建议单独注册一个 GitHub 账号。

## 安装

1. 把本目录放到 `AstrBot/data/plugins/astrbot_plugin_github_ops/`
2. 安装依赖：`httpx`
3. WebUI 重载插件
4. 插件配置里填写 `github_token`（不要发到聊天里）

打包 zip 时不要带 `__pycache__` / `*.pyc`。

PAT：

- fine-grained：Contents、Issues、Pull requests、Administration、Metadata；Repository access 选 **All repositories**
- classic：`repo`

## 配置

| 项 | 默认 | 含义 |
| --- | --- | --- |
| github_token | 空 | bot 自己的 PAT |
| api_base | `https://api.github.com` | GHE 才改 |
| http_proxy | 空 | 出口代理。留空走 `HTTP(S)_PROXY` |
| who_can_use | admin | `admin` 仅管理员；`all` 谁都能让 bot 动这个号 |
| default_private | true | 新建仓库默认私有 |
| allow_delete_repo | false | 是否允许删仓 |
| allow_merge_pr | true | 是否允许合并 PR |
| allowed_repos | `[]` | 可写白名单。空=只能写 bot 自己 login 下的仓。fork 源仓 / star / 只读不受限 |

## 使用

管理员发 `/github` 检查号是否可用。

自然语言：「用你的 GitHub 建一个 private 仓 `toy-notes`，README 写一句话，把链接给我」。

模型应调用 `github_ops(task=完整任务)`。`task` 必须自包含（含子循环要提交的文件内容）。

测试步骤见 [TEST.md](TEST.md)。

## 许可证

MIT
