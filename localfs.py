"""Sandbox local file access for the inner GitHub loop.

Inner tools may only touch files under the granted local_dir.
"""

from __future__ import annotations

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
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".tar",
    ".whl",
    ".woff",
    ".woff2",
    ".ttf",
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


def should_skip(path: Path, root: Path) -> bool:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return True
    if any(part in SKIP_DIRS for part in rel.parts):
        return True
    name = path.name.lower()
    if name in SKIP_FILE_NAMES:
        return True
    if name.endswith(SKIP_SUFFIXES):
        return True
    if is_blocked_path(rel.as_posix()):
        return True
    return False


def list_local_entries(root: Path, rel: str = "") -> dict[str, Any]:
    target = resolve_under_root(root, rel)
    if target.is_file():
        return {
            "type": "file",
            "path": rel_posix(root, target),
            "size": target.stat().st_size,
        }
    if not target.is_dir():
        raise GitHubError(404, f"本地不存在: {rel or '.'}")
    entries: list[dict[str, Any]] = []
    for p in sorted(target.rglob("*")):
        if not p.is_file() or should_skip(p, root):
            continue
        entries.append({"path": rel_posix(root, p), "size": p.stat().st_size})
        if len(entries) >= MAX_LIST:
            break
    return {
        "type": "dir",
        "root": str(root),
        "path": rel_posix(root, target) if target != root else ".",
        "count": len(entries),
        "truncated": len(entries) >= MAX_LIST,
        "entries": entries,
    }


def read_local_file(root: Path, rel: str, limit: int = 2000) -> dict[str, Any]:
    target = resolve_under_root(root, rel)
    if target.is_dir():
        raise GitHubError(400, "那是目录，用 list")
    if not target.is_file():
        raise GitHubError(404, f"本地不存在: {rel}")
    if should_skip(target, root) or is_blocked_path(rel_posix(root, target)):
        raise GitHubError(403, "拒绝敏感路径")
    size = target.stat().st_size
    if size > MAX_FILE_BYTES:
        raise GitHubError(400, f"文件过大: {rel} ({size} bytes)")
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


def _read_text_file(root: Path, path: Path) -> dict[str, str]:
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise GitHubError(400, f"文件过大: {rel_posix(root, path)} ({size} bytes)")
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise GitHubError(400, f"二进制文件不推: {rel_posix(root, path)}") from exc
    return {"path": rel_posix(root, path), "content": content}


def collect_local_files(root: Path, paths: list[str]) -> list[dict[str, str]]:
    if not paths:
        paths = [""]
    files: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in paths:
        target = resolve_under_root(root, raw)
        if target.is_dir():
            for p in sorted(target.rglob("*")):
                if not p.is_file() or should_skip(p, root):
                    continue
                try:
                    item = _read_text_file(root, p)
                except GitHubError:
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
        if should_skip(target, root) or is_blocked_path(rel_posix(root, target)):
            raise GitHubError(403, "拒绝敏感路径")
        item = _read_text_file(root, target)
        if item["path"] not in seen:
            seen.add(item["path"])
            files.append(item)
        if len(files) > MAX_PUSH_FILES:
            raise GitHubError(400, f"一次最多推 {MAX_PUSH_FILES} 个文件")
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
