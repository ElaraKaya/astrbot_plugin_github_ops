---
name: github-ops
description: >
  Execute GitHub tasks using the configured GitHub account. For any GitHub work,
  call github_ops(task=the full job, local_dir=absolute path when pushing
  local files). Do not split into granular actions. Read this skill before the
  first GitHub action.
---

# GitHub Ops

通过配置的 GitHub 账号执行仓库管理与协作操作。

主对话只调 **一次** `github_ops`：

```
github_ops(task="完整任务", local_dir="/abs/path")
```

推本地源码时 **只传目录路径**，不要把文件正文塞进 task。子循环只能读这个目录，越界会拒。没有本地目录、只改几个小文件时，才把正文写进 task。

不要自己拆 action，也不要找 MCP 里的 `create_repository`。子循环看不到聊天记录，所以 **task 必须自包含**。

## 规则

1. 没要求公开就建 private 仓。
2. 严禁输出 token / PAT / Authorization。
3. 不要在无关仓库乱开 issue / 提 PR，除非用户明确指定。
4. 不要提交 `.env`、私钥、`credentials` 等敏感信息。
5. `delete` 默认是关的。未开启配置不要尝试删仓。
6. fork 的 owner 是源仓，目标写入当前登录账号；刚 fork 完可能要等几秒就绪。
7. 用户需要落点时可新建 private 仓并提交，然后只回 `html_url`。不要无意义地建空仓。
8. 有 `local_dir` 就让子循环用 `github_files` 的 `local_paths` 推，不要再抄正文。
