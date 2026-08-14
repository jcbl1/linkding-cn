import json
import os
import shutil
import tempfile

from django.test import TestCase, override_settings
from django.urls import reverse

from bookmarks.models import User
from site_adapters.services.config.resolver import (
    get_user_domain_preferences,
    get_user_preferences,
    save_user_preferences,
)


class UserPreferencesTestCase(TestCase):
    def setUp(self):
        self.base_dir = tempfile.mkdtemp()
        self.settings_override = override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir)
        self.settings_override.enable()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.settings_override.disable()
        shutil.rmtree(self.base_dir)

    def test_save_uses_preferences_path(self):
        save_user_preferences('alice', 'example.com', 'comments', False)

        new_path = os.path.join(
            self.base_dir, 'preferences', 'users', 'alice', 'toggles.json'
        )
        old_path = os.path.join(
            self.base_dir, 'credentials', 'users', 'alice', 'preferences.json'
        )

        self.assertTrue(os.path.exists(new_path))
        self.assertFalse(os.path.exists(old_path))
        self.assertEqual(
            get_user_preferences('alice'),
            {'example.com': {'comments': False}},
        )
        self.assertEqual(
            get_user_domain_preferences('alice', 'example.com'),
            {'comments': False},
        )

    def test_does_not_read_legacy_preferences_path(self):
        legacy_path = os.path.join(
            self.base_dir, 'credentials', 'users', 'alice', 'preferences.json'
        )
        os.makedirs(os.path.dirname(legacy_path), exist_ok=True)
        with open(legacy_path, 'w', encoding='utf-8') as f:
            json.dump({'example.com': {'comments': True}}, f)

        self.assertEqual(
            get_user_preferences('alice'),
            {},
        )
        self.assertFalse(
            os.path.exists(
                os.path.join(
                    self.base_dir, 'preferences', 'users', 'alice', 'toggles.json'
                )
            )
        )

    def test_snapshot_toggles_view_saves_preferences(self):
        user = User.objects.create_user('alice', password='password')
        self.client.force_login(user)

        response = self.client.post(
            reverse('linkding:settings.snapshot_toggles'),
            {
                'domain': 'example.com',
                'toggle_id': 'comments',
                'enabled': 'false',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'success': True})
        self.assertEqual(
            get_user_preferences('alice'),
            {'example.com': {'comments': False}},
        )
