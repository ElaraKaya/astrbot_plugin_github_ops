---
name: github-ops
description: >
  The bot owns its own GitHub account. For any GitHub work on that account,
  call github_ops(task=the full job, including file contents). Do not split
  into 21 actions. Read this skill before the first GitHub action.
---

# Bot 的 GitHub 号

这不是用户的 GitHub。这是 **bot 自己的账号**。

主对话只调 **一次** `github_ops`：

```
github_ops(task="完整任务，含要提交的文件正文")
```

不要自己拆 action，也不要找 MCP 里的 `create_repository`。子循环看不到 QQ 聊天记录，所以 **task 必须自包含**。

## 规则

1. 没要求公开就建 private 仓。
2. 不要输出 token / PAT / Authorization。
3. 不要在别人的仓库里乱开 issue / 乱提 PR，除非用户明确点名。
4. 不要提交 `.env`、私钥、`credentials`、聊天记录。
5. `delete` 默认是关的。没开就不要删仓。
6. fork 的 owner 是源仓，fork 到 bot 自己的号；刚 fork 完可能要等几秒才能写。
7. 用户需要落点时可以自己建 private 仓并提交，然后只回 `html_url`。不要无意义地建空仓。
