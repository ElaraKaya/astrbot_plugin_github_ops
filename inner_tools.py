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
        "create uses name/description/private. "
        "delete here means delete the whole repository (often disabled); "
        "to delete a file use github_files action=delete."
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
class GitHubLocalTool(FunctionTool[AstrAgentContext]):
    name: str = "github_local"
    description: str = (
        "Read the granted local_dir only. action=list|get. "
        "list applies .gitignore. Paths must stay under that directory. "
        "To push, use github_files local_paths or action=sync; do not paste file bodies."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "list | get",
                },
                "path": {
                    "type": "string",
                    "description": "Relative path under the granted local_dir.",
                },
            },
            "required": ["action"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        return await _run("github_local", kwargs)


@dataclass
class GitHubFilesTool(FunctionTool[AstrAgentContext]):
    name: str = "github_files"
    description: str = (
        "Read or write files in a repo. action=list|get|put|push|delete|sync. "
        "Prefer local_paths / sync; do not paste file bodies. "
        "delete removes one file (NOT the repo). "
        "To delete in push: files=[{path, delete:true}]. Never omit content or pass null. "
        "sync aligns the granted local_dir; whole-tree default also deletes remote extras."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "get", "put", "push", "delete", "sync"],
                    "description": "list | get | put | push | delete | sync",
                },
                "repo": {"type": "string", "description": "Repo name or owner/repo."},
                "owner": {"type": "string", "description": "Owner. Omit for bot login."},
                "path": {
                    "type": "string",
                    "description": "File or directory path. Required for get/put/delete.",
                },
                "content": {
                    "type": "string",
                    "description": "Inline text for put. Skip if using local_path. Do not use null to delete.",
                },
                "local_path": {
                    "type": "string",
                    "description": "Relative path under granted local_dir for put.",
                },
                "local_paths": {
                    "type": "array",
                    "description": "push/sync: relative files or dirs under granted local_dir.",
                    "items": {"type": "string"},
                },
                "message": {"type": "string", "description": "Commit message."},
                "branch": {
                    "type": "string",
                    "description": "Branch or ref. Omit to use the default branch. Do not create extra branches to experiment.",
                },
                "delete_extra": {
                    "type": "boolean",
                    "description": "sync: delete remote files missing locally. Default true for whole-tree, false for specific paths.",
                },
                "files": {
                    "type": "array",
                    "description": "push: [{path, content}] or [{path, delete:true}]. Never omit content to delete.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                            "delete": {
                                "type": "boolean",
                                "description": "If true, delete this path from the repo.",
                            },
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
        "star, create a branch, or delete a branch. "
        "action=fork|star|branch|delete_branch. "
        "delete_branch cannot remove the repository default branch. "
        "Do not create a test branch unless the user asked for one."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["fork", "star", "branch", "delete_branch"],
                    "description": "fork | star | branch | delete_branch",
                },
                "owner": {
                    "type": "string",
                    "description": "Source owner for fork/star. Omit for bot login.",
                },
                "repo": {"type": "string", "description": "Repo name or owner/repo."},
                "branch": {
                    "type": "string",
                    "description": "branch: new name. delete_branch: name to delete (not the default branch).",
                },
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
        GitHubLocalTool(),
        GitHubFilesTool(),
        GitHubIssueTool(),
        GitHubPrTool(),
        GitHubMiscTool(),
    ]
