# 更新日志

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## 0.6.2 - 2026-10-12

### 变更

- 代码修改改由调用方完成。子循环只按 task 里的完整正文或 local_dir 原样提交，不再负责读懂后再改

## 0.6.1 - 2026-10-12

### 修复

- 没传 `local_dir` 时向子循环写明仓库在 GitHub 远端，禁止调用 `github_local`、`sync` 和 `local_paths`
- `github_files` 说明改为远端读写优先，避免子循环误去列本地目录

## 0.6.0 - 2026-10-12

### 变更

- 删除配置项 `max_steps`。子循环步数上限改由 `github_ops` 的 `max_steps` 参数传入，必填，范围 1–30
- 该上限会写进子循环提示，要求在步数内结束并汇报
- 收紧子循环：禁止重复读取，禁止浏览任务没点名的文件、PR、issue、commit
- 外层工具说明改为一次只做一件事，并注明整次调用 120 秒硬超时，复杂任务需拆分

### 不兼容

- 调用 `github_ops` 时必须传 `max_steps`，缺省或超出 1–30 会直接拒绝

## 0.5.1 - 2026-09-16

### 新增

- `github_misc action=delete_branch`：删除指定分支（`del_branch` 为别名）
- 删除前读取仓库 `default_branch`，禁止删默认分支（含 `refs/heads/main` 这类写法）
- 目标分支已不存在时返回 `already_absent`，不视为失败

## 0.5.0 - 2026-09-16

### 新增

- 删文件：`github_files action=delete`；`push` / `sync` 支持 `files[].delete=true`
- `github_files action=sync`：按本地目录与远端 blob sha 对齐；整目录默认 `delete_extra`
- 配置项 `max_steps`（默认 24，范围 4–60）
- 子循环预注入当前 GitHub 身份，一般不必再调 `github_whoami`
- 子循环运行规则：工具报错、未知 action、结果不符时停止试探并如实汇报

### 修复

- 空 content / `content: null` / 省略 content 不再被写成空文件
- 未知 `github_files` action 现在返回 `allowed` 与用法 hint（`del` 映射为 `delete`）

### 改进

- list / 推送默认尊重根目录 `.gitignore`（含否定规则）
- png 等常见资源按 base64 blob 推送；过大或可执行文件在结果里标 `skipped`
- push 返回值增加 `updated` / `deleted`
- `allow_delete_repo` 文案标明只控制删整个仓库，不影响删文件

## 0.4.2 - 2026-09-16

基线版本。主对话只挂 `github_ops`；子循环提供 whoami / repo / local / files / issue / pr / misc。
