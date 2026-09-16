# astrbot_plugin_github_ops

让 AstrBot 用配置好的 GitHub 账号做仓库管理、提交代码、Issue / PR 协作。

- 插件名：`astrbot_plugin_github_ops`
- 作者：Elara
- 版本：0.5.1
- 需要 AstrBot `>=4.5.7`

主对话只注册一个函数工具 `github_ops(task, local_dir?)`。真正的建仓 / 提交 / issue / fork / PR 在子循环里用内层工具完成，**不会**把几十个工具和海量 schema 灌进每轮主对话上下文。

推本地源码时传 `local_dir`（绝对路径）。子循环只能读这个目录，插件原生读取文件并推送 GitHub，无需模型抄录文件正文。

## 介绍

1. **单入口门面**：主对话只暴露 `github_ops`，把完整任务写进 `task`。
2. **子循环隔离**：插件启动独立 `tool_loop_agent`，主聊天记录不会进子代理。
3. **本地目录沙盒**：`local_dir` 越界会拒；list / 推送默认尊重 `.gitignore`。
4. **文件级提交**：支持更新、删除单个文件，以及把本地目录 `sync` 到仓库（可删远端多余文件）。
5. **安全护栏**：写入白名单、敏感路径拦截、正文凭据扫描、输出脱敏。提交默认使用 GitHub noreply 邮箱。

相关链接：仓库 <https://github.com/ElaraKaya/astrbot_plugin_github_ops>

## Token 说明（必读）

填写的是 **GitHub Personal Access Token**（PAT），只写在本插件设置里，不要发到聊天。

PAT 建议权限：

- fine-grained：Contents、Issues、Pull requests、Administration、Metadata；Repository access 选 **All repositories**（或至少包含要写的仓库）
- classic：`repo`

`allow_delete_repo` 默认关，只控制**删整个仓库**。删仓库里的某个文件（例如 `MODEL.md`）不需要打开这项。

## 安装

### 方式一：插件市场（推荐）

在 AstrBot 管理面板打开 **插件市场**，搜索 `astrbot_plugin_github_ops` 或「GitHub Ops」，点击安装即可。

### 方式二：用仓库地址安装

在 AstrBot 管理面板的插件管理里，通过 GitHub 仓库地址安装：

```text
https://github.com/ElaraKaya/astrbot_plugin_github_ops
```

或在聊天里（需管理员）执行：

```text
plugin i https://github.com/ElaraKaya/astrbot_plugin_github_ops
```

安装后在插件列表里启用 / 重载，再打开本插件配置填写 PAT。

## 配置

最少只要填 Token：

| 配置 | 说明 |
| --- | --- |
| `github_token` | **必填。** GitHub PAT |
| `api_base` | 默认 `https://api.github.com`。GitHub Enterprise 才改 |
| `http_proxy` | 出口代理。留空走系统 `HTTP(S)_PROXY` |
| `git_committer_name` | 提交者显示名。留空用账号 login |
| `git_committer_email` | 提交者邮箱。留空自动用 `{id}+{login}@users.noreply.github.com` |
| `who_can_use` | `admin` 仅管理员；`all` 所有人可用 |
| `default_private` | 新建仓库默认私有 |
| `allow_delete_repo` | 是否允许**删整个仓库**。默认关。不影响删文件 |
| `allow_merge_pr` | 是否允许合并 PR |
| `max_steps` | 子循环最大工具步数，默认 24（范围 4–60） |
| `allowed_repos` | 可写仓库白名单。空=只能写当前登录账号名下的仓 |

## 使用

管理员发 `/github` 检查 API Key 与账号状态。`/github actions` 查看内层 action 说明。

自然语言示例：「建一个 private 仓 `toy-notes`，README 写一句话，把链接给我」。

模型应调用 `github_ops(task=完整任务)`。需要推本地目录时再加 `local_dir=/abs/path`。`task` 必须自包含；有 `local_dir` 时无需塞入文件正文。

同步本地插件目录、并删掉仓库里多出来的文件时，直接把这件事写进 `task` 即可。子循环会用 `github_files action=sync`（或 `delete` / `push` + `files[].delete=true`），不要把文件写成空内容来“假装删除”。

子循环遇到未知 action、缺参数或结果不符时会直接汇报，而不是换名字乱猜或另开测试分支。

### 给大模型用的工具

主对话只挂 `github_ops`。子循环内层工具：

- `github_whoami`：当前账号与速率限额（身份会预注入，一般不必再调）
- `github_repo`：list / get / create / delete / commits（delete = 删仓）
- `github_files`：list / get / put / push / delete / sync（delete = 删文件）
- `github_local`：读已授权本地目录
- `github_issue` / `github_pr` / `github_misc`：协作与 fork / star / 建分支 / 删分支（不能删默认分支）

## 更新日志

完整记录见 [CHANGELOG.md](CHANGELOG.md)。

### 0.5.1

- 新增删分支：`github_misc action=delete_branch`
- 禁止删除仓库默认分支；已不存在的分支返回 `already_absent`

### 0.5.0

- 支持删文件：`github_files action=delete`，以及 `push`/`sync` 的 `files[].delete=true`
- 新增 `sync`：按本地目录对齐远端，整目录默认同步并删除多余文件
- 空 / `null` content 不再写成空文件
- 未知 action 返回 `allowed` 和用法说明
- `github_local` list / 推送默认尊重 `.gitignore`
- png 等资源可按二进制 blob 推送；push 结果区分 `updated` / `deleted`
- 配置项 `max_steps`（默认 24）；身份预注入子循环
- 子循环遇到意外情况直接汇报，不再猜 action 或另开测试分支

## 许可证

本项目基于 [MIT License](LICENSE) 开源。

```text
MIT License

Copyright (c) 2026 Elara

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
