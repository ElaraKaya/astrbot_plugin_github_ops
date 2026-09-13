# AstrBot 测试计划（github_ops）

在 **运行中的 AstrBot** 上测，不要只看代码。版本需要 `>= 4.5.7`（用到 `tool_loop_agent` / `add_llm_tools`）。

准备：bot 专用 GitHub PAT 已填进插件配置；你是管理员；先私聊测，再考虑群聊。

## 0. 装上就能跑

1. 插件放到 `data/plugins/astrbot_plugin_github_ops/`，WebUI 重载。
2. 行为管理 → 函数工具：只能看到 **`github_ops`**，不能看到 `github_repo` / `github_files` 等 6 个内层工具。
3. Skills 页能看到插件内置 `github-ops`，保持启用。
4. 管理员发 `/github`：打出 bot 的 login、rate limit。填错 token 应报校验失败，且回复里 **没有** token 原文。

失败：重载报错、内层工具出现在全局列表、`/github` 无响应。

## 1. 主对话上下文（空转）

发一句无关 GitHub 的话，例如「今天星期几」。

过：正常回答；日志/抓包里这一轮的 tools 只有 `github_ops`（外加你本来就开着的其它插件工具），没有 6 个内层 schema。

大约量级：`github_ops` 自身 150～250 token。

## 2. 自包含 task（核心路径）

私聊管理员，一次说清：

> 用你自己的 GitHub 新建 private 仓库 `ab-ops-smoke`，README.md 写 `# smoke`，把 html_url 给我。不要把文件全文再贴回来。

过：

- 模型调用 `github_ops`，参数是 **`task` 一整段**，不要自己拆 21 个 action。
- GitHub 上出现 private 仓，README 内容正确。
- 回复里有 `html_url`，没有 PAT，没有大段 JSON。
- 子循环最多约 12 步：whoami → create → put/push。

失败：只建了空仓没文件；模型说「请再说一次」；task 里没有 README 正文（子循环看不到 QQ 历史）。

## 2b. 本地目录推仓（不抄正文）

准备一个只有几个文本文件的目录，例如 `/tmp/ab-ops-local`（含 `README.md`，不要放 `.env`）。

> 把 `/tmp/ab-ops-local` 推到你自己的 private 仓 `ab-ops-local`，不要把文件正文贴进 task。

过：

- 外层调用是 `github_ops(task=..., local_dir=/tmp/ab-ops-local)`，task 里没有文件全文。
- 子循环用 `github_files` 的 `local_paths`（或省略后推整个 granted 目录），不要再生成 `files[].content`。
- 读 `../`、`/etc/passwd`、`.env` 都被拒。
- GitHub 上出现对应文本文件；`.env` / `__pycache__` / 二进制不在仓里。

失败：task 里又被塞进全文；越界路径被读到；仓里出现密钥文件。

## 3. 子循环不要污染下一轮

接着发：「随便聊聊天气」。

过：回答天气，不必再带上刚才那串 GitHub 工具结果。

失败：下一轮上下文里出现 `github_files` 的完整 JSON / 文件正文。记下 AstrBot 版本，这是 `tool_loop_agent` 把子循环写进了当前会话。

## 4. 权限闸

| 步骤 | 操作 | 期望 |
| --- | --- | --- |
| 4.1 | `who_can_use=admin`，非管理员私聊「列出你的仓库」 | 拒绝，GitHub 上无新请求（或仅谁ami也没有） |
| 4.2 | 管理员同样一句话 | 能列出 |
| 4.3 | 保持 `allow_delete_repo=false`，「把 ab-ops-smoke 删掉」 | 拒绝删除，仓还在 |
| 4.4 | 「把这段写进 `.env`：SECRET=1」 | 拒绝敏感路径，仓里没有 `.env` |
| 4.5 | 「读取 `id_rsa` / `foo.pem`」 | 拒绝 |
| 4.6 | `allowed_repos` 仍为空，「在别人的仓 `octocat/Hello-World` 开 issue」 | 拒绝写入 |
| 4.7 | 「star `octocat/Hello-World`」或 fork 一个小公开仓 | 允许（只读/fork 源仓不走可写白名单） |

## 5. 读写回归

在 `ab-ops-smoke` 上各做一次（仍用自然语言，让它走 `github_ops(task=...)`）：

1. 列出文件
2. 再提交一个 `notes.txt`
3. 开 issue，标题 `ops-test`
4. 给这个 issue 回一楼
5. 建分支 `try-pr`，改一文件，开 PR（可不开 merge）

过：GitHub 网页状态和 bot 回复的 number / html_url 一致；issue body 若很长，工具返回被截到约 2000 字，外层回复 ≤ 约 1500 字。

## 6. 失败形态

1. 清空 `github_token` 再重载，让它建仓 → 明确说没配 token，不要堆栈。
2. 仓库名非法（空格、超长）→ GitHub 错误 message，不是插件崩溃。
3. 同一句里塞特别长的文件（> 几十 KB）→ 要么截断成功，要么报错，进程不卡死。看 `tool_call_timeout`（60s）。

## 7. 群聊（可选）

默认不要把 `who_can_use` 改成 `all`。若要测群：

1. 仍是 `admin`：群友喊建仓，应拒绝。
2. 临时改 `all`：群友能建；测完改回 `admin`。
3. 确认 bot 不会在群里把 PAT 或 `.env` 内容发出来。

## 8. 收尾

1. 不想留测试仓就网页上手动删（插件默认不能删）。
2. 打包分发：zip 根目录是插件文件夹；排除 `__pycache__`、`*.pyc`、`.pytest_cache`。
3. 记录：AstrBot 版本、模型、`tool_loop_agent` 是否接受 `contexts`、子循环是否写入会话（第 3 节）。

## 不测

- 官方 GitHub MCP 启停
- 热加载另一个 GitHub 插件
- 用你本人的 GitHub 号
