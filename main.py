from __future__ import annotations

import asyncio
import inspect
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from .github_client import GitHubClient, GitHubError, clip_text

try:
    from .redact import extract_proxy_credentials, redact_text
except ImportError:
    from redact import extract_proxy_credentials, redact_text
from .inner_tools import build_inner_tools, set_runner
from .localfs import parse_local_root
from .ops import (
    OUTER_TEXT_LIMIT,
    PATH_CHECKED_ACTIONS,
    WRITE_ACTIONS,
    blocked_paths_in,
    dispatch,
    dumps,
    help_text,
    map_group_action,
    repo_write_allowed,
    unknown_group_action,
)

try:
    from astrbot.core.agent.tool import ToolSet
except ImportError:  # pragma: no cover - fallback across AstrBot layouts
    try:
        from astrbot.core.provider.func_tool_manager import ToolSet
    except ImportError:  # pragma: no cover
        from astrbot.api.provider import ToolSet

_local_root_var: ContextVar[Path | None] = ContextVar("github_ops_local_root", default=None)

INNER_SYSTEM_PROMPT = (
    "执行 GitHub 相关任务。用工具干活。"
    "身份已在任务里给出，不必先调 github_whoami。"
    "只回摘要和 html_url。"
    "严禁输出 token / PAT / Authorization。"
    "工具若返回 [REDACTED] 不要试图还原。"
    "没要求就别把文件正文或 issue 全文丢回来。"
    "task 已自包含，不要假设还有聊天记录。"
    "fork 的 owner 是源仓；写入目标默认是当前登录账号。"
    "若已授予 local_dir：github_local 只能读这个目录（list 已按 .gitignore 过滤）；"
    "推仓用 github_files 的 sync 或 local_paths，不要把文件正文再抄一遍。"
    "删文件用 github_files action=delete，或 push/sync 时 files[].delete=true。"
    "这与删仓无关；github_repo action=delete 才会删整个仓库，且通常禁止。"
    "删分支用 github_misc action=delete_branch，禁止删默认分支。"
    "不要为了试 API 另开分支；用户没要求就写默认分支。"
    "遇到工具报错、未知 action、缺参数、结果与任务不符："
    "立刻停止试探，在最终回复里如实汇报原因。禁止改 action 名乱猜，"
    "禁止建实验分支，禁止把文件写成空内容来模拟删除。"
)

INNER_INCIDENT_PROMPT = (
    "[运行规则]\n"
    "身份已确认，不必调 github_whoami。\n"
    "意外情况（工具报错、未知 action、缺参数、结果和任务不符）立刻停止试探，"
    "在最终回复如实汇报，不要换 action 名乱猜，不要另开测试分支，"
    "不要把文件写成空内容来模拟删除。\n"
    "删文件：github_files action=delete，或 push/sync 的 files.delete=true。"
    "这与配置里的 allow_delete_repo（删整个仓库）无关。\n"
    "删分支：github_misc action=delete_branch，不能删默认分支。\n"
    "有 local_dir 时优先 github_files action=sync（整目录）或 local_paths。"
)

TOOL_DESCRIPTION = (
    "Execute GitHub tasks with configured GitHub account. "
    "Pass the whole job in task. For local source trees, pass local_dir "
    "(absolute path) instead of file bodies. Do not split into GitHub "
    "actions yourself. Never print the token."
)


def _make_tool_set(tools: list[Any]) -> Any:
    try:
        return ToolSet(tools)
    except TypeError:
        tool_set = ToolSet()
        for tool in tools:
            tool_set.add_tool(tool)
        return tool_set


@dataclass
class GitHubOpsTool(FunctionTool[AstrAgentContext]):
    name: str = "github_ops"
    description: str = TOOL_DESCRIPTION
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "Self-contained GitHub task for the bot's account. "
                        "Include repo names. Prefer local_dir over file bodies. "
                        "The inner loop cannot see QQ/chat history."
                    ),
                },
                "local_dir": {
                    "type": "string",
                    "description": (
                        "Optional absolute directory the inner loop may read. "
                        "Paths outside it are rejected. Use this to push local files."
                    ),
                },
            },
            "required": ["task"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        plugin = GitHubOpsPlugin.instance
        if plugin is None:
            return "github_ops 未初始化"
        event = context.context.event
        return await plugin.run_github_agent(
            event,
            str(kwargs.get("task") or ""),
            str(kwargs.get("local_dir") or ""),
        )


class GitHubOpsPlugin(Star):
    instance: GitHubOpsPlugin | None = None

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        self._client: GitHubClient | None = None
        self._client_key: tuple[str, str, str] | None = None
        GitHubOpsPlugin.instance = self
        set_runner(self.run_inner)
        self.context.add_llm_tools(GitHubOpsTool())

    async def initialize(self) -> None:
        client = self._get_client()
        if client.configured:
            try:
                me = await client.whoami()
                proxy = client.proxy_display() or "env"
                logger.info(
                    f"[github_ops] bot GitHub login={me.get('login')} proxy={proxy}"
                )
            except GitHubError as exc:
                logger.warning(f"[github_ops] token 校验失败: {exc.status}")
            except Exception as exc:
                logger.warning(f"[github_ops] 初始化 whoami 失败: {type(exc).__name__}")
        else:
            logger.warning("[github_ops] 未配置 github_token，工具会拒绝执行")

    async def terminate(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            self._client_key = None
        if GitHubOpsPlugin.instance is self:
            GitHubOpsPlugin.instance = None

    def _get_client(self) -> GitHubClient:
        token = str(self.config.get("github_token") or "").strip()
        api_base = str(self.config.get("api_base") or "https://api.github.com").strip()
        proxy = str(self.config.get("http_proxy") or "").strip()
        name = str(self.config.get("git_committer_name") or "").strip()
        email = str(self.config.get("git_committer_email") or "").strip()
        key = (token, api_base, proxy, name, email)
        if self._client is not None and self._client_key == key:
            return self._client
        old = self._client
        self._client = GitHubClient(
            token,
            api_base=api_base,
            proxy=proxy,
            committer_name=name,
            committer_email=email,
        )
        self._client_key = key
        if old is not None:
            asyncio.create_task(old.aclose())
        return self._client

    def _get_extra_needles(self, client: GitHubClient) -> list[str]:
        proxy = str(self.config.get("http_proxy") or "").strip()
        needles = extract_proxy_credentials(proxy)
        profile_email = getattr(client, "_profile_email", "")
        if profile_email:
            needles.append(profile_email)
        return needles

    def _who_can_use(self) -> str:
        value = str(self.config.get("who_can_use") or "admin").strip().lower()
        return value if value in {"admin", "all"} else "admin"

    def _max_steps(self) -> int:
        raw = self.config.get("max_steps", 24)
        try:
            n = int(raw)
        except (TypeError, ValueError):
            n = 24
        return min(max(n, 4), 60)

    def _allowed_repos(self) -> list[str]:
        raw = self.config.get("allowed_repos") or []
        if isinstance(raw, str):
            return [x.strip() for x in raw.split(",") if x.strip()]
        if isinstance(raw, list):
            return [str(x).strip() for x in raw if str(x).strip()]
        return []

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        check = getattr(event, "is_admin", None)
        if callable(check):
            try:
                return bool(check())
            except Exception:
                return False
        return False

    def _allowed(self, event: AstrMessageEvent) -> bool:
        if self._who_can_use() == "all":
            return True
        return self._is_admin(event)

    async def run_inner(self, group: str, kwargs: dict[str, Any]) -> str:
        client = self._get_client()
        if not client.configured:
            return dumps({"error": "github_token 未配置"})
        if group == "github_whoami":
            action = "whoami"
        else:
            mapped = map_group_action(group, str(kwargs.get("action") or ""))
            if not mapped:
                return unknown_group_action(group, str(kwargs.get("action") or ""))
            action = mapped
        if action in PATH_CHECKED_ACTIONS:
            blocked = blocked_paths_in(kwargs)
            if blocked:
                return dumps({"error": "拒绝敏感路径", "paths": blocked})
        if action in WRITE_ACTIONS:
            try:
                login = await client.login()
            except GitHubError as exc:
                return dumps({"error": exc.message, "status": exc.status})
            owner = str(kwargs.get("owner") or "").strip()
            repo = str(kwargs.get("repo") or kwargs.get("name") or "").strip()
            if not repo_write_allowed(
                login=login,
                owner=owner,
                repo=repo,
                allowed_repos=self._allowed_repos(),
            ):
                return dumps(
                    {
                        "error": "写入目标不在白名单",
                        "owner": owner or login,
                        "repo": repo,
                        "hint": "allowed_repos 为空时只能写入当前登录账号下的仓库",
                    }
                )
        res = await dispatch(
            client,
            action,
            kwargs,
            default_private=bool(self.config.get("default_private", True)),
            allow_delete_repo=bool(self.config.get("allow_delete_repo", False)),
            allow_merge_pr=bool(self.config.get("allow_merge_pr", True)),
            local_root=_local_root_var.get(),
        )
        return redact_text(
            res,
            token=client._token,
            extra_needles=self._get_extra_needles(client),
        )

    async def run_github_agent(
        self,
        event: AstrMessageEvent,
        task: str,
        local_dir: str = "",
    ) -> str:
        if not self._allowed(event):
            return dumps({"error": "无权调用 GitHub 功能（权限受限：who_can_use=admin）"})
        client = self._get_client()
        if not client.configured:
            return dumps({"error": "管理员尚未配置 github_token"})
        task = (task or "").strip()
        if not task:
            return dumps({"error": "task 为空。把完整 GitHub 任务写进 task。需要推本地文件就加 local_dir。"})

        try:
            local_root = parse_local_root(local_dir) if local_dir.strip() else None
        except GitHubError as exc:
            return dumps({"error": exc.message, "status": exc.status})

        try:
            me = await client.whoami()
        except GitHubError as exc:
            return dumps({"error": exc.message, "status": exc.status})
        except Exception as exc:
            return dumps({"error": "github whoami 失败", "detail": type(exc).__name__})

        login = me.get("login") or ""
        rate = me.get("rate_limit") or {}
        identity_block = (
            f"[bot GitHub login={login} html_url={me.get('html_url')} "
            f"rate={rate.get('remaining')}/{rate.get('limit')}]"
        )
        prompt_parts = [task.strip(), "", identity_block, INNER_INCIDENT_PROMPT]
        if local_root is not None:
            prompt_parts.append(
                f"[granted local_dir={local_root}]\n"
                "只读这个目录。推仓用 github_files action=sync 或 local_paths，不要抄文件正文。"
                "list 已按 .gitignore 过滤。"
            )
        prompt = "\n".join(prompt_parts)

        token = _local_root_var.set(local_root)
        try:
            loop_kwargs: dict[str, Any] = {
                "event": event,
                "chat_provider_id": await self.context.get_current_chat_provider_id(
                    event.unified_msg_origin
                ),
                "prompt": prompt,
                "system_prompt": INNER_SYSTEM_PROMPT,
                "tools": _make_tool_set(build_inner_tools()),
                "max_steps": self._max_steps(),
                "tool_call_timeout": 120,
            }
            try:
                params = inspect.signature(self.context.tool_loop_agent).parameters
            except (TypeError, ValueError):
                params = {}
            if "contexts" in params:
                loop_kwargs["contexts"] = []

            try:
                llm_resp = await self.context.tool_loop_agent(**loop_kwargs)
            except TypeError as exc:
                logger.warning(f"[github_ops] tool_loop_agent 参数不兼容，去掉 contexts 重试: {exc}")
                loop_kwargs.pop("contexts", None)
                try:
                    llm_resp = await self.context.tool_loop_agent(**loop_kwargs)
                except Exception as exc2:
                    logger.error(f"[github_ops] 子循环失败: {exc2}")
                    return dumps({"error": "github 子循环失败", "detail": type(exc2).__name__})
            except Exception as exc:
                logger.error(f"[github_ops] 子循环失败: {exc}")
                return dumps({"error": "github 子循环失败", "detail": type(exc).__name__})

            text = ""
            if llm_resp is not None:
                text = str(getattr(llm_resp, "completion_text", None) or "")
            if not text.strip():
                return dumps({"error": "子循环没有返回文本"})
            redacted_text = redact_text(
                text.strip(),
                token=client._token,
                extra_needles=self._get_extra_needles(client),
            )
            return clip_text(redacted_text, OUTER_TEXT_LIMIT)
        finally:
            _local_root_var.reset(token)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("github")
    async def github_cmd(self, event: AstrMessageEvent, sub: str = "status"):
        """查看 GitHub 账号状态。子命令: status / whoami / actions"""
        sub = (sub or "status").strip().lower()
        if sub == "actions":
            yield event.plain_result(help_text())
            return
        client = self._get_client()
        if not client.configured:
            yield event.plain_result("尚未配置 github_token。请在 WebUI 插件配置中填写 PAT。")
            return
        try:
            me = await client.whoami()
        except GitHubError as exc:
            yield event.plain_result(f"GitHub 校验失败 ({exc.status}): {exc.message}")
            return
        except Exception as exc:
            yield event.plain_result(f"GitHub 请求失败: {type(exc).__name__}")
            return
        rate = me.get("rate_limit") or {}
        yield event.plain_result(
            "GitHub 账号: {login}\n"
            "主页: {url}\n"
            "public_repos: {pub}  private_repos: {priv}\n"
            "API remaining: {remain}/{limit}\n"
            "proxy: {proxy}\n"
            "committer: {committer}\n"
            "who_can_use: {who}\n"
            "max_steps: {steps}\n"
            "allowed_repos: {allow}".format(
                login=me.get("login"),
                url=me.get("html_url"),
                pub=me.get("public_repos"),
                priv=me.get("total_private_repos"),
                remain=rate.get("remaining"),
                limit=rate.get("limit"),
                proxy=client.proxy_display() or "(空=环境变量 HTTP(S)_PROXY)",
                committer=f"{client.get_commit_author()['name']} <{'(custom)' if self.config.get('git_committer_email') else 'noreply'}>",
                who=self._who_can_use(),
                steps=self._max_steps(),
                allow=", ".join(self._allowed_repos()) or "(空=只写自己的 login)",
            )
        )
