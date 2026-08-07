import json
import os
import shutil
import tempfile
from unittest import mock

from django.test import TestCase, override_settings

from site_adapters.services.subscriptions import (
    fetch_subscription,
    validate_subscription_url,
)


class SiteAdaptersSubscriptionsTestCase(TestCase):
    def setUp(self):
        self.base_dir = tempfile.mkdtemp()
        self.settings_override = override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir)
        self.settings_override.enable()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        from site_adapters.services.config.loader import _cache
        _cache.invalidate()
        self.settings_override.disable()
        shutil.rmtree(self.base_dir)

    def response(self, payload):
        resp = mock.Mock()
        resp.status_code = 200
        resp.text = json.dumps(payload)
        resp.headers = {}
        resp.raise_for_status.return_value = None
        return resp


    def test_fetch_subscription_preserves_string_aliases(self):
        payload = {
            "_meta": {"name": "bundle", "version": 1},
            "domains": {
                "target.com": {"metadata": {"select_title": ["h1"]}},
                "alias.com": "target.com",
            },
        }

        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            return_value=self.response(payload),
        ):
            file_path = fetch_subscription("https://example.test/bundle.jsonc", name="bundle", force=True)

        data = json.loads(open(file_path, encoding="utf-8").read())
        alias_config = data["domains"]["alias.com"]
        # String aliases are preserved as-is in single-file format
        self.assertEqual(alias_config, "target.com")

    def test_validate_subscription_url_rejects_private_hosts(self):
        with self.assertRaises(ValueError):
            validate_subscription_url("https://127.0.0.1/bundle.jsonc")

    def test_force_fetch_failure_returns_none(self):
        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            side_effect=Exception("boom"),
        ):
            self.assertIsNone(fetch_subscription("https://example.test/bundle.jsonc", name="bundle", force=True))

    def test_resolve_script_ref_https_returns_url_and_filename(self):
        from site_adapters.services.subscriptions import _resolve_script_ref
        url, name = _resolve_script_ref("https://cdn.example.com/scripts/clean.js", "https://base.test/sub.jsonc")
        self.assertEqual(url, "https://cdn.example.com/scripts/clean.js")
        self.assertEqual(name, "clean.js")

    def test_resolve_script_ref_relative_resolves_against_base(self):
        from site_adapters.services.subscriptions import _resolve_script_ref
        url, name = _resolve_script_ref("./scripts/a.js", "https://example.test/subs/bundle.jsonc")
        self.assertEqual(url, "https://example.test/subs/scripts/a.js")
        self.assertEqual(name, "a.js")

    def test_resolve_script_ref_http_rejected(self):
        from site_adapters.services.subscriptions import _resolve_script_ref
        url, name = _resolve_script_ref("http://insecure.example.com/s.js", "https://base.test/sub.jsonc")
        self.assertIsNone(url)
        self.assertIsNone(name)

    def test_resolve_script_ref_plain_name_returns_none(self):
        from site_adapters.services.subscriptions import _resolve_script_ref
        url, name = _resolve_script_ref("cleanup.js", "https://base.test/sub.jsonc")
        self.assertIsNone(url)
        self.assertIsNone(name)

    def test_validate_https_url_rejects_private_ip(self):
        from site_adapters.services.subscriptions import _validate_https_url
        with self.assertRaises(ValueError):
            _validate_https_url("https://192.168.1.1/file.jsonc")

    def test_validate_https_url_accepts_public_url(self):
        from site_adapters.services.subscriptions import _validate_https_url
        parsed = _validate_https_url("https://cdn.example.com/file.jsonc")
        self.assertEqual(parsed.hostname, "cdn.example.com")
