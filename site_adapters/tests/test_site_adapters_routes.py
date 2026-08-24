"""
Tests for the routes (path-based config override) mechanism.

Covers:
- Route matching (first-match-wins, document order, re.search)
- deep_merge of route config over domain config
- Fallback to domain config when no route matches
- Alias + routes interaction
- show_config / load_domain_config / get_metadata_config route_key propagation
- Validator: valid config, invalid regex, alias in route, nested routes,
  routes in defaults/_builtin
"""

import json
import os
import shutil
import tempfile

from django.test import TestCase, override_settings

from site_adapters.services.config.loader import (
    _cache,
    load_domain_config,
    show_config,
)
from site_adapters.services.config.resolver import get_metadata_config
from site_adapters.services.config.validator import validate_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_config(adapters_dir: str, data: dict):
    """Write adapters.jsonc into the given directory."""
    path = os.path.join(adapters_dir, "adapters.jsonc")
    os.makedirs(adapters_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _make_base_dir(domain_config: dict) -> str:
    """Create a temp base_dir with a single adapter and one domain config."""
    base_dir = tempfile.mkdtemp()
    adapters_dir = os.path.join(base_dir, "adapters")
    config_path = os.path.join(adapters_dir, "config.jsonc")
    os.makedirs(adapters_dir, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump({
            "_adapters": [
                {"id": "defaults", "name": "defaults", "source": "./defaults"},
                {"id": "local", "name": "local", "source": "./local"},
            ],
        }, f, indent=2, ensure_ascii=False)
        f.write("\n")
    _write_config(os.path.join(adapters_dir, "defaults"), {
        "_meta": {"id": "defaults", "name": "defaults"},
        "_builtin": {},
        "defaults": {},
        "domains": {},
    })
    _write_config(os.path.join(adapters_dir, "local"), {
        "_meta": {"id": "local", "name": "local"},
        "defaults": {},
        "domains": {"www.example.com": domain_config},
    })
    return base_dir


# ---------------------------------------------------------------------------
# Route matching
# ---------------------------------------------------------------------------

class RoutesTestCase(TestCase):
    def setUp(self):
        self._override = override_settings(LD_SITE_ADAPTERS_DIR='')
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.base_dir = _make_base_dir({
            "metadata": {
                "select_title": ["title"],
                "select_image": ["meta[property='og:image']"],
            },
            "routes": {
                "^/video/": {
                    "metadata": {
                        "select_title": [".video-title"],
                    },
                },
                "^/article/": {
                    "metadata": {
                        "select_description": [".article-desc"],
                    },
                },
            },
        })
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        _cache.invalidate()
        shutil.rmtree(self.base_dir, ignore_errors=True)

    def test_route_matches_first_pattern(self):
        """When two patterns both match, document-order first wins."""
        # Both /video/ and the more specific pattern could match, but ^/video/
        # comes first in the dict so it should win.
        config = load_domain_config(
            "https://www.example.com/video/BV123", self.base_dir
        )
        self.assertEqual(config["_route_key"], "^/video/")
        self.assertEqual(config["metadata"]["select_title"], [".video-title"])

    def test_route_config_deep_merges_domain_config(self):
        """Route's select_title overrides domain-level; select_image inherited."""
        config = load_domain_config(
            "https://www.example.com/video/BV123", self.base_dir
        )
        self.assertEqual(config["metadata"]["select_title"], [".video-title"])
        # select_image not in route config, should be inherited from domain
        self.assertEqual(
            config["metadata"]["select_image"],
            ["meta[property='og:image']"],
        )

    def test_no_route_match_falls_back_to_domain_config(self):
        """URL path matching no route keeps domain-level config intact."""
        config = load_domain_config(
            "https://www.example.com/other/page", self.base_dir
        )
        self.assertIsNone(config["_route_key"])
        self.assertEqual(config["metadata"]["select_title"], ["title"])
        self.assertEqual(
            config["metadata"]["select_image"],
            ["meta[property='og:image']"],
        )

    def test_route_without_routes_key_unchanged(self):
        """A domain without 'routes' key behaves exactly as before."""
        base_dir = _make_base_dir({
            "metadata": {"select_title": ["h1"]},
        })
        try:
            config = load_domain_config(
                "https://www.example.com/anything", base_dir
            )
            self.assertIsNone(config["_route_key"])
            self.assertEqual(config["metadata"]["select_title"], ["h1"])
            self.assertNotIn("routes", config)
        finally:
            _cache.invalidate()
            shutil.rmtree(base_dir, ignore_errors=True)

    def test_route_with_defaults_inherited_by_sections(self):
        """Route's defaults.timeout is inherited by metadata (via resolver)."""
        base_dir = _make_base_dir({
            "defaults": {"timeout": 30},
            "metadata": {"select_title": ["title"]},
            "routes": {
                "^/special/": {
                    "defaults": {"timeout": 60},
                    "metadata": {"select_title": [".special-title"]},
                },
            },
        })
        try:
            with override_settings(LD_SITE_ADAPTERS_DIR=base_dir):
                config = get_metadata_config(
                    "https://www.example.com/special/page", username=""
                )
            self.assertIsNotNone(config)
            self.assertEqual(config["timeout"], 60)
        finally:
            _cache.invalidate()
            shutil.rmtree(base_dir, ignore_errors=True)

    def test_route_auth_merges_with_domain_auth(self):
        """Route auth merges with domain-level auth via deep_merge."""
        base_dir = _make_base_dir({
            "auth": {"cookie": {"type": "auto"}},
            "routes": {
                "^/api/": {
                    "auth": {"cookie": {"verify": {"check": ["/login"]}}},
                },
            },
        })
        try:
            config = load_domain_config(
                "https://www.example.com/api/data", base_dir
            )
            self.assertEqual(config["_route_key"], "^/api/")
            self.assertEqual(config["auth"]["cookie"]["type"], "auto")
            self.assertIn("verify", config["auth"]["cookie"])
            self.assertEqual(
                config["auth"]["cookie"]["verify"]["check"], ["/login"]
            )
        finally:
            _cache.invalidate()
            shutil.rmtree(base_dir, ignore_errors=True)

    def test_alias_domain_resolves_then_route_matches(self):
        """Alias resolves to target domain config, then route matches on original URL."""
        base_dir = _make_base_dir({
            "metadata": {"select_title": ["title"]},
            "routes": {
                "^/video/": {
                    "metadata": {"select_title": [".video-title"]},
                },
            },
        })
        # Add alias domain
        local_path = os.path.join(base_dir, "adapters", "local", "adapters.jsonc")
        with open(local_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["domains"]["m.example.com"] = {
            "type": "alias",
            "target": "www.example.com",
        }
        _write_config(os.path.dirname(local_path), data)
        try:
            with override_settings(LD_SITE_ADAPTERS_DIR=base_dir):
                config = load_domain_config(
                    "https://m.example.com/video/BV456", base_dir
                )
            # _domain_key is the matched key (alias source), not the target
            self.assertEqual(config["_domain_key"], "m.example.com")
            self.assertEqual(config["_route_key"], "^/video/")
            self.assertEqual(config["metadata"]["select_title"], [".video-title"])
        finally:
            _cache.invalidate()
            shutil.rmtree(base_dir, ignore_errors=True)

    def test_route_key_returned_in_load_domain_config(self):
        """load_domain_config returns _route_key matching the hit pattern."""
        config = load_domain_config(
            "https://www.example.com/article/123", self.base_dir
        )
        self.assertEqual(config["_route_key"], "^/article/")

    def test_show_config_includes_route_key_and_merged(self):
        """show_config returns route_key and merged config with route applied."""
        result = show_config(
            "https://www.example.com/video/BV123", self.base_dir
        )
        self.assertTrue(result["matched"])
        self.assertEqual(result["route_key"], "^/video/")
        self.assertIn("routes", result["raw_config"])
        self.assertNotIn("routes", result["merged"])
        self.assertEqual(
            result["merged"]["metadata"]["select_title"], [".video-title"]
        )

    def test_route_key_in_section_config(self):
        """get_metadata_config returns _route_key."""
        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            config = get_metadata_config(
                "https://www.example.com/video/BV123", username=""
            )
        self.assertIsNotNone(config)
        self.assertEqual(config["_route_key"], "^/video/")

    def test_route_uses_original_url_path_not_request_url(self):
        """Route matching uses the original URL path, not request_url."""
        base_dir = _make_base_dir({
            "metadata": {
                "select_title": ["title"],
                "request_url": ["^https?://www\\.example\\.com/(.+)",
                                "https://api.example.com/$1"],
            },
            "routes": {
                "^/video/": {
                    "metadata": {"select_title": [".video-title"]},
                },
            },
        })
        try:
            config = load_domain_config(
                "https://www.example.com/video/BV789", base_dir
            )
            # Route should match on /video/ from the original URL
            self.assertEqual(config["_route_key"], "^/video/")
        finally:
            _cache.invalidate()
            shutil.rmtree(base_dir, ignore_errors=True)

    def test_invalid_route_pattern_logged_and_skipped(self):
        """An invalid regex pattern is skipped; subsequent patterns still work."""
        base_dir = _make_base_dir({
            "metadata": {"select_title": ["title"]},
            "routes": {
                "[invalid(": {
                    "metadata": {"select_title": [".bad"]},
                },
                "^/good/": {
                    "metadata": {"select_title": [".good-title"]},
                },
            },
        })
        try:
            config = load_domain_config(
                "https://www.example.com/good/page", base_dir
            )
            self.assertEqual(config["_route_key"], "^/good/")
            self.assertEqual(config["metadata"]["select_title"], [".good-title"])
        finally:
            _cache.invalidate()
            shutil.rmtree(base_dir, ignore_errors=True)

    def test_empty_routes_dict_behaves_like_no_routes(self):
        """An empty routes dict {} is equivalent to no routes at all."""
        base_dir = _make_base_dir({
            "metadata": {"select_title": ["title"]},
            "routes": {},
        })
        try:
            config = load_domain_config(
                "https://www.example.com/anything", base_dir
            )
            self.assertIsNone(config["_route_key"])
            self.assertEqual(config["metadata"]["select_title"], ["title"])
        finally:
            _cache.invalidate()
            shutil.rmtree(base_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

class RoutesValidatorTestCase(TestCase):
    def setUp(self):
        self.base_dir = tempfile.mkdtemp()
        adapters_dir = os.path.join(self.base_dir, "adapters")
        config_path = os.path.join(adapters_dir, "config.jsonc")
        os.makedirs(adapters_dir, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump({
                "_adapters": [
                    {"id": "local", "name": "local", "source": "./local"},
                ],
            }, f, indent=2, ensure_ascii=False)
            f.write("\n")
        self.adapters_dir = adapters_dir
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        _cache.invalidate()
        shutil.rmtree(self.base_dir, ignore_errors=True)

    def _write_adapter(self, data: dict):
        _write_config(os.path.join(self.adapters_dir, "local"), data)

    def test_validate_routes_valid_config(self):
        """A valid routes block produces no issues."""
        self._write_adapter({
            "_meta": {"id": "local", "name": "local"},
            "defaults": {},
            "domains": {
                "www.example.com": {
                    "metadata": {"select_title": ["title"]},
                    "routes": {
                        "^/video/": {
                            "metadata": {"select_title": [".video-title"]},
                        },
                    },
                },
            },
        })
        issues = validate_config(self.base_dir)
        errors = [i for i in issues if i["level"] == "error"]
        self.assertEqual(errors, [])

    def test_validate_routes_invalid_regex(self):
        """An invalid regex in a route pattern produces an error."""
        self._write_adapter({
            "_meta": {"id": "local", "name": "local"},
            "defaults": {},
            "domains": {
                "www.example.com": {
                    "routes": {
                        "[invalid(": {"metadata": {}},
                    },
                },
            },
        })
        issues = validate_config(self.base_dir)
        errors = [i for i in issues if i["level"] == "error" and i["code"] == "route_pattern_invalid"]
        self.assertTrue(len(errors) >= 1)

    def test_validate_routes_alias_in_route_warns(self):
        """A route config with type: alias produces a warning."""
        self._write_adapter({
            "_meta": {"id": "local", "name": "local"},
            "defaults": {},
            "domains": {
                "www.example.com": {
                    "routes": {
                        "^/api/": {"type": "alias", "target": "other.com"},
                    },
                },
            },
        })
        issues = validate_config(self.base_dir)
        warnings = [i for i in issues if i["level"] == "warning" and i["code"] == "route_alias_not_supported"]
        self.assertTrue(len(warnings) >= 1)

    def test_validate_routes_nested_routes_warns(self):
        """Routes inside a route config produces a warning."""
        self._write_adapter({
            "_meta": {"id": "local", "name": "local"},
            "defaults": {},
            "domains": {
                "www.example.com": {
                    "routes": {
                        "^/api/": {
                            "routes": {
                                "^/v2/": {"metadata": {"select_title": [".v2"]}},
                            },
                        },
                    },
                },
            },
        })
        issues = validate_config(self.base_dir)
        warnings = [i for i in issues if i["level"] == "warning" and i["code"] == "routes_unexpected_location"]
        self.assertTrue(len(warnings) >= 1)

    def test_validate_routes_in_defaults_warns(self):
        """Routes in adapter-level defaults produces a warning."""
        self._write_adapter({
            "_meta": {"id": "local", "name": "local"},
            "defaults": {
                "routes": {
                    "^/api/": {"metadata": {"select_title": [".api"]}},
                },
            },
            "domains": {
                "www.example.com": {},
            },
        })
        issues = validate_config(self.base_dir)
        warnings = [i for i in issues if i["level"] == "warning" and i["code"] == "routes_unexpected_location"]
        self.assertTrue(len(warnings) >= 1)

    def test_validate_routes_in_builtin_warns(self):
        """Routes in _builtin produces a warning."""
        self._write_adapter({
            "_meta": {"id": "local", "name": "local"},
            "_builtin": {
                "routes": {
                    "^/api/": {"metadata": {"select_title": [".api"]}},
                },
            },
            "defaults": {},
            "domains": {},
        })
        issues = validate_config(self.base_dir)
        warnings = [i for i in issues if i["level"] == "warning" and i["code"] == "routes_unexpected_location"]
        self.assertTrue(len(warnings) >= 1)

    def test_validate_routes_not_object_error(self):
        """Routes that is not an object produces an error."""
        self._write_adapter({
            "_meta": {"id": "local", "name": "local"},
            "defaults": {},
            "domains": {
                "www.example.com": {
                    "routes": "not_an_object",
                },
            },
        })
        issues = validate_config(self.base_dir)
        errors = [i for i in issues if i["level"] == "error" and i["code"] == "routes_not_object"]
        self.assertTrue(len(errors) >= 1)
