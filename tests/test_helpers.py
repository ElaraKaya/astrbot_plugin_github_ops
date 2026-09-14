import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import github_client as gc  # noqa: E402
import redact  # noqa: E402


def _load_module(name: str, filename: str, extras: dict | None = None):
    pkg = sys.modules.get("astrbot_plugin_github_ops")
    if pkg is None:
        pkg = types.ModuleType("astrbot_plugin_github_ops")
        pkg.__path__ = [str(ROOT)]
        sys.modules["astrbot_plugin_github_ops"] = pkg
    sys.modules["astrbot_plugin_github_ops.github_client"] = gc
    sys.modules["astrbot_plugin_github_ops.redact"] = redact
    if extras:
        sys.modules.update(extras)
    spec = importlib.util.spec_from_file_location(
        f"astrbot_plugin_github_ops.{name}",
        ROOT / filename,
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"astrbot_plugin_github_ops.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_localfs():
    return _load_module("localfs", "localfs.py")


def _load_ops():
    localfs = _load_localfs()
    return _load_module(
        "ops",
        "ops.py",
        {
            "astrbot_plugin_github_ops.localfs": localfs,
            "astrbot_plugin_github_ops.redact": redact,
        },
    )


def test_redact_proxy() -> None:
    assert gc.redact_proxy("") == ""
    assert gc.redact_proxy("http://127.0.0.1:7890") == "http://127.0.0.1:7890"
    assert gc.redact_proxy("http://user:pw@127.0.0.1:7890") == "http://127.0.0.1:7890"
    hidden = gc.redact_proxy("socks5://alice:secret@10.0.0.1:1080")
    assert hidden == "socks5://10.0.0.1:1080"
    assert "secret" not in hidden
    client = gc.GitHubClient("x", proxy="http://user:pw@127.0.0.1:7890")
    assert client.proxy_display() == "http://127.0.0.1:7890"
    assert client._proxy == "http://user:pw@127.0.0.1:7890"
    empty = gc.GitHubClient("x")
    assert empty.proxy_display() == ""


def test_clip_and_brief() -> None:
    assert "truncated" in gc.clip_text("a" * 50, 10)
    brief = gc._brief_repo(
        {
            "full_name": "bot/toy",
            "private": True,
            "html_url": "https://github.com/bot/toy",
            "stargazers_count": 1,
        }
    )
    assert brief["full_name"] == "bot/toy"


def test_commit_author_identity() -> None:
    # 1. With login and id (default noreply)
    c1 = gc.GitHubClient("tok")
    c1._login = "elara"
    c1._id = 987654
    assert c1.get_commit_author() == {
        "name": "elara",
        "email": "987654+elara@users.noreply.github.com",
    }

    # 2. Fallback without id
    c2 = gc.GitHubClient("tok")
    c2._login = "elara"
    c2._id = None
    assert c2.get_commit_author() == {
        "name": "elara",
        "email": "elara@users.noreply.github.com",
    }

    # 3. Custom committer name and email
    c3 = gc.GitHubClient("tok", committer_name="CustomDev", committer_email="dev@custom.org")
    c3._login = "elara"
    c3._id = 987654
    assert c3.get_commit_author() == {
        "name": "CustomDev",
        "email": "dev@custom.org",
    }

    # 4. _client_key differentiation logic
    def make_key(token, api_base, proxy, name, email):
        return (token, api_base, proxy, name, email)

    k1 = make_key("tok", "base", "", "", "")
    k2 = make_key("tok", "base", "", "custom", "")
    assert k1 != k2


def test_redact_module() -> None:
    # Exact token match
    assert redact.find_secret("my secret tok_12345", token="tok_12345") is not None
    # Extra needles (e.g. profile email)
    assert redact.find_secret("contact user@private.com", extra_needles=["user@private.com"]) is not None
    # Allowed ordinary QQ email
    assert redact.find_secret("contact 1234567@qq.com") is None
    # Allowed short sk-
    assert redact.find_secret("sk-12345") is None

    # Secret patterns
    assert redact.find_secret("ghp_" + "A" * 30) is not None
    assert redact.find_secret("github_pat_" + "1" * 30) is not None
    assert redact.find_secret("-" * 5 + "BEGIN PRIVATE KEY" + "-" * 5 + "\nMIIE...") is not None
    assert redact.find_secret("-" * 5 + "BEGIN OPENSSH PRIVATE KEY" + "-" * 5 + "\n...") is not None

    # redact_text
    raw = "Auth: " + ("ghp_" + "1" * 25) + " and user@private.com and ok"
    cleaned = redact.redact_text(raw, token="", extra_needles=["user@private.com"])
    assert "ghp_" not in cleaned
    assert "user@private.com" not in cleaned
    assert "[REDACTED]" in cleaned
    assert "and ok" in cleaned


def test_ops_guards() -> None:
    ops = _load_ops()
    files = ops._as_files('[{"path":"README.md","content":"# hi"}]')
    assert files == [{"path": "README.md", "content": "# hi"}]
    assert ops.map_group_action("github_repo", "create") == "create_repo"
    assert ops.map_group_action("github_files", "push") == "push_files"
    assert ops.map_group_action("github_local", "list") == "list_local"
    assert ops.map_group_action("github_misc", "fork") == "fork_repo"
    assert ops.map_group_action("github_repo", "nope") is None

    assert ops.is_blocked_path(".env")
    assert ops.is_blocked_path("./.env")
    assert ops.is_blocked_path("secret/id_rsa")
    assert ops.is_blocked_path("certs/foo.PEM")
    assert ops.is_blocked_path(".git/config")
    assert not ops.is_blocked_path(".env.example")
    assert not ops.is_blocked_path("README.md")

    assert ops.repo_write_allowed(
        login="bot", owner="", repo="toy", allowed_repos=[]
    )
    assert not ops.repo_write_allowed(
        login="bot", owner="octocat", repo="Hello-World", allowed_repos=[]
    )
    assert ops.repo_write_allowed(
        login="bot",
        owner="bot",
        repo="toy",
        allowed_repos=["bot/toy"],
    )
    assert not ops.repo_write_allowed(
        login="bot",
        owner="bot",
        repo="other",
        allowed_repos=["bot/toy"],
    )
    dumped = ops.dumps({"a": 1})
    assert "\n" not in dumped


def test_ops_dispatch_secret_scanning() -> None:
    ops = _load_ops()
    client = gc.GitHubClient("fake_token_12345")
    client._profile_email = "my_private@secret.com"

    # 1. Block put_file with token or sensitive text
    res = asyncio.run(
        ops.dispatch(
            client,
            "put_file",
            {"owner": "bot", "repo": "toy", "path": "file.txt", "content": "key is fake_token_12345"},
            default_private=True,
            allow_delete_repo=False,
            allow_merge_pr=True,
        )
    )
    data = json.loads(res)
    assert "error" in data and "文件 file.txt" in data["error"]

    # 2. Block push_files with profile_email
    res = asyncio.run(
        ops.dispatch(
            client,
            "push_files",
            {
                "owner": "bot",
                "repo": "toy",
                "files": [{"path": "a.txt", "content": "contact my_private@secret.com"}],
                "message": "push",
            },
            default_private=True,
            allow_delete_repo=False,
            allow_merge_pr=True,
        )
    )
    data = json.loads(res)
    assert "error" in data and "文件 a.txt" in data["error"]

    # 3. Block create_repo with secret in message/title/description or files
    res = asyncio.run(
        ops.dispatch(
            client,
            "create_repo",
            {
                "name": "newrepo",
                "description": "repo with " + ("ghp_" + "1" * 25),
            },
            default_private=True,
            allow_delete_repo=False,
            allow_merge_pr=True,
        )
    )
    data = json.loads(res)
    assert "error" in data and "description" in data["error"]

    # 4. Message secret scanning
    res = asyncio.run(
        ops.dispatch(
            client,
            "put_file",
            {"owner": "bot", "repo": "toy", "path": "file.txt", "content": "clean", "message": "commit " + ("ghp_" + "1" * 25)},
            default_private=True,
            allow_delete_repo=False,
            allow_merge_pr=True,
        )
    )
    data = json.loads(res)
    assert "error" in data and "message" in data["error"]

    # 5. Normal QQ email should pass dispatch check (will fail at network since fake token, caught cleanly)
    res = asyncio.run(
        ops.dispatch(
            client,
            "put_file",
            {"owner": "bot", "repo": "toy", "path": "file.txt", "content": "email: 123456@qq.com"},
            default_private=True,
            allow_delete_repo=False,
            allow_merge_pr=True,
        )
    )
    data = json.loads(res)
    # Shouldn't fail with secret detection error
    assert "检测到" not in data.get("error", "")


def test_localfs_sandbox(tmp_path: Path | None = None) -> None:
    localfs = _load_localfs()
    ops = _load_ops()
    root = Path("/tmp/github_ops_localfs_test")
    if tmp_path is not None:
        root = tmp_path
    else:
        if root.exists():
            import shutil

            shutil.rmtree(root)
        root.mkdir(parents=True)

    (root / "README.md").write_text("# hi\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print(1)\n", encoding="utf-8")
    (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "app.cpython-312.pyc").write_bytes(b"\x00\x01")
    (root / "src" / "skip.bin").write_bytes(b"\x00\xff")

    parsed = localfs.parse_local_root(str(root))
    listed = localfs.list_local_entries(parsed, "")
    paths = {x["path"] for x in listed["entries"]}
    assert "README.md" in paths
    assert "src/app.py" in paths
    assert ".env" not in paths
    assert not any(p.startswith("__pycache__") for p in paths)
    assert "src/skip.bin" not in paths

    try:
        localfs.parse_local_root("relative/path")
        raise AssertionError("relative local_dir should fail")
    except gc.GitHubError as exc:
        assert exc.status == 400

    try:
        localfs.resolve_under_root(parsed, "../etc/passwd")
        raise AssertionError("path escape should fail")
    except gc.GitHubError as exc:
        assert exc.status == 403

    try:
        localfs.read_local_file(parsed, ".env")
        raise AssertionError(".env should be blocked")
    except gc.GitHubError as exc:
        assert exc.status == 403

    files = localfs.collect_local_files(parsed, [""])
    collected = {x["path"] for x in files}
    assert collected == {"README.md", "src/app.py"}
    assert all(x["content"] for x in files)

    blocked = ops.blocked_paths_in({"local_paths": [".env", "README.md"]})
    assert ".env" in blocked

    empty = ops._load_local_files(None, {})
    assert empty == []
    try:
        ops._load_local_files(None, {"local_paths": ["README.md"]})
        raise AssertionError("ungranted local_dir should fail")
    except gc.GitHubError as exc:
        assert "未授予" in exc.message

    loaded = ops._load_local_files(parsed, {"local_paths": ["src"]})
    assert loaded == [{"path": "src/app.py", "content": "print(1)\n"}]
    assert json.loads(ops.dumps({"ok": True}))["ok"] is True


if __name__ == "__main__":
    test_redact_proxy()
    test_clip_and_brief()
    test_commit_author_identity()
    test_redact_module()
    test_ops_guards()
    test_ops_dispatch_secret_scanning()
    test_localfs_sandbox()
    print("ALL TESTS PASSED SUCCESSFULLY!")
