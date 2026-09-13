from __future__ import annotations

import asyncio
import inspect
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
from .inner_tools import build_inner_tools, set_runner
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
)

try:
    from astrbot.core.agent.tool import ToolSet
except ImportError:
    try:
        from astrbot.core.provider.func_tool_manager import ToolSet
    except ImportError:
        from astrbot.api.provider import ToolSet

INNER_SYSTEM_PROMPT = (
    "只操作 bot 自己的 GitHub 号。用工具干活。"
    "先 github_whoami 再写。"
    "只回摘要和 html_url。"
    "不准打 token / PAT / Authorization。"
    "没要求就别把文件正文或 issue 全文丢回来。"
    "task 已自包含，不要假设还有聊天记录。"
    "fork 的 owner 是源仓；写入目标默认是 bot 自己的 login。"
)

TOOL_DESCRIPTION = (
    "Operate THIS BOT's own GitHub account (not the human user's). "
    "Pass the whole job in task, including any file contents to commit. "
    "Do not split into GitHub actions yourself. "
    "Never print the token."
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
    description: TOOL_DESCRIPTION
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "Self-contained GitHub task for the bot's account. "
                        "Include repo names and any file contents. "
                        "The inner loop cannot see QQ/chat history."
                    ),
                }
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
        return await plugin.run_github_agent(event, str(kwargs.get("task") or ""))


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
        key = (token, api_base, proxy)
        if self._client is not None and self._client_key == key:
            return self._client
        old = self._client
        self._client = GitHubClient(token, api_base=api_base, proxy=proxy)
        self._client_key = key
        if old is not None:
            asyncio.create_task(old.aclose())
        return self._client

    def _who_can_use(self) -> str:
        value = str(self.config.get("who_can_use") or "admin").strip().lower()
        return value if value in {"admin", "all"} else "admin"

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
                return dumps(
                    {
                        "error": f"未知 {group} action: {kwargs.get('action')}",
                    }
                )
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
                        "hint": "allowed_repos 为空时只能写 bot 自己的 login",
                    }
                )
        return await dispatch(
            client,
            action,
            kwargs,
            default_private=bool(self.config.get("default_private", True)),
            allow_delete_repo=bool(self.config.get("allow_delete_repo", False)),
            allow_merge_pr=bool(self.config.get("allow_merge_pr", True)),
        )

    async def run_github_agent(self, event: AstrMessageEvent, task: str) -> str:
        if not self._allowed(event):
            return dumps({"error": "当前会话无权使用 bot 的 GitHub 号（who_can_use=admin）"})
        client = self._get_client()
        if not client.configured:
            return dumps({"error": "管理员还没在插件配置里填写 github_token"})
        task = (task or "").strip()
        if not task:
            return dumps({"error": "task 为空。把完整 GitHub 任务（含文件内容）写进 task。"})

        loop_kwargs: dict[str, Any] = {
            "event": event,
            "chat_provider_id": await self.context.get_current_chat_provider_id(
                event.unified_msg_origin
            ),
            "prompt": task,
            "system_prompt": INNER_SYSTEM_PROMPT,
            "tools": _make_tool_set(build_inner_tools()),
            "max_steps": 12,
            "tool_call_timeout": 60,
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
        return clip_text(text.strip(), OUTER_TEXT_LIMIT)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("github")
    async def github_cmd(self, event: AstrMessageEvent, sub: str = "status"):
        sub = (sub or "status").strip().lower()
        if sub == "actions":
            yield event.plain_result(help_text())
            return
        client = self._get_client()
        if not client.configured:
            yield event.plain_result("还没填 github_token。到 WebUI 插件配置里贴 bot 自己的 PAT。")
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
            "bot GitHub 号: {login}\n"
            "主页: {url}\n"
            "public_repos: {pub}  private_repos: {priv}\n"
            "API remaining: {remain}/{limit}\n"
            "proxy: {proxy}\n"
            "who_can_use: {who}\n"
            "allowed_repos: {allow}".format(
                login=me.get("login"),
                url=me.get("html_url"),
                pub=me.get("public_repos"),
                priv=me.get("total_private_repos"),
                remain=rate.get("remaining"),
                limit=rate.get("limit"),
                proxy=client.proxy_display() or "(空=环境变量 HTTP(S)_PROXY)",
                who=self._who_can_use(),
                allow=", ".join(self._allowed_repos()) or "(空=只写自己的 login)",
            )
        )
