from __future__ import annotations

import re
import urllib.parse

_DASH5 = "-" * 5
_BEGIN_PK = _DASH5 + r"BEGIN (?:[A-Z0-9_-]+ )?PRIVATE KEY" + _DASH5
_BEGIN_OPENSSH = _DASH5 + r"BEGIN OPENSSH PRIVATE KEY" + _DASH5

SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "GitHub Personal Access Token (ghp_)"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "GitHub Fine-Grained Token (github_pat_)"),
    (re.compile(r"\bgho_[A-Za-z0-9]{20,}\b"), "GitHub OAuth Token (gho_)"),
    (re.compile(r"\bghu_[A-Za-z0-9]{20,}\b"), "GitHub User-to-Server Token (ghu_)"),
    (re.compile(r"\bghs_[A-Za-z0-9]{20,}\b"), "GitHub Server-to-Server Token (ghs_)"),
    (re.compile(r"\bghr_[A-Za-z0-9]{20,}\b"), "GitHub Refresh Token (ghr_)"),
    (re.compile(r"\bsk-proj-[A-Za-z0-9_-]{20,}\b"), "OpenAI Project Key (sk-proj-)"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"), "Anthropic Key (sk-ant-)"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "Google API Key (AIza)"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS Access Key ID (AKIA)"),
    (re.compile(_BEGIN_PK), "Private Key"),
    (re.compile(_BEGIN_OPENSSH), "OpenSSH Private Key"),
]

REDACTED_TAG = "[REDACTED]"


def extract_proxy_credentials(proxy_url: str) -> list[str]:
    credentials: list[str] = []
    if not proxy_url or not isinstance(proxy_url, str):
        return credentials
    try:
        parsed = urllib.parse.urlsplit(proxy_url.strip())
        if parsed.password:
            credentials.append(parsed.password)
        if parsed.username and parsed.password:
            credentials.append(f"{parsed.username}:{parsed.password}")
    except Exception:
        pass
    return [c for c in credentials if c.strip()]


def find_secret(
    text: str,
    *,
    token: str = "",
    extra_needles: list[str] | None = None,
) -> str | None:
    if not text or not isinstance(text, str):
        return None

    tok = (token or "").strip()
    if tok and tok in text:
        return "检测到配置的 GitHub Token"

    if extra_needles:
        for needle in extra_needles:
            n = (needle or "").strip()
            if n and n in text:
                return "检测到账号私人邮箱或敏感凭据"

    for pattern, name in SECRET_PATTERNS:
        if pattern.search(text):
            return f"检测到敏感凭据 ({name})"

    return None


def redact_text(
    text: str,
    *,
    token: str = "",
    extra_needles: list[str] | None = None,
) -> str:
    if not text or not isinstance(text, str):
        return text or ""

    result = text

    tok = (token or "").strip()
    if tok and tok in result:
        result = result.replace(tok, REDACTED_TAG)

    if extra_needles:
        for needle in extra_needles:
            n = (needle or "").strip()
            if n and n in result:
                result = result.replace(n, REDACTED_TAG)

    for pattern, _ in SECRET_PATTERNS:
        result = pattern.sub(REDACTED_TAG, result)

    return result
