# astrbot_plugin_github_ops

让 AstrBot 利用 GitHub API Key 进行仓库管理、代码提交与协作操作。

主对话只注册一个函数工具 `github_ops(task, local_dir?)`。真正的建仓 / 提交 / issue / fork / PR 在子循环里用内层工具完成，**不会**把几十个工具和海量 schema 灌进每轮主对话上下文。

推本地源码时传 `local_dir`（绝对路径）。子循环只能读这个目录，插件原生读取文件并推送 GitHub，无需模型抄录文件正文。

## 工作原理

1. **单入口门面架构**
   主对话上下文仅暴露一个统一工具 `github_ops`，接收自包含任务描述 `task`（以及可选本地路径 `local_dir`）。模型无需在主上下文加载数十个繁杂的细粒度 action。

2. **Tool-Loop 子代理隔离**
   调用 `github_ops` 后，插件启动独立的 `tool_loop_agent` 执行子循环。子循环中传入独立的微工具集（ToolSet）以及隔离上下文（`contexts=[]`），主聊天记录不会泄露至子代理，避免上下文污染和安全外溢。

3. **内置内层工具集**
   子循环内仅挂载精简的专项工具：
   - `github_whoami`：查询并校验当前认证账号与速率限额
   - `github_repo`：仓库管理（list / get / create / delete）
   - `github_files`：文件读写与提交（list / get / put / push），支持引用本地目录批量推送
   - `github_local`：安全沙盒读取本地授权目录结构与文件
   - `github_issue`：Issue 生命周期管理（list / get / create / comment / update）
   - `github_pr`：Pull Request 查看、创建与合并（list / create / merge）
   - `github_misc`：辅助协作操作（fork / star / branch）

4. **安全护栏机制**
   - **权限审查**：配置 `who_can_use`（默认仅 admin 可触发），无权会话立即拦截；
   - **写入白名单**：`allowed_repos` 严格限制写操作目标，默认仅允许写入当前凭证所属账号；
   - **敏感文件拦截**：自动拦截 `.env`、`id_rsa`、`credentials`、`*.pem` 等敏感密钥提交与读取；
   - **Token 防泄漏**：内层 Prompt 强约束与外层脱敏过滤，严禁在日志与模型回复中暴露 Token。

## 安装

1. 把本目录放到 `AstrBot/data/plugins/astrbot_plugin_github_ops/`
2. 安装依赖：`httpx`
3. WebUI 重载插件
4. 插件配置里填写 `github_token`

PAT 建议权限：
- fine-grained：Contents、Issues、Pull requests、Administration、Metadata；Repository access 选 **All repositories**
- classic：`repo`

## 配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| github_token | 空 | GitHub 个人访问令牌 (PAT) |
| api_base | `https://api.github.com` | GitHub API 基础 URL，GHE 场景可按需修改 |
| http_proxy | 空 | 出口代理地址。留空走系统环境变量 `HTTP(S)_PROXY` |
| who_can_use | admin | 权限控制：`admin` 仅管理员；`all` 所有人可用 |
| default_private | true | 新建仓库默认设置为私有 |
| allow_delete_repo | false | 是否允许执行删仓操作 |
| allow_merge_pr | true | 是否允许合并 PR |
| allowed_repos | `[]` | 允许写入的仓库白名单。为空时仅允许写入当前登录账号名下的仓库 |

## 使用

管理员发 `/github` 检查 API Key 与账号状态。

自然语言调用示例：「建一个 private 仓 `toy-notes`，README 写一句话，把链接给我」。

模型应调用 `github_ops(task=完整任务)`。需要推本地目录时再加 `local_dir=/abs/path`。`task` 必须自包含；有 `local_dir` 时无需塞入文件正文。

## 许可证

MIT
