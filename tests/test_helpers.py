import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import github_client as gc  # noqa: E402


def _load_ops():
    pkg = types.ModuleType("astrbot_plugin_github_ops")
    pkg.__path__ = [str(ROOT)]
    sys.modules["astrbot_plugin_github_ops"] = pkg
    sys.modules["astrbot_plugin_github_ops.github_client"] = gc
    spec = importlib.util.spec_from_file_location(
        "astrbot_plugin_github_ops.ops",
        ROOT / "ops.py",
    )
    assert spec and spec.loader
    ops = importlib.util.module_from_spec(spec)
    sys.modules["astrbot_plugin_github_ops.ops"] = ops
    spec.loader.exec_module(ops)
    return ops


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


def test_ops_guards() -> None:
    ops = _load_ops()
    files = ops._as_files('[{"path":"README.md","content":"# hi"}]')
    assert files == [{"path": "README.md", "content": "# hi"}]
    assert ops.map_group_action("github_repo", "create") == "create_repo"
    assert ops.map_group_action("github_files", "push") == "push_files"
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


if __name__ == "__main__":
    test_redact_proxy()
    test_clip_and_brief()
    test_ops_guards()
    print("all passed")
