import json
import os
import shutil
import tempfile
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings

from site_adapters.services.config.loader import _cache


class SiteAdaptersCommandTestCase(TestCase):
    def setUp(self):
        _cache.invalidate()
        self.base_dir = tempfile.mkdtemp()
        self.settings_override = override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir)
        self.settings_override.enable()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        _cache.invalidate()
        self.settings_override.disable()
        shutil.rmtree(self.base_dir)

    def write(self, relpath, content):
        path = os.path.join(self.base_dir, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_validate_uses_configured_site_adapters_dir(self):
        self.write("domains/example.com.jsonc", '{"metadata": {"select_title": ["h1"]}}')
        out = StringIO()

        call_command("site_adapter", "validate", stdout=out)

        self.assertIn("site adapters ok", out.getvalue())

    def test_show_config_outputs_merged_config(self):
        self.write("global.jsonc", '{"*": {"http": {"timeout": 9}}}')
        self.write("domains/example.com.jsonc", '{"http": {"timeout": 1}}')
        out = StringIO()

        call_command("site_adapter", "show-config", "https://example.com/post", "--dir", self.base_dir, stdout=out)

        result = json.loads(out.getvalue())
        self.assertEqual(result["domain_key"], "example.com")
        self.assertEqual(result["merged"]["http"]["timeout"], 9)

    def test_validate_subscription_reports_local_file_errors(self):
        path = self.write("bundle.jsonc", '{"domains": {"bad.com": {"type": "alias"}}}')
        out = StringIO()

        call_command("site_adapter", "validate-subscription", path, stdout=out)

        self.assertIn("bad.com alias missing target", out.getvalue())

    def test_cookie_command_refreshes_when_cookie_is_missing(self):
        self.write(
            "domains/example.com.jsonc",
            '{"auth": {"cookie": {"type": "anon", "refresh": {"url": "https://example.com/login"}}},'
            '"snapshot": {}}',
        )

        def refresh_cookie_declarative(_refresh_config, _url, cookie_file, _domain_key):
            os.makedirs(os.path.dirname(cookie_file), exist_ok=True)
            with open(cookie_file, "w", encoding="utf-8") as f:
                json.dump([{"name": "session", "value": "abc", "domain": "example.com"}], f)
            return True

        out = StringIO()
        with mock.patch("site_adapters.services.auth.cookies.refresh_cookie_declarative", side_effect=refresh_cookie_declarative) as refresh:
            call_command(
                "site_adapter",
                "cookie",
                "https://example.com/post",
                "--section",
                "snapshot",
                stdout=out,
            )

        refresh.assert_called_once()
        result = json.loads(out.getvalue())
        self.assertTrue(result["has_cookie"])
        self.assertTrue(result["refreshed"])
