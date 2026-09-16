"""Sandbox local file access for the inner GitHub loop.

Inner tools may only touch files under the granted local_dir.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

from .github_client import GitHubError, clip_text

SKIP_DIRS = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        ".pytest_cache",
        "node_modules",
        ".mypy_cache",
        ".ruff_cache",
        ".idea",
        ".vscode",
    }
)
SKIP_FILE_NAMES = frozenset({".ds_store", "thumbs.db"})
SKIP_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".pyd",
    ".so",
    ".dylib",
    ".dll",
    ".bin",
    ".exe",
    ".zip",
    ".gz",
    ".tar",
    ".whl",
    ".7z",
    ".rar",
)
BINARY_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".eot",
)
MAX_FILE_BYTES = 512 * 1024
MAX_PUSH_FILES = 80
MAX_LIST = 200

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


def is_binary_path(path: str) -> bool:
    lower = (path or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    return any(lower.endswith(suf) for suf in BINARY_SUFFIXES)


def normalize_rel(path: str) -> str:
    text = (path or "").replace("\\", "/").strip()
    if not text or text == ".":
        return ""
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


def parse_local_root(raw: str) -> Path:
    text = (raw or "").strip()
    if not text:
        raise GitHubError(400, "未授予 local_dir")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise GitHubError(400, "local_dir 必须是绝对路径")
    if not path.exists() or not path.is_dir():
        raise GitHubError(400, "local_dir 不是目录")
    return path.resolve()


def resolve_under_root(root: Path, rel: str) -> Path:
    root = root.resolve()
    rel_n = normalize_rel(rel)
    if rel_n.startswith("~") or (len(rel_n) >= 2 and rel_n[1] == ":"):
        raise GitHubError(400, "只允许相对路径")
    target = root if not rel_n else (root / rel_n).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise GitHubError(403, "路径越界") from exc
    return target


def rel_posix(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


class GitIgnore:
    """Root-level .gitignore matcher (negation, *, **, character class)."""

    def __init__(self, rules: list[tuple[bool, Any]]):
        self._rules = rules

    @classmethod
    def from_root(cls, root: Path) -> GitIgnore:
        path = root / ".gitignore"
        rules: list[tuple[bool, Any]] = []
        if not path.is_file():
            return cls(rules)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return cls(rules)
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            negated = line.startswith("!")
            if negated:
                line = line[1:]
            compiled = _compile_gitignore_line(line)
            if compiled is not None:
                rules.append((negated, compiled))
        return cls(rules)

    def ignored(self, rel: str) -> bool:
        rel_n = normalize_rel(rel)
        if not rel_n:
            return False
        ignored = False
        for negated, matcher in self._rules:
            if matcher(rel_n):
                ignored = not negated
        return ignored


def _compile_gitignore_line(pattern: str):
    pattern = pattern.strip()
    if not pattern:
        return None
    dir_only = pattern.endswith("/")
    if dir_only:
        pattern = pattern[:-1]
    anchored = "/" in pattern[:-1] if pattern else False
    if pattern.startswith("/"):
        anchored = True
        pattern = pattern[1:]
    regex = _gitignore_glob_to_regex(pattern)
    if anchored:
        body = rf"^{regex}"
    else:
        body = rf"(?:^|/){regex}"
    if dir_only:
        full = body + r"(?:/|$)"
    else:
        full = body + r"(?:$|/)"
    try:
        cre = re.compile(full)
    except re.error:
        return None

    def matcher(rel: str, _cre=cre) -> bool:
        return _cre.search(rel) is not None

    return matcher


def _gitignore_glob_to_regex(pattern: str) -> str:
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "*" and i + 1 < n and pattern[i + 1] == "*":
            if i + 2 < n and pattern[i + 2] == "/":
                out.append("(?:.*/)?")
                i += 3
                continue
            out.append(".*")
            i += 2
            continue
        if ch == "*":
            out.append("[^/]*")
            i += 1
            continue
        if ch == "?":
            out.append("[^/]")
            i += 1
            continue
        if ch == "[":
            j = i + 1
            if j < n and pattern[j] in {"!", "^"}:
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j >= n:
                out.append(re.escape(ch))
                i += 1
                continue
            klass = pattern[i : j + 1]
            if klass.startswith("[!") or klass.startswith("[^"):
                inner = klass[2:-1]
                out.append("[^" + inner.replace("\\", "\\\\") + "]")
            else:
                out.append(klass)
            i = j + 1
            continue
        out.append(re.escape(ch))
        i += 1
    return "".join(out)


def skip_reason(path: Path, root: Path, gitignore: GitIgnore | None = None) -> str | None:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return "escape"
    rel_s = rel.as_posix()
    if any(part in SKIP_DIRS for part in rel.parts):
        return "skip-dir"
    name = path.name.lower()
    if name in SKIP_FILE_NAMES:
        return "skip-name"
    if name.endswith(SKIP_SUFFIXES):
        return "skip-suffix"
    if is_blocked_path(rel_s):
        return "blocked"
    if gitignore is not None and gitignore.ignored(rel_s):
        return "gitignore"
    return None


def should_skip(path: Path, root: Path, gitignore: GitIgnore | None = None) -> bool:
    return skip_reason(path, root, gitignore) is not None


def load_gitignore(root: Path) -> GitIgnore:
    return GitIgnore.from_root(root)


def list_local_entries(root: Path, rel: str = "") -> dict[str, Any]:
    target = resolve_under_root(root, rel)
    gitignore = load_gitignore(root)
    if target.is_file():
        reason = skip_reason(target, root, gitignore)
        if reason:
            raise GitHubError(403, _skip_error(reason, rel_posix(root, target)))
        return {
            "type": "file",
            "path": rel_posix(root, target),
            "size": target.stat().st_size,
            "binary": is_binary_path(target.name),
        }
    if not target.is_dir():
        raise GitHubError(404, f"本地不存在: {rel or '.'}")
    entries: list[dict[str, Any]] = []
    ignored = 0
    for p in sorted(target.rglob("*")):
        if not p.is_file():
            continue
        reason = skip_reason(p, root, gitignore)
        if reason:
            if reason == "gitignore":
                ignored += 1
            continue
        entries.append(
            {
                "path": rel_posix(root, p),
                "size": p.stat().st_size,
                "binary": is_binary_path(p.name),
            }
        )
        if len(entries) >= MAX_LIST:
            break
    return {
        "type": "dir",
        "root": str(root),
        "path": rel_posix(root, target) if target != root else ".",
        "count": len(entries),
        "truncated": len(entries) >= MAX_LIST,
        "gitignore": True,
        "ignored_filtered": ignored,
        "entries": entries,
    }


def read_local_file(root: Path, rel: str, limit: int = 2000) -> dict[str, Any]:
    target = resolve_under_root(root, rel)
    if target.is_dir():
        raise GitHubError(400, "那是目录，用 list")
    if not target.is_file():
        raise GitHubError(404, f"本地不存在: {rel}")
    gitignore = load_gitignore(root)
    reason = skip_reason(target, root, gitignore)
    if reason:
        raise GitHubError(403, _skip_error(reason, rel_posix(root, target)))
    size = target.stat().st_size
    if size > MAX_FILE_BYTES:
        raise GitHubError(400, f"文件过大: {rel} ({size} bytes)")
    if is_binary_path(target.name):
        raise GitHubError(400, f"二进制文件不读正文: {rel}。推仓用 github_files local_paths / sync")
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise GitHubError(400, f"二进制文件不读: {rel}") from exc
    return {
        "type": "file",
        "path": rel_posix(root, target),
        "size": size,
        "content": clip_text(text, limit),
    }


def _skip_error(reason: str, rel: str) -> str:
    if reason == "gitignore":
        return f"被 .gitignore 忽略: {rel}"
    if reason == "blocked":
        return f"拒绝敏感路径: {rel}"
    return f"跳过: {rel} ({reason})"


def _read_file_for_push(root: Path, path: Path) -> dict[str, Any]:
    size = path.stat().st_size
    rel = rel_posix(root, path)
    if size > MAX_FILE_BYTES:
        raise GitHubError(400, f"文件过大: {rel} ({size} bytes)")
    raw = path.read_bytes()
    if is_binary_path(path.name):
        return {
            "path": rel,
            "content": base64.b64encode(raw).decode("ascii"),
            "encoding": "base64",
        }
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "path": rel,
            "content": base64.b64encode(raw).decode("ascii"),
            "encoding": "base64",
        }
    return {"path": rel, "content": text, "encoding": "utf-8"}


def collect_local_bundle(
    root: Path, paths: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, str]], set[str]]:
    """Return (files_to_push, skipped, present_paths)."""
    if not paths:
        paths = [""]
    gitignore = load_gitignore(root)
    files: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    present: set[str] = set()
    seen: set[str] = set()

    def add_skipped(rel: str, reason: str) -> None:
        skipped.append({"path": rel, "reason": reason})

    for raw in paths:
        target = resolve_under_root(root, raw)
        if target.is_dir():
            for p in sorted(target.rglob("*")):
                if not p.is_file():
                    continue
                rel = rel_posix(root, p)
                reason = skip_reason(p, root, gitignore)
                if reason:
                    if reason not in {"skip-dir", "skip-name", "skip-suffix", "gitignore", "blocked"}:
                        add_skipped(rel, reason)
                    elif reason in {"skip-suffix"}:
                        present.add(rel)
                        add_skipped(rel, reason)
                    continue
                present.add(rel)
                try:
                    item = _read_file_for_push(root, p)
                except GitHubError as exc:
                    add_skipped(rel, exc.message)
                    continue
                if item["path"] in seen:
                    continue
                seen.add(item["path"])
                files.append(item)
                if len(files) > MAX_PUSH_FILES:
                    raise GitHubError(400, f"一次最多推 {MAX_PUSH_FILES} 个文件")
            continue
        if not target.is_file():
            raise GitHubError(404, f"本地不存在: {raw}")
        rel = rel_posix(root, target)
        reason = skip_reason(target, root, gitignore)
        if reason:
            if reason == "blocked":
                raise GitHubError(403, _skip_error(reason, rel))
            raise GitHubError(403, _skip_error(reason, rel))
        present.add(rel)
        item = _read_file_for_push(root, target)
        if item["path"] not in seen:
            seen.add(item["path"])
            files.append(item)
        if len(files) > MAX_PUSH_FILES:
            raise GitHubError(400, f"一次最多推 {MAX_PUSH_FILES} 个文件")
    return files, skipped, present


def collect_local_files(root: Path, paths: list[str]) -> list[dict[str, Any]]:
    files, _skipped, _present = collect_local_bundle(root, paths)
    if not files:
        raise GitHubError(400, "local_dir 下没有可推的文件")
    return files


def as_path_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            import json

            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise GitHubError(400, f"paths 不是合法 JSON: {exc}") from exc
            if not isinstance(parsed, list):
                raise GitHubError(400, "paths 必须是字符串数组")
            return [str(x).strip() for x in parsed if str(x).strip()]
        return [text]
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    raise GitHubError(400, "paths 必须是字符串或数组")
