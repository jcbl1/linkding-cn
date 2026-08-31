import json
import os
import shutil
import tempfile
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings

from site_adapters.services.config.loader import _cache
from site_adapters.services.auth.cookies import _should_refresh_cookie, verify_and_refresh


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
        import json as _json
        self.write("adapters/config.jsonc", _json.dumps({
            "_adapters": [{"id": "defaults", "name": "defaults", "source": "./defaults/adapters.jsonc"}]
        }))
        self.write("adapters/defaults/adapters.jsonc", _json.dumps({
            "defaults": {"http": {"timeout": 9}},
            "domains": {"example.com": {"http": {"timeout": 1}}}
        }))
        out = StringIO()

        call_command("site_adapter", "show-config", "https://example.com/post", "--dir", self.base_dir, stdout=out)

        result = json.loads(out.getvalue())
        self.assertEqual(result["domain_key"], "example.com")
        self.assertEqual(result["merged"]["http"]["timeout"], 1)

    def test_validate_subscription_reports_local_file_errors(self):
        path = self.write("bundle.jsonc", '{"domains": {"bad.com": {"type": "alias"}}}')
        out = StringIO()

        call_command("site_adapter", "validate-subscription", path, stdout=out)

        self.assertIn("bad.com alias missing target", out.getvalue())

    def test_prefetch_subscriptions_downloads_missing_remote_source(self):
        import json as _json
        self.write("adapters/config.jsonc", _json.dumps({
            "_adapters": [
                {"id": "defaults", "name": "defaults", "source": "./defaults/adapters.jsonc"},
                {
                    "id": "woohoodai",
                    "name": "official-standard",
                    "source": "https://example.test/adapters.jsonc",
                    "update_interval": 86400,
                    "enabled": True,
                },
            ]
        }))
        self.write("adapters/defaults/adapters.jsonc", '{"domains": {}}')

        payload = {
            "_meta": {"id": "woohoodai", "name": "official-standard", "version": 1},
            "domains": {"example.com": {"metadata": {"select_title": ["h1"]}}},
        }
        resp = mock.Mock()
        resp.status_code = 200
        resp.text = _json.dumps(payload)
        resp.headers = {}
        resp.raise_for_status.return_value = None
        resp.json.return_value = payload

        out = StringIO()
        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            return_value=resp,
        ):
            call_command("site_adapter", "prefetch-subscriptions", stdout=out)

        cached = os.path.join(
            self.base_dir, "adapters", "woohoodai.official-standard", "adapters.jsonc"
        )
        self.assertTrue(os.path.exists(cached))
        self.assertIn("prefetched 1 subscription(s)", out.getvalue())

    def test_prefetch_subscriptions_skips_fresh_cache(self):
        import json as _json
        import time
        from site_adapters.services.subscriptions import _update_meta_entry

        source = "https://example.test/adapters.jsonc"
        self.write("adapters/config.jsonc", _json.dumps({
            "_adapters": [
                {"id": "defaults", "name": "defaults", "source": "./defaults/adapters.jsonc"},
                {
                    "id": "woohoodai",
                    "name": "official-standard",
                    "source": source,
                    "update_interval": 86400,
                    "enabled": True,
                },
            ]
        }))
        self.write("adapters/defaults/adapters.jsonc", '{"domains": {}}')
        cache_dir = os.path.join(self.base_dir, "adapters", "woohoodai.official-standard")
        os.makedirs(cache_dir, exist_ok=True)
        with open(os.path.join(cache_dir, "adapters.jsonc"), "w", encoding="utf-8") as f:
            _json.dump({"domains": {}}, f)
        _update_meta_entry("https://example.test/", last_fetch=time.time())

        out = StringIO()
        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
        ) as get:
            call_command("site_adapter", "prefetch-subscriptions", stdout=out)

        get.assert_not_called()
        self.assertIn("no subscriptions needed prefetching", out.getvalue())

    def test_cookie_command_refreshes_when_cookie_is_missing(self):
        cookie_config = {"type": "anon", "refresh": {"url": "https://example.com/login"}}
        meta_config = {"_domain_key": "example.com", "cookie": cookie_config}
        snap_config = {"_domain_key": "example.com", "cookie": cookie_config}

        def refresh_cookie_declarative(_refresh_config, _url, _domain_key):
            from site_adapters.services.auth.credentials import save_shared_cookie
            save_shared_cookie(domain="example.com", cookie_str="session=abc")
            return [{"name": "session", "value": "abc", "domain": ".example.com", "path": "/"}]

        out = StringIO()
        with mock.patch(
            "site_adapters.management.commands.site_adapter.get_metadata_config",
            return_value=meta_config,
        ), mock.patch(
            "site_adapters.management.commands.site_adapter.get_snapshot_config",
            return_value=snap_config,
        ), mock.patch(
            "site_adapters.services.auth.cookies.refresh_cookie_declarative",
            side_effect=refresh_cookie_declarative,
        ) as refresh:
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

    def test_login_cookie_does_not_use_anonymous_refresh_by_default(self):
        default_refresh = {"url": "", "wait_cookie": "", "timeout": 30, "interval": 14400}

        self.assertTrue(_should_refresh_cookie({
            "type": "auto",
            "refresh": default_refresh,
        }))
        self.assertFalse(_should_refresh_cookie({
            "type": "login",
            "refresh": default_refresh,
        }))
        self.assertTrue(_should_refresh_cookie({
            "type": "login",
            "refresh": {**default_refresh, "wait_cookie": "reddit_session"},
        }))

    def test_verify_and_refresh_uses_hostname_lookup_for_wildcard_domain(self):
        """A stored cookie for a wildcard key is reused, not force-refreshed."""
        from site_adapters.services.auth.credentials import save_shared_cookie

        save_shared_cookie(domain="*.xiaoheihe.cn",
                           cookie_str="x_xhh_tokenid=abc",
                           scope="metadata")

        with mock.patch(
            "site_adapters.services.auth.cookies.refresh_cookie_declarative",
        ) as refresh:
            result = verify_and_refresh(
                cookie_config={
                    "type": "auto",
                    "refresh": {"url": "https://www.xiaoheihe.cn/",
                                "wait_cookie": "x_xhh_tokenid",
                                "timeout": 30},
                },
                url="https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?link_id=0f487254ffbc",
                domain_key="*.xiaoheihe.cn",
                verify_context={"url": "", "status": 0, "title": "", "body_preview": ""},
                scope="metadata",
            )

        refresh.assert_not_called()
        self.assertEqual(result, "x_xhh_tokenid=abc")
