# astrbot_plugin_github_ops

让 AstrBot 利用 GitHub API Key 进行仓库管理、代码提交与协作操作。

主对话只注册一个函数工具 `github_ops(task, local_dir?)`。真正的建仓 / 提交 / issue / fork / PR 在子循环里用内层工具完成，**不会**把官方 GitHub MCP 那几十个工具灌进每轮上下文。

推本地源码时传 `local_dir`（绝对路径）。子循环只能读这个目录，插件自己读文件推 GitHub，不再让内层模型抄正文。

灵感来自 [GitHub official MCP](https://github.com/github/github-mcp-server) 的能力范围，以及 AstrBot 官方 Multi-Agent（`tool_loop_agent`）写法。实际请求走 GitHub REST（`httpx`）。

## 安装

1. 把本目录放到 `AstrBot/data/plugins/astrbot_plugin_github_ops/`
2. 安装依赖：`httpx`
3. WebUI 重载插件
4. 插件配置里填写 `github_token`（不要发到聊天里）

打包 zip 时不要带 `__pycache__` / `*.pyc`。

PAT 建议权限：

- fine-grained：Contents、Issues、Pull requests、Administration、Metadata；Repository access 选 **All repositories**
- classic：`repo`

## 配置

| 项 | 默认 | 含义 |
| --- | --- | --- |
| github_token | 空 | GitHub PAT / API Key |
| api_base | `https://api.github.com` | GHE 才改 |
| http_proxy | 空 | 出口代理。留空走 `HTTP(S)_PROXY` |
| who_can_use | admin | `admin` 仅管理员；`all` 所有人可用 |
| default_private | true | 新建仓库默认私有 |
| allow_delete_repo | false | 是否允许删仓 |
| allow_merge_pr | true | 是否允许合并 PR |
| allowed_repos | `[]` | 可写白名单。空=只能写当前账号名下的仓。fork 源仓 / star / 只读不受限 |

## 使用

管理员发 `/github` 检查 API Key 与账号状态。

自然语言调用示例：「建一个 private 仓 `toy-notes`，README 写一句话，把链接给我」。

模型应调用 `github_ops(task=完整任务)`。需要推本地目录时再加 `local_dir=/abs/path`。`task` 必须自包含；有 `local_dir` 就不要再塞文件正文。

测试步骤见 [TEST.md](TEST.md)。

## 许可证

MIT
