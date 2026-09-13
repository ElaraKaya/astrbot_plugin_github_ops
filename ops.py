"""Dispatch github_ops actions for the bot-owned GitHub account."""

from __future__ import annotations

import json
from typing import Any

from .github_client import GitHubClient, GitHubError, clip_text

ACTIONS = (
    "whoami",
    "list_repos",
    "get_repo",
    "create_repo",
    "delete_repo",
    "list_files",
    "get_file",
    "put_file",
    "push_files",
    "create_branch",
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
        "create_issue",
        "comment_issue",
        "update_issue",
        "create_pr",
        "merge_pr",
        "create_branch",
    }
)

PATH_CHECKED_ACTIONS = frozenset({"list_files", "get_file", "put_file", "push_files"})

GROUP_ACTION_MAP = {
    "github_repo": {
        "list": "list_repos",
        "get": "get_repo",
        "create": "create_repo",
        "delete": "delete_repo",
        "commits": "list_commits",
    },
    "github_files": {
        "list": "list_files",
        "get": "get_file",
        "put": "put_file",
        "push": "push_files",
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
    },
}

_BLOCKED_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    "id_rsa",
    "id_ecdsa",
    "id_ed25519",
    "id_dsa",
    ".netrc",
    ".git-credentials",
    "credentials",
    "credentials.json",
    "authorized_keys",
}
_BLOCKED_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".kdbx")
_ENV_ALLOW = {".env.example", ".env.sample"}


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def is_blocked_path(path: str) -> bool:
    norm = (path or "").replace("\\", "/").strip()
    while norm.startswith("./"):
        norm = norm[2:]
    norm = norm.lstrip("/")
    lower = norm.lower()
    if ".git/config" in lower:
        return True
    name = lower.rsplit("/", 1)[-1]
    if name in _ENV_ALLOW:
        return False
    if name in _BLOCKED_NAMES:
        return True
    if name == ".env" or name.startswith(".env."):
        return True
    return any(name.endswith(suf) for suf in _BLOCKED_SUFFIXES)


def blocked_paths_in(kwargs: dict[str, Any]) -> list[str]:
    found: list[str] = []
    path = str(kwargs.get("path") or "").strip()
    if path and is_blocked_path(path):
        found.append(path)
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


def _as_files(value: Any) -> list[dict[str, str]]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise GitHubError(400, f"files_json 不是合法 JSON: {exc}") from exc
    if not isinstance(value, list):
        raise GitHubError(400, "files 必须是 [{path, content}, ...] 数组")
    out: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        out.append({"path": path, "content": str(item.get("content") or "")})
    return out


async def dispatch(
    client: GitHubClient,
    action: str,
    kwargs: dict[str, Any],
    *,
    default_private: bool,
    allow_delete_repo: bool,
    allow_merge_pr: bool,
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
    files = _as_files(kwargs.get("files") if kwargs.get("files") not in (None, "") else kwargs.get("files_json"))

    try:
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
                auto_init=True,
            )
            if files:
                login = created.get("full_name") or ""
                c_owner, c_repo = login.split("/", 1) if "/" in login else (owner, name)
                pushed = await client.push_files(
                    c_owner,
                    c_repo,
                    files,
                    message or "init files",
                )
                created["pushed"] = pushed
            return dumps(created)
        if action == "delete_repo":
            if not allow_delete_repo:
                return dumps({"error": "已禁止删除仓库。管理员可在插件配置里打开 allow_delete_repo。"})
            return dumps(await client.delete_repo(owner, repo))
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
        if action == "create_branch":
            return dumps(
                await client.create_branch(
                    owner,
                    repo,
                    branch,
                    from_branch=base or str(kwargs.get("from_branch") or ""),
                )
            )
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
        return dumps({"error": exc.message, "status": exc.status, "action": action})
    except Exception as exc:
        return dumps({"error": f"{type(exc).__name__}: {exc}", "action": action})

    return dumps({"error": "未处理的 action", "action": action})


def help_text() -> str:
    return (
        "主对话只调 github_ops(task=完整任务)。"
        " 子循环工具: github_whoami, github_repo, github_files, "
        "github_issue, github_pr, github_misc。"
        " 默认 owner 是 bot 自己的 GitHub 登录名。不要输出 token。"
    )
