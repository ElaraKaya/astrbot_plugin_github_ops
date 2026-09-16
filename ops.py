"""Dispatch github_ops actions for the bot-owned GitHub account."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .github_client import GitHubClient, GitHubError, clip_text, file_blob_sha

try:
    from .redact import find_secret, redact_text
except ImportError:
    from redact import find_secret, redact_text
from .localfs import (
    as_path_list,
    collect_local_bundle,
    collect_local_files,
    is_blocked_path,
    list_local_entries,
    read_local_file,
)

ACTIONS = (
    "whoami",
    "list_repos",
    "get_repo",
    "create_repo",
    "delete_repo",
    "list_local",
    "get_local",
    "list_files",
    "get_file",
    "put_file",
    "push_files",
    "delete_file",
    "sync_files",
    "create_branch",
    "delete_branch",
    "list_commits",
    "list_issues",
    "get_issue",
    "create_issue",
    "comment_issue",
    "update_issue",
    "fork_repo",
    "create_pr",
    "list_prs",
    "merge_pr",
    "star_repo",
)


INNER_TEXT_LIMIT = 2000
OUTER_TEXT_LIMIT = 1500

WRITE_ACTIONS = frozenset(
    {
        "create_repo",
        "delete_repo",
        "put_file",
        "push_files",
        "delete_file",
        "sync_files",
        "create_issue",
        "comment_issue",
        "update_issue",
        "create_pr",
        "merge_pr",
        "create_branch",
        "delete_branch",
    }
)

PATH_CHECKED_ACTIONS = frozenset(
    {
        "list_files",
        "get_file",
        "put_file",
        "push_files",
        "delete_file",
        "sync_files",
        "list_local",
        "get_local",
    }
)

GROUP_ACTION_MAP = {
    "github_repo": {
        "list": "list_repos",
        "get": "get_repo",
        "create": "create_repo",
        "delete": "delete_repo",
        "commits": "list_commits",
    },
    "github_local": {
        "list": "list_local",
        "get": "get_local",
    },
    "github_files": {
        "list": "list_files",
        "get": "get_file",
        "put": "put_file",
        "push": "push_files",
        "delete": "delete_file",
        "del": "delete_file",
        "sync": "sync_files",
    },
    "github_issue": {
        "list": "list_issues",
        "get": "get_issue",
        "create": "create_issue",
        "comment": "comment_issue",
        "update": "update_issue",
    },
    "github_pr": {
        "list": "list_prs",
        "create": "create_pr",
        "merge": "merge_pr",
    },
    "github_misc": {
        "fork": "fork_repo",
        "star": "star_repo",
        "branch": "create_branch",
        "delete_branch": "delete_branch",
        "del_branch": "delete_branch",
    },
}

GROUP_HINTS = {
    "github_files": (
        "allowed: list, get, put, push, delete, sync。"
        "删文件用 action=delete 或 push/sync 时 files[].delete=true；"
        "不要省略 content、不要传 null、不要把文件写成空内容来模拟删除。"
        "delete 只删文件，不是删仓（删仓是 github_repo action=delete，且默认禁止）。"
        "有 local_dir 时优先 sync 或 local_paths，不要抄文件正文。"
        "遇到未知 action 直接汇报，不要换名字乱猜，也不要另开测试分支。"
    ),
    "github_repo": (
        "allowed: list, get, create, delete, commits。"
        "delete 会删整个仓库，默认关闭（allow_delete_repo）。"
    ),
    "github_local": "allowed: list, get。只读已授予的 local_dir；list 已按 .gitignore 过滤。",
    "github_issue": "allowed: list, get, create, comment, update。",
    "github_pr": "allowed: list, create, merge。",
    "github_misc": (
        "allowed: fork, star, branch, delete_branch。"
        "branch 是新建分支；delete_branch 删指定分支，不能删默认分支。"
        "用户没要求就不要另开测试分支；写默认分支即可。"
    ),
}

CANONICAL_GROUP_ACTIONS = {
    "github_repo": ["list", "get", "create", "delete", "commits"],
    "github_local": ["list", "get"],
    "github_files": ["list", "get", "put", "push", "delete", "sync"],
    "github_issue": ["list", "get", "create", "comment", "update"],
    "github_pr": ["list", "create", "merge"],
    "github_misc": ["fork", "star", "branch", "delete_branch"],
}


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def unknown_group_action(group: str, action: str) -> str:
    allowed = CANONICAL_GROUP_ACTIONS.get(group) or sorted(
        (GROUP_ACTION_MAP.get(group) or {}).keys()
    )
    return dumps(
        {
            "error": f"未知 {group} action: {action}",
            "allowed": allowed,
            "hint": GROUP_HINTS.get(group, "不要猜测 action，把错误原样汇报给用户。"),
        }
    )


def blocked_paths_in(kwargs: dict[str, Any]) -> list[str]:
    found: list[str] = []
    path = str(kwargs.get("path") or "").strip()
    if path and is_blocked_path(path):
        found.append(path)
    local_path = str(kwargs.get("local_path") or "").strip()
    if local_path and is_blocked_path(local_path):
        found.append(local_path)
    try:
        files = _as_files(
            kwargs.get("files") if kwargs.get("files") not in (None, "") else kwargs.get("files_json")
        )
    except GitHubError:
        files = []
    for item in files:
        p = item.get("path") or ""
        if p and is_blocked_path(p):
            found.append(p)
    try:
        for p in as_path_list(kwargs.get("local_paths") or kwargs.get("paths")):
            if p and is_blocked_path(p):
                found.append(p)
    except GitHubError:
        pass
    return found


def map_group_action(group: str, action: str) -> str | None:
    table = GROUP_ACTION_MAP.get(group) or {}
    return table.get((action or "").strip().lower())


def repo_write_allowed(
    *,
    login: str,
    owner: str,
    repo: str,
    allowed_repos: list[str],
) -> bool:
    owner = (owner or login).strip().strip("/")
    repo = (repo or "").strip().strip("/")
    if "/" in repo and not owner:
        owner, repo = repo.split("/", 1)
    owner_l = owner.lower()
    repo_l = repo.lower()
    login_l = (login or "").lower()
    allow = [str(x).strip() for x in allowed_repos if str(x).strip()]
    if not allow:
        return bool(owner_l) and owner_l == login_l
    full = f"{owner_l}/{repo_l}" if repo_l else owner_l
    names = {x.lower().strip("/") for x in allow}
    if owner_l in names or full in names:
        return True
    return login_l in names and owner_l == login_l


def _as_bool(value: Any, default: bool | None = None) -> bool | None:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _as_int(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_files(value: Any) -> list[dict[str, Any]]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise GitHubError(400, f"files_json 不是合法 JSON: {exc}") from exc
    if not isinstance(value, list):
        raise GitHubError(400, "files 必须是 [{path, content}|{path, delete:true}, ...] 数组")
    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        if _as_bool(item.get("delete"), False):
            out.append({"path": path, "delete": True})
            continue
        if "content" not in item or item.get("content") is None:
            raise GitHubError(
                400,
                f"{path}: 缺少 content。删文件请设 delete=true 或改用 action=delete，"
                "不要省略 content 或传 null（那会失败，也不会把文件删掉）",
            )
        encoding = str(item.get("encoding") or "utf-8")
        out.append(
            {
                "path": path,
                "content": str(item.get("content")),
                "encoding": encoding,
                "delete": False,
            }
        )
    return out


def _load_local_files(local_root: Path | None, kwargs: dict[str, Any]) -> list[dict[str, Any]]:
    paths = as_path_list(kwargs.get("local_paths") or kwargs.get("paths"))
    local_path = str(kwargs.get("local_path") or "").strip()
    if local_path:
        paths.append(local_path)
    if not paths:
        return []
    if local_root is None:
        raise GitHubError(400, "未授予 local_dir，不能读本地文件")
    return collect_local_files(local_root, paths)


async def dispatch(
    client: GitHubClient,
    action: str,
    kwargs: dict[str, Any],
    *,
    default_private: bool,
    allow_delete_repo: bool,
    allow_merge_pr: bool,
    local_root: Path | None = None,
) -> str:
    action = (action or "").strip()
    if action not in ACTIONS:
        return dumps(
            {
                "error": f"未知 action: {action}",
                "allowed": list(ACTIONS),
            }
        )

    owner = str(kwargs.get("owner") or "").strip()
    repo = str(kwargs.get("repo") or "").strip()
    path = str(kwargs.get("path") or "").strip()
    content = str(kwargs.get("content") or "")
    message = str(kwargs.get("message") or "").strip()
    title = str(kwargs.get("title") or "").strip()
    body = str(kwargs.get("body") or "")
    branch = str(kwargs.get("branch") or "").strip()
    description = str(kwargs.get("description") or "").strip()
    head = str(kwargs.get("head") or "").strip()
    base = str(kwargs.get("base") or "").strip()
    state = str(kwargs.get("state") or "").strip()
    query_name = str(kwargs.get("name") or "").strip()
    issue_number = _as_int(kwargs.get("issue_number"))
    if issue_number is None:
        issue_number = _as_int(kwargs.get("number"))
    per_page = _as_int(kwargs.get("per_page"), 20) or 20
    page = _as_int(kwargs.get("page"), 1) or 1
    private = _as_bool(kwargs.get("private"), default_private)
    files: list[dict[str, Any]] = []
    local_files: list[dict[str, Any]] = []
    needles = [getattr(client, "_profile_email", "")] if getattr(client, "_profile_email", "") else None
    tok = getattr(client, "_token", "") if getattr(client, "configured", False) else ""

    try:
        files = _as_files(
            kwargs.get("files") if kwargs.get("files") not in (None, "") else kwargs.get("files_json")
        )
        local_files = _load_local_files(local_root, kwargs)
        if local_files:
            files = local_files + files
        put_encoding = "utf-8"
        if action == "put_file" and local_files:
            item = local_files[0]
            path = path or item.get("path", "")
            content = item.get("content", "")
            put_encoding = str(item.get("encoding") or "utf-8")
        if action == "push_files" and not files:
            if local_root is None:
                raise GitHubError(400, "push 需要 files 或 local_paths")
            files = collect_local_files(local_root, [""])

        # 写入前敏感信息扫描

        for field_label, val in [("message", message), ("title", title), ("body", body), ("description", description)]:
            if val:
                sec_err = find_secret(val, token=tok, extra_needles=needles)
                if sec_err:
                    return dumps({"error": f"{field_label}: {sec_err}", "action": action})

        if action == "put_file" and put_encoding != "base64":
            sec_err = find_secret(content, token=tok, extra_needles=needles)
            if sec_err:
                return dumps({"error": f"文件 {path}: {sec_err}", "action": action})

        if action in ("push_files", "create_repo", "sync_files"):
            for f_item in files:
                if f_item.get("delete") or f_item.get("encoding") == "base64":
                    continue
                f_path = str(f_item.get("path") or "")
                f_content = str(f_item.get("content") or "")
                sec_err = find_secret(f_content, token=tok, extra_needles=needles)
                if sec_err:
                    return dumps({"error": f"文件 {f_path}: {sec_err}", "action": action})

        if action == "whoami":
            return dumps(await client.whoami())
        if action == "list_repos":
            return dumps(await client.list_repos(per_page=per_page, page=page))
        if action == "get_repo":
            return dumps(await client.get_repo(owner, repo))
        if action == "create_repo":
            name = query_name or repo
            created = await client.create_repo(
                name,
                description=description,
                private=bool(private),
                auto_init=False,
            )
            to_push = files if files else [{"path": "README.md", "content": f"# {name}\n"}]
            login = created.get("full_name") or ""
            c_owner, c_repo = login.split("/", 1) if "/" in login else (owner, name)
            pushed = await client.push_files(
                c_owner,
                c_repo,
                to_push,
                message or "init files",
            )
            created["pushed"] = pushed
            return dumps(created)
        if action == "delete_repo":
            if not allow_delete_repo:
                return dumps({"error": "已禁止删除仓库。管理员可在插件配置里打开 allow_delete_repo。"})
            return dumps(await client.delete_repo(owner, repo))
        if action == "list_local":
            if local_root is None:
                raise GitHubError(400, "未授予 local_dir")
            return dumps(list_local_entries(local_root, path))
        if action == "get_local":
            if local_root is None:
                raise GitHubError(400, "未授予 local_dir")
            return dumps(read_local_file(local_root, path, INNER_TEXT_LIMIT))
        if action == "list_files":
            return dumps(await client.list_files(owner, repo, path=path, ref=branch))
        if action == "get_file":
            data = await client.get_file(owner, repo, path, ref=branch)
            if isinstance(data.get("content"), str):
                data["content"] = clip_text(data["content"], INNER_TEXT_LIMIT)
            return dumps(data)
        if action == "put_file":
            return dumps(
                await client.put_file(
                    owner,
                    repo,
                    path,
                    content,
                    message or f"update {path}",
                    branch=branch,
                    encoding=put_encoding,
                )
            )
        if action == "push_files":
            return dumps(
                await client.push_files(
                    owner,
                    repo,
                    files,
                    message or "update files",
                    branch=branch,
                )
            )
        if action == "delete_file":
            return dumps(
                await client.delete_file(
                    owner,
                    repo,
                    path,
                    message or f"delete {path}",
                    branch=branch,
                )
            )
        if action == "sync_files":
            return dumps(
                await _sync_files(
                    client,
                    owner,
                    repo,
                    kwargs,
                    local_root=local_root,
                    message=message,
                    branch=branch,
                )
            )
        if action == "create_branch":
            return dumps(
                await client.create_branch(
                    owner,
                    repo,
                    branch,
                    from_branch=base or str(kwargs.get("from_branch") or ""),
                )
            )
        if action == "delete_branch":
            return dumps(await client.delete_branch(owner, repo, branch))
        if action == "list_commits":
            return dumps(await client.list_commits(owner, repo, branch=branch, per_page=per_page))
        if action == "list_issues":
            return dumps(await client.list_issues(owner, repo, state=state or "open", per_page=per_page))
        if action == "get_issue":
            if issue_number is None:
                raise GitHubError(400, "缺少 issue_number")
            return dumps(await client.get_issue(owner, repo, issue_number))
        if action == "create_issue":
            return dumps(await client.create_issue(owner, repo, title, body=body))
        if action == "comment_issue":
            if issue_number is None:
                raise GitHubError(400, "缺少 issue_number")
            return dumps(await client.comment_issue(owner, repo, issue_number, body))
        if action == "update_issue":
            if issue_number is None:
                raise GitHubError(400, "缺少 issue_number")
            return dumps(
                await client.update_issue(
                    owner,
                    repo,
                    issue_number,
                    title=title,
                    body=body,
                    state=state,
                )
            )
        if action == "fork_repo":
            return dumps(await client.fork_repo(owner, repo))
        if action == "create_pr":
            return dumps(
                await client.create_pr(
                    owner,
                    repo,
                    title,
                    head,
                    base=base,
                    body=body,
                )
            )
        if action == "list_prs":
            return dumps(await client.list_prs(owner, repo, state=state or "open", per_page=per_page))
        if action == "merge_pr":
            if not allow_merge_pr:
                return dumps({"error": "已禁止合并 PR。管理员可在插件配置里打开 allow_merge_pr。"})
            if issue_number is None:
                raise GitHubError(400, "缺少 issue_number（PR 编号）")
            method = str(kwargs.get("merge_method") or "squash")
            return dumps(await client.merge_pr(owner, repo, issue_number, merge_method=method))
        if action == "star_repo":
            return dumps(await client.star_repo(owner, repo))
    except GitHubError as exc:
        err_msg = redact_text(exc.message, token=tok, extra_needles=needles)
        return dumps({"error": err_msg, "status": exc.status, "action": action})
    except Exception as exc:
        err_msg = redact_text(f"{type(exc).__name__}: {exc}", token=tok, extra_needles=needles)
        return dumps({"error": err_msg, "action": action})

    return dumps({"error": "未处理的 action", "action": action})


async def _sync_files(
    client: GitHubClient,
    owner: str,
    repo: str,
    kwargs: dict[str, Any],
    *,
    local_root: Path | None,
    message: str,
    branch: str,
) -> dict[str, Any]:
    if local_root is None:
        raise GitHubError(400, "sync 需要 local_dir")
    paths = as_path_list(kwargs.get("local_paths") or kwargs.get("paths"))
    local_path = str(kwargs.get("local_path") or "").strip()
    if local_path:
        paths.append(local_path)
    whole_tree = (not paths) or all(normalize_empty(p) for p in paths)
    if not paths:
        paths = [""]
    files, skipped, present = collect_local_bundle(local_root, paths)
    delete_extra = _as_bool(kwargs.get("delete_extra"), True if whole_tree else False)
    remote = await client.list_tree_blobs(owner, repo, ref=branch)
    truncated = bool(remote.get("truncated"))
    if truncated and delete_extra:
        delete_extra = False
    remote_blobs = remote.get("blobs") or {}
    to_push: list[dict[str, Any]] = []
    unchanged: list[str] = []
    for item in files:
        remote_item = remote_blobs.get(item["path"])
        if remote_item and remote_item.get("sha") == file_blob_sha(item):
            unchanged.append(item["path"])
        else:
            to_push.append(item)
    if delete_extra:
        for rpath in remote_blobs:
            if rpath in present:
                continue
            if is_blocked_path(rpath):
                continue
            to_push.append({"path": rpath, "delete": True})
    if not to_push:
        return {
            "mode": "noop",
            "branch": remote.get("branch") or branch,
            "commit": None,
            "files": [],
            "updated": [],
            "deleted": [],
            "unchanged": unchanged,
            "skipped": skipped,
            "message": "没有需要提交的变更",
        }
    pushed = await client.push_files(
        owner,
        repo,
        to_push,
        message or "sync files",
        branch=branch or str(remote.get("branch") or ""),
    )
    pushed["unchanged"] = unchanged
    pushed["skipped"] = skipped
    if truncated:
        pushed["warning"] = "远端 tree 过大被截断，未执行 delete_extra"
    return pushed


def normalize_empty(path: str) -> bool:
    text = (path or "").replace("\\", "/").strip()
    return text in {"", ".", "./"}


def help_text() -> str:
    return (
        "主对话只调 github_ops(task=完整任务, local_dir=可选绝对路径)。"
        " 子循环工具: github_whoami, github_repo, github_local, github_files, "
        "github_issue, github_pr, github_misc。"
        " github_files: list/get/put/push/delete/sync。"
        " 删文件用 delete，与删仓无关。"
        " 删分支用 github_misc action=delete_branch，不能删默认分支。"
        " 有 local_dir 就 sync 或 local_paths，不要抄文件正文。"
        " 默认 owner 为当前登录的 GitHub 账号。严禁输出 token。"
    )
