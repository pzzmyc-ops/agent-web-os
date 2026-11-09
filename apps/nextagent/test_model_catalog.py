import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_PROJECT = Path(__file__).resolve().parents[2]
if str(_PROJECT / "vendor") not in sys.path:
    sys.path.insert(0, str(_PROJECT / "vendor"))

from appconfig import normalize_upstreams
from apps.nextagent.config import resolve_default_chat_model
from apps.nextagent.upstreams.catalog import model_catalog, model_id, resolve_model

RAW = [
    {"name": "公司网关", "base_url": "http://gw.local/v1", "api_key": "k1", "models": ["gpt-6-sol", "deepseek-flash"]},
    {"name": "白湾", "base_url": "https://whitebay.click/v1", "api_key": "k2", "models": ["gpt-6-sol"]},
    {"name": "claude fable 5", "base_url": "https://whitebay.click/v1", "api_key": "k2", "models": ["claude-fable-5-claude-prime"]},
]


def _config():
    return SimpleNamespace(upstreams=normalize_upstreams(RAW))


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.cfg = patch("apps.nextagent.upstreams.catalog.load_config", _config)
        self.cfg.start()

    def tearDown(self):
        self.cfg.stop()

    def test_same_model_may_live_in_two_upstreams(self):
        clean = normalize_upstreams(RAW)
        self.assertEqual([u["models"] for u in clean], [["gpt-6-sol", "deepseek-flash"], ["gpt-6-sol"], ["claude-fable-5-claude-prime"]])

    def test_upstream_names_still_must_be_unique(self):
        with self.assertRaises(RuntimeError):
            normalize_upstreams([RAW[1], dict(RAW[1], base_url="http://other/v1")])

    def test_catalog_ids_are_unique_and_display_the_upstream_name(self):
        rows = model_catalog()
        ids = [r["id"] for r in rows]
        self.assertEqual(len(ids), len(set(ids)))
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(by_id[model_id("白湾", "gpt-6-sol")]["displayName"], "白湾")
        self.assertEqual(by_id[model_id("claude fable 5", "claude-fable-5-claude-prime")]["displayName"], "claude fable 5")
        self.assertEqual(by_id[model_id("公司网关", "gpt-6-sol")]["displayName"], "公司网关 · gpt-6-sol")
        self.assertEqual(by_id[model_id("公司网关", "deepseek-flash")]["displayName"], "公司网关 · deepseek-flash")
        self.assertEqual(by_id[model_id("白湾", "gpt-6-sol")]["model"], "gpt-6-sol")
        self.assertEqual(by_id[model_id("白湾", "gpt-6-sol")]["provider"], "白湾")

    def test_resolve_returns_the_right_upstream_and_raw_model(self):
        up, model = resolve_model(model_id("白湾", "gpt-6-sol"))
        self.assertEqual((up.name, model), ("白湾", "gpt-6-sol"))
        up, model = resolve_model(model_id("公司网关", "gpt-6-sol"))
        self.assertEqual((up.name, model), ("公司网关", "gpt-6-sol"))

    def test_resolve_rejects_bare_model_names_and_unknown_ids(self):
        with self.assertRaises(RuntimeError):
            resolve_model("gpt-6-sol")
        with self.assertRaises(RuntimeError):
            resolve_model(model_id("白湾", "deepseek-flash"))
        with self.assertRaises(RuntimeError):
            resolve_model("")

    def test_default_model_keeps_a_known_id(self):
        want = model_id("白湾", "gpt-6-sol")
        self.assertEqual(resolve_default_chat_model(want)[0], want)

    def test_legacy_bare_name_maps_to_its_only_upstream(self):
        chosen, ids = resolve_default_chat_model("claude-fable-5-claude-prime")
        self.assertEqual(chosen, model_id("claude fable 5", "claude-fable-5-claude-prime"))
        self.assertEqual(ids, [r["id"] for r in model_catalog()])

    def test_legacy_bare_name_with_two_owners_falls_to_the_default(self):
        self.assertEqual(resolve_default_chat_model("gpt-6-sol")[0], model_id("公司网关", "gpt-6-sol"))

    def test_unknown_name_falls_to_the_default(self):
        self.assertEqual(resolve_default_chat_model("nope")[0], model_id("公司网关", "gpt-6-sol"))


if __name__ == "__main__":
    unittest.main()
