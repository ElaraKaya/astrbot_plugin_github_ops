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
    "只做 task 里写明的事，做完立刻停。"
    "外层负责读代码和改代码，子循环只做 GitHub 写入与执行。"
    "task 给了完整新正文就原样 put/push；给了 local_dir 就原样 sync。禁止重写、补全或再思考改法。"
    "写入直接 put/push/sync，不要先建实验分支。"
    "身份已给出，不必调 github_whoami。"
    "只回摘要和 html_url。严禁输出 token / PAT / Authorization。[REDACTED] 不要还原。"
    "fork 的 owner 是源仓；写入目标默认是当前登录账号。白名单空时只能写当前登录账号自己的仓库。"
    "仓库在 GitHub 远端，远端只用 github_files。没有浏览器、curl、git clone。"
    "没传 local_dir 时写明 local_dir=none，禁止调用 github_local，禁止 sync 和 local_paths。"
    "有 local_dir 时只读该目录，推仓用 sync 或 local_paths，不要抄正文。"
    "删文件用 github_files action=delete；删仓才是 github_repo action=delete，通常禁止。"
    "删分支用 github_misc action=delete_branch，禁止删默认分支。"
    "报错、未知 action、缺参数或结果不符：立刻停，在最终回复如实汇报。禁止改 action 名乱猜，禁止写空文件冒充删除。"
)

INNER_RULES = (
    "[运行规则]\n"
    "本次最多 {max_steps} 步（含每次工具调用）。步数用尽前结束并汇报做了什么、没做成什么。\n"
    "外层负责读代码和改代码，子循环只做 GitHub 写入。禁止让子循环找到某逻辑再改。\n"
    "task 给出完整新正文时原样 put/push；给出 local_dir 时原样 sync。不要重写代码，不要先读再改。\n"
    "仓库在 GitHub 远端，远端只用 github_files。没有浏览器、curl、git clone。\n"
    "没传 local_dir 时为 local_dir=none，禁止调用 github_local，禁止 sync 和 local_paths。\n"
    "有 local_dir 时只读该目录，推仓用 sync 或 local_paths，不要抄正文。\n"
    "删文件用 github_files action=delete，或 push/sync 的 files.delete=true。与 allow_delete_repo 无关。\n"
    "删分支用 github_misc action=delete_branch，禁止删默认分支。\n"
    "报错、未知 action、缺参数或结果不符立刻停，如实汇报，禁止改 action 名乱猜，禁止写空文件冒充删除。"
)

TOOL_DESCRIPTION = (
    "用配置好的 GitHub 账号做一件具体的事。一次调用只做一件，复杂任务先拆开再调。"
    "整次调用硬超时 120 秒，超时会被取消。"
    "绝对不要使用本工具读取或直接修改远端仓库内容；涉及查看或修改仓库内容时，请先下载到本地工作空间处理，或使用 web 工具查看。修改完成后通过 local_dir 将本地目录原样 sync 推送到远端。"
    "子循环没有浏览器、curl、git clone，没传 local_dir 时禁止本地操作。"
    "max_steps 必填，范围 1–30，纯写入填 2 到 4。不要自己拆成底层 GitHub action。不要输出 token。"
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
                        "一件具体的 GitHub 操作，必须自包含。绝对不要用本工具读取或直接修改远端仓库内容；"
                        "查看或修改代码请下载到本地工作空间或用 web 工具，本地改完后传 local_dir 原样 sync 推送。"
                    ),
                },
                "local_dir": {
                    "type": "string",
                    "description": (
                        "可选。本地绝对目录。有 local_dir 则原样 sync，不要把正文塞进 task；未传时为 none，禁止本地操作。"
                    ),
                },
                "max_steps": {
                    "type": "integer",
                    "description": (
                        "必填。子循环步数上限，范围 1 到 30。纯写入填 2 到 4。"
                    ),
                },
            },
            "required": ["task", "max_steps"],
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
            kwargs.get("max_steps"),
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

    def _parse_max_steps(self, raw: Any) -> int | None:
        try:
            n = int(raw)
        except (TypeError, ValueError):
            return None
        if n < 1 or n > 30:
            return None
        return n

    def _allow_all_repos(self) -> bool:
        return bool(self.config.get("allow_all_repos", False))

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
                allow_all=self._allow_all_repos(),
            ):
                return dumps(
                    {
                        "error": "目标仓库未授权写入",
                        "owner": owner or login,
                        "repo": repo,
                        "hint": "当前仅允许写入当前登录账号下的仓库。可在插件配置开启【允许写入所有仓库】",
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
        max_steps: Any = None,
    ) -> str:
        if not self._allowed(event):
            return dumps({"error": "无权调用 GitHub 功能（权限受限：who_can_use=admin）"})
        client = self._get_client()
        if not client.configured:
            return dumps({"error": "管理员尚未配置 github_token"})
        task = (task or "").strip()
        if not task:
            return dumps({"error": "task 为空。把完整 GitHub 任务写进 task。需要推本地文件就加 local_dir。"})
        steps = self._parse_max_steps(max_steps)
        if steps is None:
            return dumps({"error": "max_steps 必填，且必须是 1 到 30 的整数"})

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
        prompt_parts = [
            task.strip(),
            "",
            identity_block,
            INNER_RULES.format(max_steps=steps),
        ]
        if local_root is not None:
            prompt_parts.append(
                f"[granted local_dir={local_root}]\n"
                "只读这个目录。推仓用 github_files action=sync 或 local_paths，不要抄文件正文。"
                "list 已按 .gitignore 过滤。"
            )
        else:
            prompt_parts.append(
                "[local_dir=none]\n"
                "没有本地目录。仓库在 GitHub 远端，远端只用 github_files。没有浏览器、curl、git clone。\n"
                "禁止调用 github_local，禁止 sync 和 local_paths。"
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
                "max_steps": steps,
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
            "max_steps: 由调用时传入（1-30）\n"
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
                allow="ALL (全部仓库)" if self._allow_all_repos() else (", ".join(self._allowed_repos()) or "(仅限自己账号)"),
            )
        )
