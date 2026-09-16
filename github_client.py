"""Async GitHub REST client for the bot's own account.

Never log or return the token.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

import httpx

try:
    from .redact import find_secret
except ImportError:
    from redact import find_secret

API_VERSION = "2022-11-28"
DEFAULT_API_BASE = "https://api.github.com"
USER_AGENT = "astrbot-plugin-github-ops"
MAX_TEXT = 12000


class GitHubError(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(f"GitHub API {status}: {message}")


def clip_text(text: str, limit: int = MAX_TEXT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def git_blob_sha(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("utf-8")
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


def normalize_branch_name(branch: str) -> str:
    text = (branch or "").strip()
    if text.startswith("refs/heads/"):
        text = text[len("refs/heads/") :]
    elif text.startswith("heads/"):
        text = text[len("heads/") :]
    return text.strip("/")


def is_default_branch(branch: str, default_branch: str) -> bool:
    name = normalize_branch_name(branch)
    default = normalize_branch_name(default_branch)
    return bool(name) and bool(default) and name == default


def file_blob_sha(item: dict[str, Any]) -> str:
    encoding = str(item.get("encoding") or "utf-8")
    content = item.get("content") or ""
    if encoding == "base64":
        raw = base64.b64decode(content)
    else:
        raw = str(content).encode("utf-8")
    return git_blob_sha(raw)


def _brief_repo(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "full_name": item.get("full_name"),
        "private": item.get("private"),
        "html_url": item.get("html_url"),
        "description": item.get("description"),
        "default_branch": item.get("default_branch"),
        "pushed_at": item.get("pushed_at"),
        "stars": item.get("stargazers_count"),
        "fork": item.get("fork"),
        "language": item.get("language"),
    }


def _brief_issue(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": item.get("number"),
        "title": item.get("title"),
        "state": item.get("state"),
        "html_url": item.get("html_url"),
        "user": (item.get("user") or {}).get("login"),
        "comments": item.get("comments"),
        "updated_at": item.get("updated_at"),
    }


def _brief_pr(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": item.get("number"),
        "title": item.get("title"),
        "state": item.get("state"),
        "html_url": item.get("html_url"),
        "head": ((item.get("head") or {}).get("ref")),
        "base": ((item.get("base") or {}).get("ref")),
        "draft": item.get("draft"),
        "merged_at": item.get("merged_at"),
        "user": (item.get("user") or {}).get("login"),
    }


def redact_proxy(url: str) -> str:
    text = (url or "").strip()
    if not text:
        return ""
    scheme, sep, rest = text.partition("://")
    if not sep:
        return text
    if "@" in rest:
        rest = rest.rsplit("@", 1)[-1]
    return f"{scheme}://{rest}"


class GitHubClient:
    def __init__(
        self,
        token: str,
        api_base: str = DEFAULT_API_BASE,
        proxy: str = "",
        committer_name: str = "",
        committer_email: str = "",
    ) -> None:
        self._token = (token or "").strip()
        self._api_base = (api_base or DEFAULT_API_BASE).rstrip("/")
        self._proxy = (proxy or "").strip()
        self._committer_name = (committer_name or "").strip()
        self._committer_email = (committer_email or "").strip()
        self._login: str | None = None
        self._id: int | None = None
        self._profile_email: str = ""
        client_kwargs: dict[str, Any] = {
            "base_url": self._api_base,
            "timeout": httpx.Timeout(30.0, connect=10.0),
            "headers": {
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": USER_AGENT,
            },
            "trust_env": not bool(self._proxy),
        }
        if self._proxy:
            client_kwargs["proxy"] = self._proxy
        self._http = httpx.AsyncClient(**client_kwargs)

    def proxy_display(self) -> str:
        return redact_proxy(self._proxy)

    @property
    def configured(self) -> bool:
        return bool(self._token)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        if not self._token:
            raise GitHubError(401, "GitHub token 未配置")
        resp = await self._http.request(
            method,
            path,
            json=json_body,
            params=params,
        )
        if resp.status_code == 204:
            return None
        text = resp.text
        data: Any
        try:
            data = resp.json() if text else None
        except json.JSONDecodeError:
            data = {"raw": clip_text(text, 2000)}
        if resp.status_code >= 400:
            msg = ""
            if isinstance(data, dict):
                msg = str(data.get("message") or "")
                errors = data.get("errors")
                if errors:
                    msg = f"{msg} {errors}".strip()
            if not msg:
                msg = clip_text(text, 500) or f"HTTP {resp.status_code}"
            if "token" in msg.lower() and "bad credentials" not in msg.lower():
                msg = f"HTTP {resp.status_code}"
            raise GitHubError(resp.status_code, msg)
        return data

    async def get(self, path: str, **params: Any) -> Any:
        clean = {k: v for k, v in params.items() if v is not None and v != ""}
        return await self._request("GET", path, params=clean or None)

    async def post(self, path: str, body: dict[str, Any]) -> Any:
        return await self._request("POST", path, json_body=body)

    async def patch(self, path: str, body: dict[str, Any]) -> Any:
        return await self._request("PATCH", path, json_body=body)

    async def put(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return await self._request("PUT", path, json_body=body)

    async def delete(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return await self._request("DELETE", path, json_body=body)

    async def whoami(self) -> dict[str, Any]:
        me = await self.get("/user")
        self._login = me.get("login")
        self._id = me.get("id")
        raw_email = (me.get("email") or "").strip()
        if raw_email and "users.noreply.github.com" not in raw_email:
            self._profile_email = raw_email
        else:
            self._profile_email = ""
        rate = await self.get("/rate_limit")
        core = (rate.get("resources") or {}).get("core") or {}
        return {
            "login": me.get("login"),
            "name": me.get("name"),
            "html_url": me.get("html_url"),
            "public_repos": me.get("public_repos"),
            "total_private_repos": me.get("total_private_repos"),
            "type": me.get("type"),
            "rate_limit": {
                "remaining": core.get("remaining"),
                "limit": core.get("limit"),
                "reset": core.get("reset"),
            },
        }


    async def _ensure_identity(self) -> None:
        if not self._login or self._id is None:
            await self.whoami()

    def get_commit_author(self) -> dict[str, str]:
        login = (self._login or "").strip() or "unknown"
        name = self._committer_name or login
        if self._committer_email:
            email = self._committer_email
        elif self._id:
            email = f"{self._id}+{login}@users.noreply.github.com"
        else:
            email = f"{login}@users.noreply.github.com"
        return {"name": name, "email": email}

    async def login(self) -> str:
        if self._login:
            return self._login
        info = await self.whoami()
        login = str(info.get("login") or "")
        if not login:
            raise GitHubError(500, "无法读取当前 GitHub 登录名")
        return login

    async def resolve_repo(self, owner: str | None, repo: str) -> tuple[str, str]:
        repo = (repo or "").strip().strip("/")
        owner = (owner or "").strip().strip("/")
        if "/" in repo and not owner:
            owner, repo = repo.split("/", 1)
        if not repo:
            raise GitHubError(400, "缺少 repo")
        if not owner:
            owner = await self.login()
        return owner, repo

    async def list_repos(self, per_page: int = 20, page: int = 1) -> dict[str, Any]:
        items = await self.get(
            "/user/repos",
            per_page=min(max(per_page, 1), 50),
            page=max(page, 1),
            sort="updated",
            affiliation="owner,collaborator",
        )
        return {
            "page": page,
            "count": len(items) if isinstance(items, list) else 0,
            "repos": [_brief_repo(x) for x in items] if isinstance(items, list) else [],
        }

    async def get_repo(self, owner: str, repo: str) -> dict[str, Any]:
        owner, repo = await self.resolve_repo(owner, repo)
        item = await self.get(f"/repos/{owner}/{repo}")
        brief = _brief_repo(item)
        brief["topics"] = item.get("topics") or []
        brief["open_issues"] = item.get("open_issues_count")
        return brief

    async def create_repo(
        self,
        name: str,
        *,
        description: str = "",
        private: bool = True,
        auto_init: bool = False,
    ) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise GitHubError(400, "缺少仓库名")
        body: dict[str, Any] = {
            "name": name,
            "private": bool(private),
            "auto_init": bool(auto_init),
        }
        if description:
            body["description"] = description
        item = await self.post("/user/repos", body)
        return _brief_repo(item)

    async def delete_repo(self, owner: str, repo: str) -> dict[str, Any]:
        owner, repo = await self.resolve_repo(owner, repo)
        await self.delete(f"/repos/{owner}/{repo}")
        return {"deleted": True, "full_name": f"{owner}/{repo}"}

    async def list_files(
        self,
        owner: str,
        repo: str,
        path: str = "",
        ref: str = "",
    ) -> dict[str, Any]:
        owner, repo = await self.resolve_repo(owner, repo)
        api_path = f"/repos/{owner}/{repo}/contents/{path.lstrip('/')}" if path else f"/repos/{owner}/{repo}/contents"
        params: dict[str, Any] = {}
        if ref:
            params["ref"] = ref
        data = await self.get(api_path, **params)
        if isinstance(data, list):
            return {
                "type": "dir",
                "path": path or "/",
                "entries": [
                    {
                        "name": x.get("name"),
                        "path": x.get("path"),
                        "type": x.get("type"),
                        "size": x.get("size"),
                    }
                    for x in data
                ],
            }
        return await self._decode_file(data)

    async def get_file(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str = "",
    ) -> dict[str, Any]:
        owner, repo = await self.resolve_repo(owner, repo)
        if not path:
            raise GitHubError(400, "缺少 path")
        params: dict[str, Any] = {}
        if ref:
            params["ref"] = ref
        data = await self.get(f"/repos/{owner}/{repo}/contents/{path.lstrip('/')}", **params)
        if isinstance(data, list):
            return {
                "type": "dir",
                "path": path,
                "entries": [
                    {"name": x.get("name"), "path": x.get("path"), "type": x.get("type")}
                    for x in data
                ],
            }
        return await self._decode_file(data)

    async def _decode_file(self, data: dict[str, Any]) -> dict[str, Any]:
        encoding = data.get("encoding")
        raw = data.get("content") or ""
        text = ""
        if encoding == "base64" and isinstance(raw, str):
            try:
                decoded = base64.b64decode(raw)
                text = decoded.decode("utf-8")
            except UnicodeDecodeError:
                return {
                    "type": "file",
                    "path": data.get("path"),
                    "sha": data.get("sha"),
                    "size": data.get("size"),
                    "html_url": data.get("html_url"),
                    "binary": True,
                }
        return {
            "type": "file",
            "path": data.get("path"),
            "sha": data.get("sha"),
            "size": data.get("size"),
            "html_url": data.get("html_url"),
            "content": clip_text(text, 2000),
        }

    async def put_file(
        self,
        owner: str,
        repo: str,
        path: str,
        content: str,
        message: str,
        branch: str = "",
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        owner, repo = await self.resolve_repo(owner, repo)
        if not path:
            raise GitHubError(400, "缺少 path")
        sha = None
        try:
            existing = await self.get(
                f"/repos/{owner}/{repo}/contents/{path.lstrip('/')}",
                ref=branch or None,
            )
            if isinstance(existing, dict):
                sha = existing.get("sha")
        except GitHubError as exc:
            if exc.status != 404:
                raise
        needles = [self._profile_email] if self._profile_email else None
        if (encoding or "utf-8") != "base64":
            secret_err = find_secret(content, token=self._token, extra_needles=needles)
            if secret_err:
                raise GitHubError(400, f"拒绝写入文件 {path}: {secret_err}")
        secret_err_msg = find_secret(message, token=self._token, extra_needles=needles)
        if secret_err_msg:
            raise GitHubError(400, f"拒绝提交信息: {secret_err_msg}")
        if (encoding or "utf-8") == "base64":
            b64 = str(content or "")
        else:
            b64 = base64.b64encode((content or "").encode("utf-8")).decode("ascii")
        await self._ensure_identity()
        author = self.get_commit_author()
        body: dict[str, Any] = {
            "message": message or f"update {path}",
            "content": b64,
            "author": author,
            "committer": author,
        }
        if branch:
            body["branch"] = branch
        if sha:
            body["sha"] = sha
        result = await self.put(f"/repos/{owner}/{repo}/contents/{path.lstrip('/')}", body)
        commit = result.get("commit") or {}
        return {
            "path": path,
            "html_url": (result.get("content") or {}).get("html_url"),
            "commit": commit.get("sha"),
            "commit_url": commit.get("html_url"),
            "updated": [path],
            "deleted": [],
        }

    async def delete_file(
        self,
        owner: str,
        repo: str,
        path: str,
        message: str,
        branch: str = "",
    ) -> dict[str, Any]:
        owner, repo = await self.resolve_repo(owner, repo)
        path = (path or "").lstrip("/")
        if not path:
            raise GitHubError(400, "缺少 path")
        params: dict[str, Any] = {}
        if branch:
            params["ref"] = branch
        try:
            existing = await self.get(
                f"/repos/{owner}/{repo}/contents/{path}",
                **params,
            )
        except GitHubError as exc:
            if exc.status == 404:
                return {
                    "deleted": False,
                    "already_absent": True,
                    "path": path,
                    "updated": [],
                    "deleted": [],
                }
            raise
        if isinstance(existing, list):
            raise GitHubError(400, f"{path} 是目录，请对具体文件使用 delete")
        sha = existing.get("sha")
        if not sha:
            raise GitHubError(400, f"无法读取 {path} 的 sha")
        needles = [self._profile_email] if self._profile_email else None
        secret_err_msg = find_secret(message, token=self._token, extra_needles=needles)
        if secret_err_msg:
            raise GitHubError(400, f"拒绝提交信息: {secret_err_msg}")
        await self._ensure_identity()
        author = self.get_commit_author()
        body: dict[str, Any] = {
            "message": message or f"delete {path}",
            "sha": sha,
            "author": author,
            "committer": author,
        }
        if branch:
            body["branch"] = branch
        result = await self.delete(f"/repos/{owner}/{repo}/contents/{path}", body)
        commit = (result or {}).get("commit") or {}
        return {
            "deleted": True,
            "path": path,
            "commit": commit.get("sha"),
            "commit_url": commit.get("html_url"),
            "html_url": commit.get("html_url"),
            "updated": [],
            "deleted": [path],
        }

    async def list_tree_blobs(
        self,
        owner: str,
        repo: str,
        ref: str = "",
    ) -> dict[str, Any]:
        owner, repo = await self.resolve_repo(owner, repo)
        repo_info = await self.get(f"/repos/{owner}/{repo}")
        ref_name = ref or repo_info.get("default_branch") or "main"
        ref_data = await self.get(f"/repos/{owner}/{repo}/git/ref/heads/{ref_name}")
        commit_sha = (ref_data.get("object") or {}).get("sha")
        if not commit_sha:
            raise GitHubError(404, f"分支 {ref_name} 不存在")
        commit = await self.get(f"/repos/{owner}/{repo}/git/commits/{commit_sha}")
        tree_sha = (commit.get("tree") or {}).get("sha")
        tree = await self.get(
            f"/repos/{owner}/{repo}/git/trees/{tree_sha}",
            recursive="1",
        )
        blobs: dict[str, dict[str, Any]] = {}
        for item in tree.get("tree") or []:
            if item.get("type") != "blob":
                continue
            p = str(item.get("path") or "")
            if not p:
                continue
            blobs[p] = {
                "sha": item.get("sha"),
                "size": item.get("size"),
                "mode": item.get("mode"),
            }
        return {
            "branch": ref_name,
            "truncated": bool(tree.get("truncated")),
            "count": len(blobs),
            "blobs": blobs,
        }

    async def push_files(
        self,
        owner: str,
        repo: str,
        files: list[dict[str, Any]],
        message: str,
        branch: str = "",
    ) -> dict[str, Any]:
        owner, repo = await self.resolve_repo(owner, repo)
        if not files:
            raise GitHubError(400, "files 为空")
        cleaned: list[dict[str, Any]] = []
        for item in files:
            path = str(item.get("path") or "").lstrip("/")
            if not path:
                continue
            if item.get("delete"):
                cleaned.append({"path": path, "delete": True})
                continue
            encoding = str(item.get("encoding") or "utf-8")
            if "content" not in item or item.get("content") is None:
                raise GitHubError(
                    400,
                    f"{path}: 缺少 content。删文件请设 delete=true，不要省略 content 或传 null",
                )
            cleaned.append(
                {
                    "path": path,
                    "content": str(item.get("content")),
                    "encoding": encoding,
                    "delete": False,
                }
            )
        if not cleaned:
            raise GitHubError(400, "files 里没有有效 path")

        needles = [self._profile_email] if self._profile_email else None
        for item in cleaned:
            if item.get("delete") or item.get("encoding") == "base64":
                continue
            secret_err = find_secret(item["content"], token=self._token, extra_needles=needles)
            if secret_err:
                raise GitHubError(400, f"拒绝写入文件 {item['path']}: {secret_err}")
        secret_err_msg = find_secret(message, token=self._token, extra_needles=needles)
        if secret_err_msg:
            raise GitHubError(400, f"拒绝提交信息: {secret_err_msg}")

        repo_info = await self.get(f"/repos/{owner}/{repo}")
        branch = branch or repo_info.get("default_branch") or "main"
        try:
            return await self._push_via_git_data(owner, repo, cleaned, message, branch)
        except GitHubError as exc:
            if exc.status != 404:
                raise
            last: dict[str, Any] | None = None
            updated: list[str] = []
            deleted: list[str] = []
            for item in cleaned:
                if item.get("delete"):
                    last = await self.delete_file(
                        owner,
                        repo,
                        item["path"],
                        message or f"delete {item['path']}",
                        branch=branch,
                    )
                    deleted.extend(last.get("deleted") or [])
                else:
                    last = await self.put_file(
                        owner,
                        repo,
                        item["path"],
                        item["content"],
                        message or f"update {item['path']}",
                        branch=branch,
                        encoding=str(item.get("encoding") or "utf-8"),
                    )
                    updated.append(item["path"])
            return {
                "mode": "contents-api-fallback",
                "branch": branch,
                "files": [x["path"] for x in cleaned],
                "updated": updated,
                "deleted": deleted,
                "last": last,
            }

    async def _push_via_git_data(
        self,
        owner: str,
        repo: str,
        files: list[dict[str, Any]],
        message: str,
        branch: str,
    ) -> dict[str, Any]:
        ref = await self.get(f"/repos/{owner}/{repo}/git/ref/heads/{branch}")
        base_sha = (ref.get("object") or {}).get("sha")
        if not base_sha:
            raise GitHubError(404, f"分支 {branch} 不存在")
        commit = await self.get(f"/repos/{owner}/{repo}/git/commits/{base_sha}")
        base_tree = (commit.get("tree") or {}).get("sha")
        tree_items: list[dict[str, Any]] = []
        for item in files:
            if item.get("delete"):
                tree_items.append(
                    {
                        "path": item["path"],
                        "mode": "100644",
                        "type": "blob",
                        "sha": None,
                    }
                )
                continue
            encoding = str(item.get("encoding") or "utf-8")
            blob = await self.post(
                f"/repos/{owner}/{repo}/git/blobs",
                {"content": item["content"], "encoding": encoding},
            )
            tree_items.append(
                {
                    "path": item["path"],
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob.get("sha"),
                }
            )
        tree = await self.post(
            f"/repos/{owner}/{repo}/git/trees",
            {"base_tree": base_tree, "tree": tree_items},
        )
        await self._ensure_identity()
        author = self.get_commit_author()
        new_commit = await self.post(
            f"/repos/{owner}/{repo}/git/commits",
            {
                "message": message or "update files",
                "tree": tree.get("sha"),
                "parents": [base_sha],
                "author": author,
                "committer": author,
            },
        )
        await self.patch(
            f"/repos/{owner}/{repo}/git/refs/heads/{branch}",
            {"sha": new_commit.get("sha")},
        )
        updated = [x["path"] for x in files if not x.get("delete")]
        deleted = [x["path"] for x in files if x.get("delete")]
        return {
            "mode": "git-data",
            "branch": branch,
            "commit": new_commit.get("sha"),
            "html_url": new_commit.get("html_url"),
            "files": [x["path"] for x in files],
            "updated": updated,
            "deleted": deleted,
        }

    async def create_branch(
        self,
        owner: str,
        repo: str,
        branch: str,
        from_branch: str = "",
    ) -> dict[str, Any]:
        owner, repo = await self.resolve_repo(owner, repo)
        if not branch:
            raise GitHubError(400, "缺少 branch")
        repo_info = await self.get(f"/repos/{owner}/{repo}")
        from_branch = from_branch or repo_info.get("default_branch") or "main"
        ref = await self.get(f"/repos/{owner}/{repo}/git/ref/heads/{from_branch}")
        sha = (ref.get("object") or {}).get("sha")
        created = await self.post(
            f"/repos/{owner}/{repo}/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": sha},
        )
        return {
            "branch": branch,
            "from": from_branch,
            "sha": (created.get("object") or {}).get("sha"),
        }

    async def delete_branch(
        self,
        owner: str,
        repo: str,
        branch: str,
    ) -> dict[str, Any]:
        owner, repo = await self.resolve_repo(owner, repo)
        branch = normalize_branch_name(branch)
        if not branch:
            raise GitHubError(400, "缺少 branch")
        repo_info = await self.get(f"/repos/{owner}/{repo}")
        default_branch = normalize_branch_name(
            str(repo_info.get("default_branch") or "main")
        )
        if is_default_branch(branch, default_branch):
            raise GitHubError(403, f"禁止删除默认分支 {default_branch}")
        try:
            await self.delete(f"/repos/{owner}/{repo}/git/refs/heads/{branch}")
        except GitHubError as exc:
            if exc.status == 404:
                return {
                    "deleted": False,
                    "already_absent": True,
                    "branch": branch,
                    "default_branch": default_branch,
                    "full_name": f"{owner}/{repo}",
                }
            raise
        return {
            "deleted": True,
            "branch": branch,
            "default_branch": default_branch,
            "full_name": f"{owner}/{repo}",
        }

    async def list_commits(
        self,
        owner: str,
        repo: str,
        branch: str = "",
        per_page: int = 10,
    ) -> dict[str, Any]:
        owner, repo = await self.resolve_repo(owner, repo)
        items = await self.get(
            f"/repos/{owner}/{repo}/commits",
            sha=branch or None,
            per_page=min(max(per_page, 1), 30),
        )
        commits = []
        if isinstance(items, list):
            for item in items:
                c = item.get("commit") or {}
                commits.append(
                    {
                        "sha": (item.get("sha") or "")[:12],
                        "message": (c.get("message") or "").split("\n", 1)[0],
                        "author": ((c.get("author") or {}).get("name")),
                        "date": ((c.get("author") or {}).get("date")),
                        "html_url": item.get("html_url"),
                    }
                )
        return {"count": len(commits), "commits": commits}

    async def list_issues(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        per_page: int = 20,
    ) -> dict[str, Any]:
        owner, repo = await self.resolve_repo(owner, repo)
        items = await self.get(
            f"/repos/{owner}/{repo}/issues",
            state=state or "open",
            per_page=min(max(per_page, 1), 50),
        )
        issues = []
        if isinstance(items, list):
            for item in items:
                if item.get("pull_request"):
                    continue
                issues.append(_brief_issue(item))
        return {"count": len(issues), "issues": issues}

    async def get_issue(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        owner, repo = await self.resolve_repo(owner, repo)
        item = await self.get(f"/repos/{owner}/{repo}/issues/{int(number)}")
        brief = _brief_issue(item)
        brief["body"] = clip_text(str(item.get("body") or ""), 2000)
        return brief

    async def create_issue(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str = "",
    ) -> dict[str, Any]:
        owner, repo = await self.resolve_repo(owner, repo)
        if not title:
            raise GitHubError(400, "缺少 title")
        payload: dict[str, Any] = {"title": title}
        if body:
            payload["body"] = body
        item = await self.post(f"/repos/{owner}/{repo}/issues", payload)
        return _brief_issue(item)

    async def comment_issue(
        self,
        owner: str,
        repo: str,
        number: int,
        body: str,
    ) -> dict[str, Any]:
        owner, repo = await self.resolve_repo(owner, repo)
        if not body:
            raise GitHubError(400, "缺少 body")
        item = await self.post(
            f"/repos/{owner}/{repo}/issues/{int(number)}/comments",
            {"body": body},
        )
        return {
            "id": item.get("id"),
            "html_url": item.get("html_url"),
        }

    async def update_issue(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        title: str = "",
        body: str = "",
        state: str = "",
    ) -> dict[str, Any]:
        owner, repo = await self.resolve_repo(owner, repo)
        payload: dict[str, Any] = {}
        if title:
            payload["title"] = title
        if body:
            payload["body"] = body
        if state:
            payload["state"] = state
        if not payload:
            raise GitHubError(400, "没有可更新的字段")
        item = await self.patch(f"/repos/{owner}/{repo}/issues/{int(number)}", payload)
        return _brief_issue(item)

    async def fork_repo(self, owner: str, repo: str) -> dict[str, Any]:
        owner, repo = await self.resolve_repo(owner, repo)
        item = await self.post(f"/repos/{owner}/{repo}/forks", {})
        brief = _brief_repo(item)
        brief["note"] = "fork 是异步的，刚返回时仓库可能还不能立刻 push"
        return brief

    async def create_pr(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str = "",
        body: str = "",
    ) -> dict[str, Any]:
        owner, repo = await self.resolve_repo(owner, repo)
        if not title or not head:
            raise GitHubError(400, "缺少 title 或 head")
        if not base:
            repo_info = await self.get(f"/repos/{owner}/{repo}")
            base = repo_info.get("default_branch") or "main"
        payload: dict[str, Any] = {"title": title, "head": head, "base": base}
        if body:
            payload["body"] = body
        item = await self.post(f"/repos/{owner}/{repo}/pulls", payload)
        return _brief_pr(item)

    async def list_prs(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        per_page: int = 20,
    ) -> dict[str, Any]:
        owner, repo = await self.resolve_repo(owner, repo)
        items = await self.get(
            f"/repos/{owner}/{repo}/pulls",
            state=state or "open",
            per_page=min(max(per_page, 1), 50),
        )
        prs = [_brief_pr(x) for x in items] if isinstance(items, list) else []
        return {"count": len(prs), "pulls": prs}

    async def merge_pr(
        self,
        owner: str,
        repo: str,
        number: int,
        merge_method: str = "squash",
    ) -> dict[str, Any]:
        owner, repo = await self.resolve_repo(owner, repo)
        item = await self.put(
            f"/repos/{owner}/{repo}/pulls/{int(number)}/merge",
            {"merge_method": merge_method or "squash"},
        )
        return {
            "merged": item.get("merged"),
            "message": item.get("message"),
            "sha": item.get("sha"),
        }

    async def star_repo(self, owner: str, repo: str) -> dict[str, Any]:
        owner, repo = await self.resolve_repo(owner, repo)
        await self.put(f"/user/starred/{owner}/{repo}")
        return {"starred": True, "full_name": f"{owner}/{repo}"}
