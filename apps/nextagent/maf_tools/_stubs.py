"""Stubs for hermes_cli.* that mafagent doesn't have.

Each function here represents an upstream hermes feature that is intentionally
absent in mafagent.  Returning a safe default is correct — the callers were
written to handle "this feature is not installed" gracefully.

- load_config / save_config / read_raw_config / cfg_get: hermes config.yaml
  readers.  mafagent uses config.json via apps/nextagent/config.py — these
  two config systems have different keys and schemas, so bridging them would
  do more harm than good.
- get_env_value: upstream reads from config.yaml env overrides.  mafagent
  uses plain os.getenv.
- invoke_hook: hermes hook/plugin system.  mafagent has no hook infrastructure.
- _contains_gateway_lifecycle_command: hermes gateway restart/reload guard.
  mafagent's gateway is a stateless LLM proxy.
- resolve_nous_access_token / get_nous_portal_account_info: Nous Tool Gateway
  (managed cloud execution).  mafagent runs local only.
- apply_terminal_config_to_env: bridges hermes config.yaml ``terminal.*``
  keys into os.environ.  mafagent has no config.yaml — always uses the
  ``TERMINAL_ENV=local`` default in _get_env_config.
- PROVIDER_REGISTRY / OPTIONAL_ENV_VARS / DEFAULT_CONFIG: hermes provider
  credential registry.  mafagent has no third-party provider ecosystem.
"""
def load_config(path=None): return {}
def save_config(config, path=None): pass
def read_raw_config(path=None): return {}
def cfg_get(config, key, default=None): return default
def get_env_value(key, default=None): return __import__("os").getenv(key, default)
def invoke_hook(*a, **kw): pass
def _contains_gateway_lifecycle_command(cmd): return False
def resolve_nous_access_token(): return None
def get_nous_portal_account_info(*a, **kw): return {}
def apply_terminal_config_to_env(env=None, override=False): pass
DEFAULT_CONFIG = {}
OPTIONAL_ENV_VARS = {}
class _FakeProviderRegistry:
    def get(self, k, d=None): return d
    def values(self): return iter([])
PROVIDER_REGISTRY = _FakeProviderRegistry()
