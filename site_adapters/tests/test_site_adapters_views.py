import os
import shutil
import tempfile
from unittest import mock

from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from bookmarks.models import User
from site_adapters.services.config import parse_jsonc
from site_adapters.services.config.loader import _cache


class SiteAdaptersViewsTestCase(TestCase):
    def setUp(self):
        self.base_dir = tempfile.mkdtemp()
        self.settings_override = override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir)
        self.settings_override.enable()
        self.addCleanup(self.cleanup)

        user = User.objects.create_user("site-adapter-user", password="password", is_superuser=True)
        self.client.force_login(user)

    def cleanup(self):
        _cache.invalidate()
        self.settings_override.disable()
        shutil.rmtree(self.base_dir)

    def test_resources_lists_directories_before_files(self):
        os.makedirs(os.path.join(self.base_dir, "domains"))
        with open(os.path.join(self.base_dir, "global.jsonc"), "w", encoding="utf-8") as f:
            f.write("{}")
        with open(os.path.join(self.base_dir, "notes.txt"), "w", encoding="utf-8") as f:
            f.write("notes")

        response = self.client.get(reverse("linkding:settings.site_adapters.resources"))

        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        self.assertEqual(
            [item["name"] for item in items],
            ["cookies", "domains", "logs", "scripts", "test_assets", "global.jsonc", "notes.txt"],
        )
        self.assertEqual([item["is_dir"] for item in items], [True, True, True, True, True, False, False])

    def test_site_adapters_requires_superuser(self):
        user = User.objects.create_user("site-adapter-nonstaff", password="password")
        self.client.force_login(user)

        response = self.client.get(reverse("linkding:settings.site_adapters"))

        self.assertEqual(response.status_code, 403)

    def test_site_adapters_allows_superuser(self):
        user = User.objects.create_user("site-adapter-superuser", password="password", is_superuser=True)
        self.client.force_login(user)

        response = self.client.get(reverse("linkding:settings.site_adapters"))

        self.assertEqual(response.status_code, 200)

    def test_site_adapters_requires_active_staff(self):
        user = User.objects.create_user("site-adapter-inactive-superuser", password="password", is_superuser=True, is_active=False)
        self.client.force_login(user)

        response = self.client.get(reverse("linkding:settings.site_adapters"))

        self.assertEqual(response.status_code, 302)

    def test_site_adapters_page_renders_global_content(self):
        with open(os.path.join(self.base_dir, "global.jsonc"), "w", encoding="utf-8") as f:
            f.write('{"*": {"http": {"timeout": 9}}}')

        response = self.client.get(reverse("linkding:settings.site_adapters"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "site-adapters.css")
        self.assertContains(response, "timeout")

    def test_domain_lifecycle_supports_alias_save_rename_and_delete(self):
        response = self.client.post(
            reverse("linkding:settings.site_adapters.domain_create"),
            {"domain_key": "example.com"},
        )

        self.assertEqual(response.status_code, 200)
        filename = response.json()["filename"]
        self.assertTrue(os.path.exists(os.path.join(self.base_dir, "domains", filename)))

        alias_content = '{"type": "alias", "target": "target.com"}'
        response = self.client.post(
            reverse("linkding:settings.site_adapters.domain_save"),
            {"filename": filename, "content": alias_content},
        )

        self.assertEqual(response.status_code, 200)

        response = self.client.get(
            reverse("linkding:settings.site_adapters.domain_read"),
            {"filename": filename},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(parse_jsonc(response.json()["content"])["target"], "target.com")

        response = self.client.get(reverse("linkding:settings.site_adapters"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "→ target.com")

        response = self.client.post(
            reverse("linkding:settings.site_adapters.domain_rename"),
            {"old_filename": filename, "new_domain": "renamed.example.com"},
        )

        self.assertEqual(response.status_code, 200)
        new_filename = response.json()["new_filename"]
        self.assertFalse(os.path.exists(os.path.join(self.base_dir, "domains", filename)))
        self.assertTrue(os.path.exists(os.path.join(self.base_dir, "domains", new_filename)))

        response = self.client.post(
            reverse("linkding:settings.site_adapters.domain_delete"),
            {"filename": new_filename},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(os.path.exists(os.path.join(self.base_dir, "domains", new_filename)))

    def test_domain_crud_rejects_unsafe_inputs_and_invalid_json(self):
        os.makedirs(os.path.join(self.base_dir, "domains"))
        with open(os.path.join(self.base_dir, "domains", "example.com.jsonc"), "w", encoding="utf-8") as f:
            f.write("{}")

        response = self.client.post(
            reverse("linkding:settings.site_adapters.domain_create"),
            {"domain_key": "../outside"},
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            reverse("linkding:settings.site_adapters.domain_save"),
            {"filename": "../outside.jsonc", "content": "{}"},
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            reverse("linkding:settings.site_adapters.domain_save"),
            {"filename": "example.com.jsonc", "content": '{"metadata": '},
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            reverse("linkding:settings.site_adapters.domain_rename"),
            {"old_filename": "example.com.jsonc", "new_domain": "../outside"},
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            reverse("linkding:settings.site_adapters.domain_delete"),
            {"filename": "../outside.jsonc"},
        )
        self.assertEqual(response.status_code, 400)

    def test_resources_reads_full_file_content(self):
        os.makedirs(os.path.join(self.base_dir, "scripts"))
        content = "\n".join(f"line {i}" for i in range(68))
        with open(os.path.join(self.base_dir, "scripts", "notes.txt"), "w", encoding="utf-8") as f:
            f.write(content)

        response = self.client.get(
            reverse("linkding:settings.site_adapters.resources"),
            {"path": "scripts/notes.txt"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["content"], content)

    def test_resource_save_writes_global_jsonc_at_root(self):
        os.makedirs(os.path.join(self.base_dir, "domains"))

        response = self.client.post(
            reverse("linkding:settings.site_adapters.resource_save"),
            {"path": "global.jsonc", "content": '{"*": {"http": {"timeout": 7}}}'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(os.path.exists(os.path.join(self.base_dir, "global.jsonc")))
        self.assertFalse(os.path.exists(os.path.join(self.base_dir, "domains", "global.jsonc")))

    def test_resource_save_updates_global_scope_only(self):
        with open(os.path.join(self.base_dir, "global.jsonc"), "w", encoding="utf-8") as f:
            f.write('{\n  // keep this\n  "_subscriptions": [{"name": "demo"}],\n  "*": {"http": {"timeout": 5}}\n}\n')

        response = self.client.post(
            reverse("linkding:settings.site_adapters.resource_save"),
            {
                "path": "global.jsonc",
                "scope": "*",
                "content": '{"http": {"timeout": 7}}',
            },
        )

        self.assertEqual(response.status_code, 200)
        with open(os.path.join(self.base_dir, "global.jsonc"), encoding="utf-8") as f:
            content = f.read()
        config = parse_jsonc(content)
        self.assertIn("// keep this", content)
        self.assertEqual(config["_subscriptions"][0]["name"], "demo")
        self.assertEqual(config["*"]["http"]["timeout"], 7)

    def test_resource_save_rejects_path_traversal(self):
        response = self.client.post(
            reverse("linkding:settings.site_adapters.resource_save"),
            {"path": "../outside.txt", "content": "nope"},
        )

        self.assertEqual(response.status_code, 400)

    def test_resource_manage_creates_deletes_and_moves_resources(self):
        response = self.client.post(
            reverse("linkding:settings.site_adapters.resource_manage"),
            {"action": "create_dir", "path": "scripts", "name": "helpers"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(os.path.isdir(os.path.join(self.base_dir, "scripts", "helpers")))

        response = self.client.post(
            reverse("linkding:settings.site_adapters.resource_manage"),
            {"action": "create_file", "path": "scripts/helpers", "name": "cleanup.js"},
        )

        self.assertEqual(response.status_code, 200)
        source = os.path.join(self.base_dir, "scripts", "helpers", "cleanup.js")
        self.assertTrue(os.path.exists(source))

        response = self.client.post(
            reverse("linkding:settings.site_adapters.resource_manage"),
            {"action": "move", "path": "scripts/helpers/cleanup.js", "target_dir": "scripts"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(os.path.exists(source))
        moved = os.path.join(self.base_dir, "scripts", "cleanup.js")
        self.assertTrue(os.path.exists(moved))

        response = self.client.post(
            reverse("linkding:settings.site_adapters.resource_manage"),
            {"action": "rename", "path": "scripts/cleanup.js", "name": "cleaned.js"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["path"], "scripts/cleaned.js")
        self.assertFalse(os.path.exists(moved))
        self.assertTrue(os.path.exists(os.path.join(self.base_dir, "scripts", "cleaned.js")))

        response = self.client.post(
            reverse("linkding:settings.site_adapters.resource_manage"),
            {"action": "delete", "path": "scripts/helpers"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(os.path.exists(os.path.join(self.base_dir, "scripts", "helpers")))

    def test_resource_manage_rejects_etc_and_path_traversal(self):
        response = self.client.post(
            reverse("linkding:settings.site_adapters.resource_manage"),
            {"action": "create_file", "path": "etc", "name": "local.txt"},
        )

        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            reverse("linkding:settings.site_adapters.resource_manage"),
            {"action": "delete", "path": "../outside.txt"},
        )

        self.assertEqual(response.status_code, 400)

    def test_validate_can_scope_to_current_domain_file(self):
        os.makedirs(os.path.join(self.base_dir, "domains"))
        with open(os.path.join(self.base_dir, "domains", "good.com.jsonc"), "w", encoding="utf-8") as f:
            f.write('{"metadata": {"select_title": ["h1"]}}')
        with open(os.path.join(self.base_dir, "domains", "bad.com.jsonc"), "w", encoding="utf-8") as f:
            f.write('{"metadata": ')

        response = self.client.post(
            reverse("linkding:settings.site_adapters.action"),
            {"action": "validate", "filename": "good.com.jsonc"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["issues"], [])

        response = self.client.post(
            reverse("linkding:settings.site_adapters.action"),
            {"action": "validate"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(any("bad.com.jsonc" in issue for issue in response.json()["issues"]))

    def test_action_config_returns_merged_config_without_network(self):
        os.makedirs(os.path.join(self.base_dir, "domains"))
        with open(os.path.join(self.base_dir, "global.jsonc"), "w", encoding="utf-8") as f:
            f.write('{"*": {"http": {"timeout": 9}}}')
        with open(os.path.join(self.base_dir, "domains", "example.com.jsonc"), "w", encoding="utf-8") as f:
            f.write('{"http": {"timeout": 1}, "metadata": {"select_title": ["h1"]}}')

        response = self.client.post(
            reverse("linkding:settings.site_adapters.action"),
            {"action": "test", "test_type": "config", "url": "https://example.com/post"},
        )

        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertEqual(result["domain_key"], "example.com")
        self.assertEqual(result["merged"]["http"]["timeout"], 9)

    def test_action_test_returns_json_error_when_test_fails(self):
        os.makedirs(os.path.join(self.base_dir, "domains"))
        with open(os.path.join(self.base_dir, "domains", "example.com.jsonc"), "w", encoding="utf-8") as f:
            f.write('{"metadata": {"select_title": ["h1"]}}')

        with mock.patch(
            "site_adapters.views.testing.load_website_metadata_for_test",
            side_effect=RuntimeError("blocked"),
        ):
            response = self.client.post(
                reverse("linkding:settings.site_adapters.action"),
                {"action": "test", "test_type": "metadata", "url": "https://example.com/post"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["type"], "metadata")
        self.assertEqual(response.json()["error"], "blocked")

    def test_cookie_test_uses_snapshot_cookie_scripts_and_refreshes_status(self):
        os.makedirs(os.path.join(self.base_dir, "domains"))
        os.makedirs(os.path.join(self.base_dir, "scripts"))
        with open(os.path.join(self.base_dir, "domains", "example.com.jsonc"), "w", encoding="utf-8") as f:
            f.write(
                '{"auth": {"cookie": {"type": "anon"}},'
                '"metadata": {"select_title": ["h1"]},'
                '"snapshot": {}}'
            )

        def mock_verify_and_refresh(cookie_config, url, domain_key, verify_context):
            from site_adapters.services.auth.cookies import save_cookie_for_domain
            save_cookie_for_domain("example.com", "session=abc", source="test")
            return "session=abc"

        with mock.patch("site_adapters.views.testing.verify_and_refresh", side_effect=mock_verify_and_refresh):
            response = self.client.post(
                reverse("linkding:settings.site_adapters.action"),
                {"action": "test", "test_type": "cookie", "url": "https://example.com/post"},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["has_cookie"])
        self.assertTrue(data["refreshed"])
        self.assertEqual(data["cookie_preview"], "session=abc")

    def test_view_snapshot_rejects_path_outside_test_assets(self):
        response = self.client.get(
            reverse("linkding:settings.site_adapters.view_snapshot"),
            {"path": "../global.jsonc"},
        )

        self.assertEqual(response.status_code, 404)

    def test_subscription_manage_adds_subscription_and_preserves_global_comments(self):
        with open(os.path.join(self.base_dir, "global.jsonc"), "w", encoding="utf-8") as f:
            f.write('{\n  // keep this\n  "*": {"http": {"timeout": 5}}\n}\n')

        with mock.patch("site_adapters.services.subscriptions.validate_subscription_url") as validate_url:
            response = self.client.post(
                reverse("linkding:settings.site_adapters.subscription_manage"),
                {
                    "action": "add",
                    "url": "https://example.test/site-adapters.jsonc",
                    "name": "demo",
                    "update_interval": "3600",
                },
            )

        self.assertEqual(response.status_code, 200)
        validate_url.assert_called_once_with("https://example.test/site-adapters.jsonc")
        with open(os.path.join(self.base_dir, "global.jsonc"), encoding="utf-8") as f:
            content = f.read()
        self.assertIn("// keep this", content)
        config = parse_jsonc(content)
        self.assertEqual(config["_subscriptions"][0]["name"], "demo")
        self.assertEqual(config["_subscriptions"][0]["update_interval"], 3600)

    def test_subscription_manage_rejects_non_https_url(self):
        response = self.client.post(
            reverse("linkding:settings.site_adapters.subscription_manage"),
            {
                "action": "add",
                "url": "http://example.test/site-adapters.jsonc",
            },
        )

        self.assertEqual(response.status_code, 400)

    def test_subscription_manage_moves_updates_interval_and_deletes_cache(self):
        os.makedirs(os.path.join(self.base_dir, "subscriptions", "a"))
        with open(os.path.join(self.base_dir, "global.jsonc"), "w", encoding="utf-8") as f:
            f.write(
                '{"_subscriptions": ['
                '{"url": "https://a.test/site-adapters.jsonc", "name": "a", "update_interval": 86400},'
                '{"url": "https://b.test/site-adapters.jsonc", "name": "b", "update_interval": 86400}'
                ']}'
            )

        response = self.client.post(
            reverse("linkding:settings.site_adapters.subscription_manage"),
            {"action": "move", "index": "1", "direction": "up"},
        )

        self.assertEqual(response.status_code, 200)
        with open(os.path.join(self.base_dir, "global.jsonc"), encoding="utf-8") as f:
            config = parse_jsonc(f.read())
        self.assertEqual([sub["name"] for sub in config["_subscriptions"]], ["b", "a"])

        response = self.client.post(
            reverse("linkding:settings.site_adapters.subscription_manage"),
            {"action": "set_interval", "index": "0", "update_interval": "7200"},
        )

        self.assertEqual(response.status_code, 200)
        with open(os.path.join(self.base_dir, "global.jsonc"), encoding="utf-8") as f:
            config = parse_jsonc(f.read())
        self.assertEqual(config["_subscriptions"][0]["update_interval"], 7200)

        response = self.client.post(
            reverse("linkding:settings.site_adapters.subscription_manage"),
            {"action": "delete", "index": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(os.path.exists(os.path.join(self.base_dir, "subscriptions", "a")))

    def test_subscription_manage_saves_rejects_conflicts_and_renames_cache(self):
        os.makedirs(os.path.join(self.base_dir, "subscriptions", "b"))
        with open(os.path.join(self.base_dir, "subscriptions", "b", "_meta.json"), "w", encoding="utf-8") as f:
            f.write("{}")
        with open(os.path.join(self.base_dir, "global.jsonc"), "w", encoding="utf-8") as f:
            f.write(
                '{"_subscriptions": ['
                '{"url": "https://a.test/site-adapters.jsonc", "name": "a"},'
                '{"url": "https://b.test/site-adapters.jsonc", "name": "b"}'
                ']}'
            )

        with mock.patch("site_adapters.services.subscriptions.validate_subscription_url"):
            response = self.client.post(
                reverse("linkding:settings.site_adapters.subscription_manage"),
                {
                    "action": "save",
                    "index": "1",
                    "url": "https://a.test/site-adapters.jsonc",
                    "name": "a",
                    "update_interval": "86400",
                },
            )
            self.assertEqual(response.status_code, 409)

            response = self.client.post(
                reverse("linkding:settings.site_adapters.subscription_manage"),
                {
                    "action": "save",
                    "index": "1",
                    "url": "https://c.test/site-adapters.jsonc",
                    "name": "c",
                    "update_interval": "7200",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(os.path.exists(os.path.join(self.base_dir, "subscriptions", "b")))
        with open(os.path.join(self.base_dir, "global.jsonc"), encoding="utf-8") as f:
            config = parse_jsonc(f.read())
        self.assertEqual(config["_subscriptions"][1]["name"], "c")
        self.assertEqual(config["_subscriptions"][1]["update_interval"], 7200)

    def test_subscription_manage_update_fetches_subscription_and_reports_failure(self):
        with open(os.path.join(self.base_dir, "global.jsonc"), "w", encoding="utf-8") as f:
            f.write('{"_subscriptions": [{"url": "https://a.test/site-adapters.jsonc", "name": "a"}]}')

        with mock.patch("site_adapters.services.subscriptions.fetch_subscription", return_value="/tmp/sub") as fetch:
            response = self.client.post(
                reverse("linkding:settings.site_adapters.subscription_manage"),
                {"action": "update", "index": "0"},
            )

        self.assertEqual(response.status_code, 200)
        fetch.assert_called_once_with("https://a.test/site-adapters.jsonc", name="a", force=True)

        with mock.patch("site_adapters.services.subscriptions.fetch_subscription", return_value=None):
            response = self.client.post(
                reverse("linkding:settings.site_adapters.subscription_manage"),
                {"action": "update", "index": "0"},
            )

        self.assertEqual(response.status_code, 502)


class HelpersTestCase(TestCase):
    """Tests for views helpers that were previously untested."""

    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user("testhelpers", password="password", is_superuser=True)

    def test_get_global_subscriptions_returns_empty_list_when_no_config(self):
        from site_adapters.views.helpers import _get_global_subscriptions
        with tempfile.TemporaryDirectory() as tmp:
            from django.conf import settings
            old = settings.LD_SITE_ADAPTERS_DIR
            settings.LD_SITE_ADAPTERS_DIR = tmp
            try:
                result = _get_global_subscriptions()
                self.assertEqual(result, [])
            finally:
                settings.LD_SITE_ADAPTERS_DIR = old

    def test_get_global_subscriptions_returns_list_from_config(self):
        from site_adapters.views.helpers import _get_global_subscriptions
        with tempfile.TemporaryDirectory() as tmp:
            from django.conf import settings
            old = settings.LD_SITE_ADAPTERS_DIR
            settings.LD_SITE_ADAPTERS_DIR = tmp
            try:
                with open(os.path.join(tmp, 'global.jsonc'), 'w') as f:
                    f.write('{"_subscriptions": [{"url": "https://example.test"}]}')
                result = _get_global_subscriptions()
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]['url'], 'https://example.test')
            finally:
                settings.LD_SITE_ADAPTERS_DIR = old

    def test_get_global_subscriptions_returns_empty_for_invalid_type(self):
        from site_adapters.views.helpers import _get_global_subscriptions
        with tempfile.TemporaryDirectory() as tmp:
            from django.conf import settings
            old = settings.LD_SITE_ADAPTERS_DIR
            settings.LD_SITE_ADAPTERS_DIR = tmp
            try:
                with open(os.path.join(tmp, 'global.jsonc'), 'w') as f:
                    f.write('{"_subscriptions": "not_a_list"}')
                result = _get_global_subscriptions()
                self.assertEqual(result, [])
            finally:
                settings.LD_SITE_ADAPTERS_DIR = old
