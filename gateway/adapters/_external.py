from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from appconfig import load_config

_NS = "gateway_ext_adapter"


def load_external_modules(*, reload: bool = False) -> list[ModuleType]:
    directory = Path(load_config().adapters_dir)
    directory.mkdir(parents=True, exist_ok=True)
    modules: list[ModuleType] = []
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        mod_name = f"{_NS}__{path.stem}"
        if not reload and mod_name in sys.modules:
            modules.append(sys.modules[mod_name])
            continue
        sys.modules.pop(mod_name, None)
        spec = importlib.util.spec_from_file_location(mod_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        modules.append(module)
    return modules
