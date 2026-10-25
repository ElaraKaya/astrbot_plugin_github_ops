---
name: github-ops
description: >
  Execute one GitHub task with the configured account. NEVER use this tool to
  read or directly modify remote repository contents; download the repo to the
  local workspace or use web tools instead, then push local changes via
  local_dir. The whole call times out at 120 seconds. Read this skill before
  the first GitHub action.
---

# GitHub Ops

通过配置的 GitHub 账号执行仓库管理与协作操作。

一次调用只做一件事。复杂任务先拆开，分多次调。整次调用 120 秒硬超时，超时会被取消。

```
github_ops(task="一件具体的事", max_steps=2, local_dir="/abs/path")
```

`max_steps` 必填，范围 1–30，会原样传给子循环。纯写入填 2–4。

分工：
- 绝对不要使用本工具读取或直接修改远端仓库内容；如果涉及查看或修改仓库内容，请先下载到本地工作空间处理，或者使用 web 工具查看。
- 本地修改完成后，传入 `local_dir` 让子循环将本地目录原样 `sync` 推送到远端。禁止让子循环“找到某逻辑再改”，否则会撞上 120 秒硬超时。
- 子循环没有浏览器、curl、git clone。
- 没传 `local_dir` 时为 `local_dir=none`，禁止 `github_local`、`sync`、`local_paths`。
- 有 `local_dir` 时子循环只读该目录并原样推送，不要把文件正文塞进 task。

不要自己拆底层 action，也不要找 MCP 里的 `create_repository`。子循环看不到聊天记录，**task 必须自包含**。

## 规则

1. 没要求公开就建 private 仓。白名单空时只能写当前登录账号自己的仓库。
2. 严禁输出 token / PAT / Authorization。
3. 不要在无关仓库乱开 issue / 提 PR，除非用户明确指定。
4. 不要提交 `.env`、私钥、`credentials` 等敏感信息。
5. `github_repo action=delete` 会删整个仓库，默认关。删仓库里的文件用 `github_files action=delete` 或 push/sync 的 `files[].delete=true`，与删仓无关。删分支用 `github_misc action=delete_branch`，禁止删默认分支。
6. fork 的 owner 是源仓，目标写入当前登录账号；刚 fork 完可能要等几秒就绪。
7. 用户需要落点时可新建 private 仓并提交，然后只回 `html_url`。不要无意义地建空仓。
8. 有 `local_dir` 时用 `github_files action=sync` 或 `local_paths` 推，不要抄正文。没传 `local_dir` 时禁止本地操作。
9. 子循环报错、未知 action、缺参数或结果不符立刻停，把错误如实汇报给用户，禁止改 action 名乱猜，禁止写空文件冒充删除。
