"""把前端那组状态机测试挂进 python 的测试入口。

前端是浏览器 ES 模块,但「回合结束谁说了算」这条状态机是纯逻辑,可以在 node 里
带着最小 DOM 桩真跑一遍(见 js_tests/turn-state.test.mjs)。这里只负责找到 node、
跑一遍、把输出原样带出来,让 `unittest discover` 一次覆盖前后端。
"""
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TEST_FILE = _HERE / "js_tests" / "turn-state.test.mjs"

#: 这台机器上没有全局 node,只有几个依赖自带的 node 二进制。
_FALLBACK_NODE_PATHS = (
    Path(r"E:/mafagent/python/Lib/site-packages/playwright/driver/node.exe"),
    Path(r"E:/mafagent/python/Lib/site-packages/cursor_sdk/_vendor/bridge/bin/node.exe"),
)


def _find_node() -> str | None:
    explicit = os.environ.get("MAFAGENT_NODE")
    if explicit and Path(explicit).exists():
        return explicit
    found = shutil.which("node")
    if found:
        return found
    for candidate in _FALLBACK_NODE_PATHS:
        if candidate.exists():
            return str(candidate)
    return None


class FrontendTurnStateTests(unittest.TestCase):
    def test_frontend_turn_state_machine(self) -> None:
        node = _find_node()
        if node is None:
            self.skipTest("没有可用的 node,跳过前端状态机测试")
        result = subprocess.run(
            [node, str(_TEST_FILE)],
            cwd=str(_HERE),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            self.fail("前端状态机测试失败:\n" + output)
        self.assertIn("passed", output)


if __name__ == "__main__":
    unittest.main()
