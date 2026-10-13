---
name: github-ops
description: >
  Execute one GitHub task with the configured account. Call
  github_ops(task=one concrete job, max_steps=1-30, local_dir=absolute path
  when pushing local files). The whole call times out at 120 seconds, so split
  complex work into separate calls. Read this skill before the first GitHub action.
---

# GitHub Ops

通过配置的 GitHub 账号执行仓库管理与协作操作。

一次调用只做一件事。复杂任务拆开，分多次调。整次调用 120 秒硬超时，超时会被取消。

```
github_ops(task="一件具体的事", max_steps=6, local_dir="/abs/path")
```

`max_steps` 必填，范围 1–30，会原样传给子循环。查一件事填 2–4，改一个文件填 4–8。

改代码在调用前完成。子循环只提交，不负责读懂再改。小文件把完整新正文写进 task；文件多或已经在本地改好，传 local_dir，不要再抄正文。子循环只能读这个目录，越界会拒。

不要自己拆底层 action，也不要找 MCP 里的 `create_repository`。子循环看不到聊天记录，所以 **task 必须自包含**。

## 规则

1. 没要求公开就建 private 仓。
2. 严禁输出 token / PAT / Authorization。
3. 不要在无关仓库乱开 issue / 提 PR，除非用户明确指定。
4. 不要提交 `.env`、私钥、`credentials` 等敏感信息。
5. `github_repo action=delete` 会删整个仓库，默认关。未开启配置不要尝试删仓。删仓库里的文件用 `github_files` 的 delete / sync，与这项无关。删分支用 `github_misc action=delete_branch`，不能删默认分支。
6. fork 的 owner 是源仓，目标写入当前登录账号；刚 fork 完可能要等几秒就绪。
7. 用户需要落点时可新建 private 仓并提交，然后只回 `html_url`。不要无意义地建空仓。
8. 有 `local_dir` 就让子循环用 `github_files action=sync` 或 `local_paths` 推，不要再抄正文。
9. 子循环若报未知 action / 缺参数 / 结果不符：把错误原样告诉用户，不要自己换 action 名试探。
