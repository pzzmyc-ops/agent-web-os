"""Environment variable passthrough registry.

Skills that declare ``required_environment_variables`` in their frontmatter
need those vars available in subprocess execution environments (terminal).
By default terminal strips secrets from the child process environment for
security.  This module provides a session-scoped allowlist so skill-declared
vars pass through.

Two sources feed the allowlist:

1. **Skill declarations** — when a skill is loaded, its
   ``required_environment_variables`` are registered here automatically.
2. **User config** — upstream hermes reads ``terminal.env_passthrough`` from
   config.yaml.  mafagent has no config.yaml, so this source always returns
   empty.  The entry point (_load_config_passthrough) is kept as a no-op so
   the code shape stays familiar to future hermes re-porters.

Both ``environments/local.py`` consult :func:`is_env_passthrough` before
stripping a variable.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Iterable

logger = logging.getLogger(__name__)

# Session-scoped set of env var names that should pass through to sandboxes.
# Backed by ContextVar to prevent cross-session data bleed in the gateway pipeline.
_allowed_env_vars_var: ContextVar[set[str]] = ContextVar("_allowed_env_vars")


def _get_allowed() -> set[str]:
    """Get or create the allowed env vars set for the current context/session."""
    try:
        return _allowed_env_vars_var.get()
    except LookupError:
        val: set[str] = set()
        _allowed_env_vars_var.set(val)
        return val


# Cache for the config-based allowlist (loaded once per process).
_config_passthrough: frozenset[str] | None = None


def _is_hermes_provider_credential(name: str) -> bool:
    """True if ``name`` is a Hermes-managed provider credential (API key,
    token, or similar) per ``_HERMES_PROVIDER_ENV_BLOCKLIST``.

    Skill-declared ``required_environment_variables`` frontmatter must
    not be able to override this list — that was the bypass in
    GHSA-rhgp-j443-p4rf where a malicious skill registered
    ``ANTHROPIC_TOKEN`` / ``OPENAI_API_KEY`` as passthrough and received
    the credential in the ``execute_code`` child process, defeating the
    sandbox's scrubbing guarantee.

    Non-Hermes API keys (TENOR_API_KEY, NOTION_TOKEN, etc.) are NOT
    in the blocklist and remain legitimately registerable — skills that
    wrap third-party APIs still work.

    Fail closed: if the authoritative blocklist cannot be imported (partial
    install, import-time error, etc.) we treat the name as a protected
    provider credential and refuse passthrough, rather than fall open and
    let a skill tunnel a Hermes credential into the execute_code child.
    """
    try:
        from .environments.local import (
            _HERMES_PROVIDER_ENV_BLOCKLIST,
            _is_hermes_internal_secret,
        )
    except Exception as e:
        logger.warning(
            "env passthrough: provider credential blocklist import failed; "
            "failing closed and refusing passthrough registration for %r: %s",
            name,
            e,
        )
        return True
    # Dynamically-generated Hermes-internal secrets (AUXILIARY_*_API_KEY /
    # _BASE_URL side-LLM credentials, GATEWAY_RELAY_* relay-auth) are provider
    # credentials the static blocklist can't enumerate — they're injected per
    # task/relay at gateway startup. A skill must not be able to register them
    # as passthrough and tunnel them into an execute_code / terminal child.
    if _is_hermes_internal_secret(name):
        return True
    return name in _HERMES_PROVIDER_ENV_BLOCKLIST


def register_env_passthrough(var_names: Iterable[str]) -> None:
    """Register environment variable names as allowed in sandboxed environments.

    Typically called when a skill declares ``required_environment_variables``.

    Variables that are Hermes-managed provider credentials (from
    ``_HERMES_PROVIDER_ENV_BLOCKLIST``) are rejected here to preserve
    the ``execute_code`` sandbox's credential-scrubbing guarantee per
    GHSA-rhgp-j443-p4rf. A skill that needs to talk to a Hermes-managed
    provider should do so via the agent's main-process tools (web_search,
    web_extract, etc.) where the credential remains safely in the main
    process.

    Non-Hermes third-party API keys (TENOR_API_KEY, NOTION_TOKEN, etc.)
    pass through normally — they were never in the sandbox scrub list.
    """
    for name in var_names:
        name = name.strip()
        if not name:
            continue
        if _is_hermes_provider_credential(name):
            logger.warning(
                "env passthrough: refusing to register Hermes provider "
                "credential %r (blocked by _HERMES_PROVIDER_ENV_BLOCKLIST). "
                "Skills must not override the execute_code sandbox's "
                "credential scrubbing; see GHSA-rhgp-j443-p4rf.",
                name,
            )
            continue
        _get_allowed().add(name)
        logger.debug("env passthrough: registered %s", name)


def _load_config_passthrough() -> frozenset[str]:
    """Load ``terminal.env_passthrough`` from config (cached).

    mafagent has no config.yaml, so the config-based source is always empty.
    Skill-declared passthrough (register_env_passthrough) is the only active
    source.  The function is kept with the same signature and caching shape
    as upstream so the code remains familiar for future re-porting.
    """
    global _config_passthrough
    if _config_passthrough is not None:
        return _config_passthrough
    _config_passthrough = frozenset()
    return _config_passthrough


def is_env_passthrough(var_name: str) -> bool:
    """Check whether *var_name* is allowed to pass through to sandboxes.

    Returns ``True`` if the variable was registered by a skill or listed in
    the user's ``tools.env_passthrough`` config.
    """
    if var_name in _get_allowed():
        return True
    return var_name in _load_config_passthrough()


def get_all_passthrough() -> frozenset[str]:
    """Return the union of skill-registered and config-based passthrough vars."""
    return frozenset(_get_allowed()) | _load_config_passthrough()


def clear_env_passthrough() -> None:
    """Reset the skill-scoped allowlist (e.g. on session reset)."""
    _get_allowed().clear()


