"""Inner GitHub tools. Only mounted inside github_ops' tool_loop_agent."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

InnerRunner = Callable[[str, dict[str, Any]], Awaitable[str]]

_runner: InnerRunner | None = None


def set_runner(runner: InnerRunner) -> None:
    global _runner
    _runner = runner


async def _run(group: str, kwargs: dict[str, Any]) -> str:
    if _runner is None:
        return '{"error":"github 内层工具未初始化"}'
    return await _runner(group, kwargs)


@dataclass
class GitHubWhoamiTool(FunctionTool[AstrAgentContext]):
    name: str = "github_whoami"
    description: str = "Show the bot's GitHub login, repo counts, and API rate limit."
    parameters: dict = Field(
        default_factory=lambda: {"type": "object", "properties": {}, "required": []}
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        return await _run("github_whoami", kwargs)


@dataclass
class GitHubRepoTool(FunctionTool[AstrAgentContext]):
    name: str = "github_repo"
    description: str = (
        "Bot GitHub repos. action=list|get|create|delete|commits. "
        "create uses name/description/private. delete is often disabled."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "list | get | create | delete | commits",
                },
                "repo": {
                    "type": "string",
                    "description": "Repo name or owner/repo. For create, also accept name.",
                },
                "name": {"type": "string", "description": "New repo name for create."},
                "owner": {
                    "type": "string",
                    "description": "Owner. Omit for the bot's own login.",
                },
                "private": {"type": "boolean", "description": "create: private repo."},
                "description": {"type": "string", "description": "create: description."},
                "branch": {"type": "string", "description": "commits: branch."},
                "page": {"type": "number", "description": "list: page number."},
            },
            "required": ["action"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        return await _run("github_repo", kwargs)


@dataclass
class GitHubFilesTool(FunctionTool[AstrAgentContext]):
    name: str = "github_files"
    description: str = (
        "Read or write files in a repo. action=list|get|put|push. "
        "put=one file; push=multiple files in one commit via files[]."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "list | get | put | push",
                },
                "repo": {"type": "string", "description": "Repo name or owner/repo."},
                "owner": {"type": "string", "description": "Owner. Omit for bot login."},
                "path": {"type": "string", "description": "File or directory path."},
                "content": {"type": "string", "description": "File text for put."},
                "message": {"type": "string", "description": "Commit message."},
                "branch": {"type": "string", "description": "Branch or ref."},
                "files": {
                    "type": "array",
                    "description": "push: [{path, content}, ...]",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path"],
                    },
                },
            },
            "required": ["action", "repo"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        return await _run("github_files", kwargs)


@dataclass
class GitHubIssueTool(FunctionTool[AstrAgentContext]):
    name: str = "github_issue"
    description: str = "Issues. action=list|get|create|comment|update."
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "list | get | create | comment | update",
                },
                "repo": {"type": "string", "description": "Repo name or owner/repo."},
                "owner": {"type": "string", "description": "Owner. Omit for bot login."},
                "number": {"type": "number", "description": "Issue number."},
                "title": {"type": "string", "description": "create/update title."},
                "body": {"type": "string", "description": "create/comment/update body."},
                "state": {
                    "type": "string",
                    "description": "list: open|closed|all. update: open|closed.",
                },
            },
            "required": ["action", "repo"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        return await _run("github_issue", kwargs)


@dataclass
class GitHubPrTool(FunctionTool[AstrAgentContext]):
    name: str = "github_pr"
    description: str = "Pull requests. action=list|create|merge."
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "list | create | merge"},
                "repo": {"type": "string", "description": "Repo name or owner/repo."},
                "owner": {"type": "string", "description": "Owner. Omit for bot login."},
                "title": {"type": "string", "description": "create: PR title."},
                "head": {"type": "string", "description": "create: head branch."},
                "base": {"type": "string", "description": "create: base branch."},
                "body": {"type": "string", "description": "create: PR body."},
                "number": {"type": "number", "description": "merge: PR number."},
                "merge_method": {
                    "type": "string",
                    "description": "merge | squash | rebase. Default squash.",
                },
            },
            "required": ["action", "repo"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        return await _run("github_pr", kwargs)


@dataclass
class GitHubMiscTool(FunctionTool[AstrAgentContext]):
    name: str = "github_misc"
    description: str = (
        "fork (owner/repo is the source; forks into the bot account), "
        "star, or create a branch. action=fork|star|branch."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "fork | star | branch"},
                "owner": {
                    "type": "string",
                    "description": "Source owner for fork/star. Omit for bot login.",
                },
                "repo": {"type": "string", "description": "Repo name or owner/repo."},
                "branch": {"type": "string", "description": "New branch name for branch."},
                "from_branch": {
                    "type": "string",
                    "description": "Source branch for branch. Default default_branch.",
                },
            },
            "required": ["action", "repo"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        return await _run("github_misc", kwargs)


def build_inner_tools() -> list[FunctionTool[AstrAgentContext]]:
    return [
        GitHubWhoamiTool(),
        GitHubRepoTool(),
        GitHubFilesTool(),
        GitHubIssueTool(),
        GitHubPrTool(),
        GitHubMiscTool(),
    ]
