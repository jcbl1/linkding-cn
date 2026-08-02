import os
import shutil
import tempfile
import time

from django.test import TestCase, override_settings

from site_adapters.services.config.loader import (
    _cache,
    load_domain_config,
)
from site_adapters.services.config import parse_jsonc
from site_adapters.services.config.resolver import get_metadata_config
from site_adapters.services.auth.tokens import _resolve_json_path


class SiteAdaptersEngineTestCase(TestCase):
    def setUp(self):
        self.base_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.base_dir, "domains"))
        self.addCleanup(self.cleanup)

    def cleanup(self):
        _cache.invalidate()
        shutil.rmtree(self.base_dir)

    def write(self, relpath, content):
        path = os.path.join(self.base_dir, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_parse_jsonc_keeps_urls_inside_strings(self):
        data = parse_jsonc('{"url": "https://example.com/a", "items": [1,], // comment\n}')

        self.assertEqual(data["url"], "https://example.com/a")
        self.assertEqual(data["items"], [1])

    def test_file_content_change_invalidates_cache(self):
        self.write("global.jsonc", "{}")
        path = self.write("domains/example.com.jsonc", '{"http": {"timeout": 1}}')

        self.assertEqual(load_domain_config("https://example.com", self.base_dir)["http"]["timeout"], 1)

        time.sleep(0.1)
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"http": {"timeout": 2}}')
        _cache._last_check = 0  # force re-check

        self.assertEqual(load_domain_config("https://example.com", self.base_dir)["http"]["timeout"], 2)

    def test_local_global_defaults_have_highest_priority(self):
        self.write("global.jsonc", '{"*": {"http": {"timeout": 9}}}')
        self.write("domains/example.com.jsonc", '{"http": {"timeout": 1}}')

        config = load_domain_config("https://example.com", self.base_dir)

        self.assertEqual(config["http"]["timeout"], 9)

    def test_subscriptions_follow_global_order(self):
        import json
        self.write(
            "global.jsonc",
            '{"_subscriptions": [{"name": "first"}, {"name": "second"}]}',
        )
        # New single-file format
        self.write("subscriptions/first/subscription.jsonc", json.dumps({
            "_meta": {"name": "first", "version": 1},
            "domains": {"example.com": {"http": {"timeout": 1}}}
        }))
        self.write("subscriptions/second/subscription.jsonc", json.dumps({
            "_meta": {"name": "second", "version": 1},
            "domains": {"example.com": {"http": {"timeout": 2}}}
        }))

        config = load_domain_config("https://example.com", self.base_dir)

        self.assertEqual(config["http"]["timeout"], 1)

    def test_alias_domain_resolves_target_config(self):
        self.write("domains/target.com.jsonc", '{"metadata": {"select_title": [".target"]}}')
        self.write("domains/alias.com.jsonc", '{"type": "alias", "target": "target.com"}')

        config = load_domain_config("https://alias.com/post", self.base_dir)

        self.assertEqual(config["_domain_key"], "alias.com")
        self.assertEqual(config["metadata"]["select_title"], [".target"])

    def test_alias_loop_returns_no_config(self):
        self.write("domains/a.com.jsonc", '{"type": "alias", "target": "b.com"}')
        self.write("domains/b.com.jsonc", '{"type": "alias", "target": "a.com"}')

        self.assertIsNone(load_domain_config("https://a.com/post", self.base_dir))

    def test_relative_paths_resolve_from_domain_file_directory(self):
        self.write("domains/example.com.jsonc", '{"metadata": {"script": "./metadata.js"}}')

        config = load_domain_config("https://example.com/post", self.base_dir)

        self.assertEqual(config["metadata"]["script"], os.path.realpath(os.path.join(self.base_dir, "domains", "metadata.js")))

    def test_relative_non_script_strings_are_not_resolved(self):
        self.write(
            "domains/example.com.jsonc",
            '{"metadata": {"select_title": ["./article"], "request_url": ["../post", "api"]}}',
        )

        config = load_domain_config("https://example.com/post", self.base_dir)

        self.assertEqual(config["metadata"]["select_title"], ["./article"])
        self.assertEqual(config["metadata"]["request_url"], ["../post", "api"])

    def test_resolve_json_path_supports_array_index(self):
        data = {"data": [{"token": "abc123"}]}

        self.assertEqual(_resolve_json_path(data, "data[0].token"), "abc123")

    def test_resolver_merges_http_and_handles_auth_config(self):
        self.write(
            "domains/example.com.jsonc",
            """
            {
              "auth": {
                "cookie": {
                  "type": "anon"
                }
              },
              "default": {
                "timeout": 5,
                "http": {
                  "Cookie": "ignored",
                  "X-Test": "domain"
                }
              },
              "metadata": {
                "select_title": ["h1"],
                "request_url": ["post/(\\\\d+)", "api/post/\\\\1"],
                "rewrite_url": ["post/(\\\\d+)", "article/\\\\1"],
                "http": {
                  "X-Test": "section",
                  "Accept": "text/html"
                }
              }
            }
            """,
        )

        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            config = get_metadata_config("https://example.com/post/123")

        self.assertEqual(config["timeout"], 5)
        # Cookie file is auto-derived from domain key
        self.assertEqual(config["cookie"]["file"], os.path.join(self.base_dir, "cookies", "example.com.json"))
        self.assertNotIn("Cookie", config["headers"])
        self.assertEqual(config["headers"]["X-Test"], "section")
        self.assertEqual(config["headers"]["Accept"], "text/html")
        self.assertEqual(config["_request_url"], "https://example.com/api/post/123")
        self.assertEqual(config["_rewrite_url"], "https://example.com/article/123")

class ExecutionLogTestCase(TestCase):
    def test_redact_cmd_args_masks_cookie_file(self):
        from site_adapters.services.execution_log import _redact_cmd_args
        args = ['single-file', '--browser-cookies-file=/tmp/secret.json', '--user-agent=UA']
        result = _redact_cmd_args(args)
        self.assertEqual(result[1], '--browser-cookies-file=[redacted]')
        self.assertEqual(result[2], '--user-agent=UA')

    def test_redact_cmd_args_leaves_normal_args_unchanged(self):
        from site_adapters.services.execution_log import _redact_cmd_args
        args = ['single-file', '--browser-script=/tmp/s.js', '--http-header=X: Y']
        result = _redact_cmd_args(args)
        self.assertEqual(result, args)


class ApplyTogglesTestCase(TestCase):
    def test_apply_toggles_returns_original_when_no_toggles(self):
        from site_adapters.services.config.resolver import _apply_toggles
        section = {"remove_elements": [".ad"], "keep_elements": [".article"]}
        remove, keep = _apply_toggles(section, {"_domain_key": "example.com"}, "user")
        self.assertEqual(remove, [".ad"])
        self.assertEqual(keep, [".article"])

    def test_apply_toggles_adds_default_remove(self):
        from site_adapters.services.config.resolver import _apply_toggles
        section = {
            "toggles": {
                "hide_sidebar": {"selector": ".sidebar", "label": "Hide sidebar", "default": True}
            }
        }
        remove, keep = _apply_toggles(section, {"_domain_key": "example.com"}, "user")
        self.assertIn(".sidebar", remove)
        self.assertNotIn(".sidebar", keep)

    def test_apply_toggles_respects_user_preference_over_default(self):
        from site_adapters.services.config.resolver import _apply_toggles
        from site_adapters.services.auth.credentials import save_user_preferences
        section = {
            "toggles": {
                "hide_sidebar": {"selector": ".sidebar", "default": True}
            }
        }
        # User explicitly chose to keep it
        save_user_preferences("testuser", "example.com", "hide_sidebar", False)
        remove, keep = _apply_toggles(section, {"_domain_key": "example.com"}, "testuser")
        self.assertNotIn(".sidebar", remove)
        self.assertIn(".sidebar", keep)
