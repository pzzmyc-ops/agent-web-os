from __future__ import annotations

from urllib.parse import urlparse

CHAT = "chat"
RESPONSES = "responses"
ANTHROPIC = "anthropic"
ALL = (CHAT, RESPONSES, ANTHROPIC)

_remembered: dict[tuple[str, str], str] = {}


def _hostname(base_url: str) -> str:
    return (urlparse(base_url).hostname or "").lower()


def detect(base_url: str, model: str) -> str:
    url = base_url.rstrip("/").lower()
    host = _hostname(base_url)
    mid = (model or "").strip().lower()
    if host == "api.kimi.com" and "/coding" in url:
        return ANTHROPIC
    if host == "api.anthropic.com" or url.endswith("/anthropic"):
        return ANTHROPIC
    if host == "api.openai.com":
        return RESPONSES
    if url.endswith("/responses"):
        return RESPONSES
    if mid.startswith("anthropic/") or mid.startswith("claude"):
        return ANTHROPIC
    return CHAT


def candidates(base_url: str, model: str) -> list[str]:
    known = _remembered.get((base_url, model))
    first = known or detect(base_url, model)
    order = [first]
    for p in ALL:
        if p not in order:
            order.append(p)
    return order


def remember(base_url: str, model: str, protocol: str) -> None:
    _remembered[(base_url, model)] = protocol


def forget_all() -> None:
    _remembered.clear()


def endpoint(base_url: str, protocol: str) -> str:
    base = base_url.rstrip("/")
    if protocol == CHAT:
        return base + "/chat/completions"
    if protocol == RESPONSES:
        if base.endswith("/responses"):
            return base
        return base + "/responses"
    if protocol == ANTHROPIC:
        if base.endswith("/v1"):
            return base + "/messages"
        return base + "/v1/messages"
    raise RuntimeError("未知协议: " + protocol)


_MISMATCH_MARKERS = (
    "unrecognized request argument",
    "unknown field",
    "unknown parameter",
    "unsupported parameter",
    "unexpected field",
    "extra inputs are not permitted",
    "'messages' is a required",
    "\"messages\" is a required",
    "messages: field required",
    "'input' is a required",
    "\"input\" is a required",
    "input: field required",
    "max_tokens: field required",
    "not found",
    "no route",
    "cannot post",
)


def is_mismatch(status: int, body_text: str) -> bool:
    if status in (404, 405):
        return True
    if status not in (400, 415, 422):
        return False
    low = (body_text or "").lower()
    return any(marker in low for marker in _MISMATCH_MARKERS)
