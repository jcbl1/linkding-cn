import json
import os
import shutil
import tempfile

from django.test import TestCase, override_settings

from site_adapters.services.config.loader import (
    _cache,
    _read_builtin_source,
    _read_runtime_defaults,
    load_builtin_config,
)
from site_adapters.views.helpers import (
    _ensure_defaults_adapter,
    _get_defaults_source_path,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BUILTIN_BASELINE = {
    "default": {
        "timeout": 30,
        "http": {"User-Agent": "Mozilla/5.0 Test"},
    },
    "metadata": {
        "select_title": ["h1", "title"],
        "load_full_page": True,
    },
    "snapshot": {
        "process_lazy_images": True,
        "timeout": 120,
    },
    "reader": {"defuddle_args": {}},
}


def _make_source_file(path: str):
    """Write a minimal defaults source template to *path*."""
    data = {
        "_meta": {"id": "defaults", "name": "defaults", "description": "src"},
        "_builtin": _BUILTIN_BASELINE,
        "_builtin_overrides": {},
        "defaults": {},
        "domains": {},
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


# ---------------------------------------------------------------------------
# load_builtin_config
# ---------------------------------------------------------------------------

class LoadBuiltinConfigTestCase(TestCase):
    def setUp(self):
        self.base_dir = tempfile.mkdtemp()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        _cache.invalidate()
        shutil.rmtree(self.base_dir)

    def _runtime_path(self):
        return os.path.join(self.base_dir, "adapters", "defaults", "adapters.jsonc")

    def _write_runtime(self, data: dict):
        path = self._runtime_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")

    # ---- cases ----

    def test_missing_runtime_file_returns_none(self):
        self.assertIsNone(load_builtin_config(self.base_dir))

    def test_empty_builtin_returns_none(self):
        self._write_runtime({"_builtin": {}, "_builtin_overrides": {}})
        self.assertIsNone(load_builtin_config(self.base_dir))

    def test_builtin_without_overrides_returns_as_is(self):
        self._write_runtime(
            {"_builtin": _BUILTIN_BASELINE, "_builtin_overrides": {}}
        )
        result = load_builtin_config(self.base_dir)
        self.assertIsNotNone(result)
        self.assertEqual(result["default"]["timeout"], 30)
        self.assertEqual(result["snapshot"]["timeout"], 120)
        self.assertEqual(result["metadata"]["select_title"], ["h1", "title"])

    def test_overrides_top_level_field(self):
        self._write_runtime(
            {
                "_builtin": _BUILTIN_BASELINE,
                "_builtin_overrides": {"default": {"timeout": 60}},
            }
        )
        result = load_builtin_config(self.base_dir)
        self.assertEqual(result["default"]["timeout"], 60)
        # Other fields still from builtin
        self.assertEqual(result["snapshot"]["timeout"], 120)

    def test_overrides_nested_field(self):
        self._write_runtime(
            {
                "_builtin": _BUILTIN_BASELINE,
                "_builtin_overrides": {"snapshot": {"timeout": 999}},
            }
        )
        result = load_builtin_config(self.base_dir)
        self.assertEqual(result["snapshot"]["timeout"], 999)
        self.assertTrue(result["snapshot"]["process_lazy_images"])

    def test_overrides_adds_new_field(self):
        self._write_runtime(
            {
                "_builtin": _BUILTIN_BASELINE,
                "_builtin_overrides": {"default": {"proxy": "http://p"}},
            }
        )
        result = load_builtin_config(self.base_dir)
        self.assertEqual(result["default"]["proxy"], "http://p")

    def test_corrupted_runtime_file_returns_none(self):
        path = self._runtime_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{{{ not json")
        self.assertIsNone(load_builtin_config(self.base_dir))


# ---------------------------------------------------------------------------
# _read_builtin_source
# ---------------------------------------------------------------------------

class ReadBuiltinSourceTestCase(TestCase):
    def test_source_file_exists_returns_builtin(self):
        result = _read_builtin_source()
        self.assertIsInstance(result, dict)
        for key in ("default", "metadata", "snapshot", "reader"):
            self.assertIn(key, result)
        self.assertEqual(result["default"]["timeout"], 30)
        self.assertTrue(result["snapshot"]["process_lazy_images"])

    def test_source_file_missing_returns_empty(self):
        import site_adapters.services.config.loader as mod
        original = mod._DEFAULTS_SOURCE_FILE
        try:
            mod._DEFAULTS_SOURCE_FILE = "/nonexistent/path/adapters.jsonc"
            result = _read_builtin_source()
            self.assertEqual(result, {})
        finally:
            mod._DEFAULTS_SOURCE_FILE = original


# ---------------------------------------------------------------------------
# _ensure_defaults_adapter
# ---------------------------------------------------------------------------

class EnsureDefaultsAdapterTestCase(TestCase):
    def setUp(self):
        self.adapters_dir = tempfile.mkdtemp()
        self.addCleanup(self.cleanup)
        # Also create a temp dir for the source file so we don't affect
        # the real package source.
        self.source_dir = tempfile.mkdtemp()
        self.source_file = os.path.join(
            self.source_dir, "adapters", "defaults", "adapters.jsonc"
        )
        _make_source_file(self.source_file)
        self.addCleanup(self.cleanup_source)

    def cleanup(self):
        _cache.invalidate()
        shutil.rmtree(self.adapters_dir)

    def cleanup_source(self):
        shutil.rmtree(self.source_dir)

    def _runtime_file(self):
        return os.path.join(self.adapters_dir, "defaults", "adapters.jsonc")

    def _read_runtime(self):
        from site_adapters.services.config import load_jsonc_file
        return load_jsonc_file(self._runtime_file())

    def _call(self):
        with self.settings_source_path():
            _ensure_defaults_adapter(self.adapters_dir)

    def settings_source_path(self):
        """Context manager that patches _get_defaults_source_path."""
        import site_adapters.views.helpers as mod
        original = mod._get_defaults_source_path

        def _patched():
            return self.source_file

        from unittest.mock import patch
        return patch.object(mod, "_get_defaults_source_path", _patched)

    # ---- cases ----

    def test_first_deployment_creates_file_with_all_keys(self):
        self.assertFalse(os.path.exists(self._runtime_file()))
        self._call()
        data = self._read_runtime()
        for key in (
            "_meta", "_builtin", "_builtin_overrides", "defaults", "domains"
        ):
            self.assertIn(key, data, f"missing key: {key}")
        self.assertTrue(data["_builtin"]["snapshot"]["process_lazy_images"])

    def test_first_deployment_also_creates_config_jsonc(self):
        self._call()
        config_path = os.path.join(self.adapters_dir, "config.jsonc")
        self.assertTrue(os.path.exists(config_path))

    def test_resync_overwrites_meta_and_builtin(self):
        # First deploy
        self._call()
        # Tamper with runtime file
        data = self._read_runtime()
        data["_builtin"]["snapshot"]["timeout"] = 1
        data["_meta"]["description"] = "tampered"
        data["defaults"] = {"timeout": 1}
        data["domains"] = {"example.com": {}}
        path = self._runtime_file()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")

        # Re-sync
        self._call()
        data2 = self._read_runtime()
        # Builtin and meta overwritten from source
        self.assertEqual(data2["_builtin"]["snapshot"]["timeout"], 120)
        self.assertEqual(data2["_meta"]["description"], "src")
        # User sections preserved
        self.assertEqual(data2["defaults"]["timeout"], 1)
        self.assertEqual(data2["domains"]["example.com"], {})

    def test_overrides_preserved_after_sync(self):
        self._call()
        # Add a user override
        from site_adapters.services.config.jsonc import update_key
        with open(self._runtime_file(), "r", encoding="utf-8") as f:
            text = f.read()
        text = update_key(text, "_builtin_overrides", {"snapshot": {"timeout": 777}})
        with open(self._runtime_file(), "w", encoding="utf-8") as f:
            f.write(text)

        self._call()
        data = self._read_runtime()
        self.assertEqual(
            data["_builtin_overrides"]["snapshot"]["timeout"], 777
        )

    def test_domains_preserved_after_sync(self):
        self._call()
        data = self._read_runtime()
        data["domains"] = {"example.org": {"metadata": {"select_title": ["h2"]}}}
        with open(self._runtime_file(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")

        self._call()
        data2 = self._read_runtime()
        self.assertEqual(
            data2["domains"]["example.org"]["metadata"]["select_title"], ["h2"]
        )

    def test_missing_source_file_does_not_crash(self):
        self.source_file = "/nonexistent/source.jsonc"
        self._call()
        # The runtime file simply won't be created; no exception raised
        self.assertFalse(os.path.exists(self._runtime_file()))

    def test_source_file_missing_but_runtime_exists_skips_sync(self):
        # First deploy with valid source
        self._call()
        data_before = self._read_runtime()

        # Now remove source, re-call
        import site_adapters.views.helpers as mod
        old_path = mod._get_defaults_source_path

        def _bad_path():
            return "/nonexistent/source.jsonc"

        from unittest.mock import patch
        with patch.object(mod, "_get_defaults_source_path", _bad_path):
            _ensure_defaults_adapter(self.adapters_dir)

        data_after = self._read_runtime()
        self.assertEqual(data_before["_builtin"], data_after["_builtin"])


# ---------------------------------------------------------------------------
# Resolver integration
# ---------------------------------------------------------------------------

class BuiltinResolverIntegrationTestCase(TestCase):
    def setUp(self):
        self.base_dir = tempfile.mkdtemp()
        self.addCleanup(self.cleanup)

        # Write a complete runtime defaults adapter
        runtime_dir = os.path.join(self.base_dir, "adapters", "defaults")
        os.makedirs(runtime_dir, exist_ok=True)
        runtime_data = {
            "_meta": {"id": "defaults", "name": "defaults"},
            "_builtin": _BUILTIN_BASELINE,
            "_builtin_overrides": {},
            "defaults": {},
            "domains": {},
        }
        runtime_path = os.path.join(runtime_dir, "adapters.jsonc")
        with open(runtime_path, "w", encoding="utf-8") as f:
            json.dump(runtime_data, f, indent=2, ensure_ascii=False)
            f.write("\n")

    def cleanup(self):
        _cache.invalidate()
        shutil.rmtree(self.base_dir)

    def _resolver_kwargs(self):
        return {"LD_SITE_ADAPTERS_DIR": self.base_dir}

    def test_metadata_config_falls_back_to_builtin(self):
        with override_settings(**self._resolver_kwargs()):
            from site_adapters.services.config.resolver import get_metadata_config
            config = get_metadata_config("https://unknown.example.com/page")
        self.assertIsNotNone(config)
        self.assertEqual(config["select_title"], ["h1", "title"])
        self.assertTrue(config["load_full_page"])

    def test_snapshot_config_falls_back_to_builtin(self):
        with override_settings(**self._resolver_kwargs()):
            from site_adapters.services.config.resolver import get_snapshot_config
            config = get_snapshot_config("https://unknown.example.com/page")
        self.assertIsNotNone(config)
        self.assertEqual(config["timeout"], 120)
        self.assertTrue(config["process_lazy_images"])

    def test_overrides_affect_resolver_output(self):
        # Write an override
        from site_adapters.services.config.jsonc import update_key
        runtime_path = os.path.join(
            self.base_dir, "adapters", "defaults", "adapters.jsonc"
        )
        with open(runtime_path, "r", encoding="utf-8") as f:
            text = f.read()
        text = update_key(text, "_builtin_overrides", {"snapshot": {"timeout": 999}})
        with open(runtime_path, "w", encoding="utf-8") as f:
            f.write(text)

        with override_settings(**self._resolver_kwargs()):
            from site_adapters.services.config.resolver import get_snapshot_config
            config = get_snapshot_config("https://unknown.example.com/page")
        self.assertEqual(config["timeout"], 999)

    def test_empty_base_dir_returns_none(self):
        import site_adapters.services.config.resolver as mod
        with override_settings(LD_SITE_ADAPTERS_DIR="/nonexistent"):
            self.assertIsNone(mod.get_metadata_config("https://x.com"))
            self.assertIsNone(mod.get_snapshot_config("https://x.com"))
            self.assertIsNone(mod.get_reader_config("https://x.com"))
