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

    def test_site_adapters_page_renders(self):
        response = self.client.get(reverse("linkding:settings.site_adapters"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "site-adapters.css")


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


    def test_action_config_returns_merged_config_without_network(self):
        import json as _json
        os.makedirs(os.path.join(self.base_dir, "adapters", "defaults"))
        with open(os.path.join(self.base_dir, "adapters", "config.jsonc"), "w", encoding="utf-8") as f:
            _json.dump({
                "_adapters": [{"id": "defaults", "name": "defaults", "source": "./defaults/adapters.jsonc"}]
            }, f)
        with open(os.path.join(self.base_dir, "adapters", "defaults", "adapters.jsonc"), "w", encoding="utf-8") as f:
            _json.dump({
                "defaults": {"http": {"timeout": 9}},
                "domains": {"example.com": {"http": {"timeout": 1}, "metadata": {"select_title": ["h1"]}}}
            }, f)

        response = self.client.post(
            reverse("linkding:settings.site_adapters.action"),
            {"action": "test", "test_type": "config", "url": "https://example.com/post"},
        )

        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertEqual(result["domain_key"], "example.com")
        self.assertEqual(result["merged"]["http"]["timeout"], 1)

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
        cookie_config = {"type": "anon"}
        meta_config = {"_domain_key": "example.com", "cookie": cookie_config}

        def mock_verify_and_refresh(cookie_config, url, domain_key, verify_context):
            from site_adapters.services.auth.credentials import save_shared_cookie
            save_shared_cookie("example.com", "session=abc")
            return "session=abc"

        with mock.patch("site_adapters.views.testing.get_metadata_config", return_value=meta_config),              mock.patch("site_adapters.views.testing.get_snapshot_config", return_value={}),              mock.patch("site_adapters.views.testing.verify_and_refresh", side_effect=mock_verify_and_refresh):
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


