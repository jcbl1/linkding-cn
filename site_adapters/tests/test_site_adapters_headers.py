"""
Tests for headers config normalization, auth requirements extraction,
get_best_headers, and resolver/get_auth_for_request header injection.

Covers the "免声明读取" (read saved header credentials without requiring
auth.headers declaration) feature and the structured headers block refactor.
"""

import json
import os
import shutil
import tempfile

from django.test import TestCase, override_settings

from site_adapters.services.auth.credentials import (
    _normalize_headers_block,
    _merge_headers_block,
    _extract_auth_block,
    _build_section_auth_requirements,
    get_best_headers,
    get_user_header,
    get_shared_header,
    save_user_header,
    save_shared_header,
    save_user_cookie,
    delete_user_header,
    delete_shared_header,
)
from site_adapters.services.auth.cookies import _filter_cookies_for_domain
from site_adapters.services.config.loader import _cache
from site_adapters.services.config.resolver import (
    get_metadata_config,
)
from site_adapters.services.auth import get_auth_for_request


class CookieDomainFilterTestCase(TestCase):
    def test_wildcard_keeps_only_target_domain_cookies(self):
        cookies = [
            {"name": "token", "value": "1", "domain": ".xiaoheihe.cn"},
            {"name": "api_only", "value": "2", "domain": "api.xiaoheihe.cn"},
            {"name": "www_only", "value": "3", "domain": "www.xiaoheihe.cn"},
            {"name": "parent", "value": "4", "domain": "xiaoheihe.cn"},
            {"name": "other", "value": "5", "domain": ".example.com"},
        ]
        result = _filter_cookies_for_domain(cookies, "*.xiaoheihe.cn")
        names = {c["name"] for c in result}
        self.assertEqual(names, {"token", "api_only", "www_only", "parent"})

    def test_exact_host_does_not_keep_other_subdomains(self):
        cookies = [
            {"name": "parent", "value": "1", "domain": ".xiaoheihe.cn"},
            {"name": "api_only", "value": "2", "domain": "api.xiaoheihe.cn"},
            {"name": "www_only", "value": "3", "domain": "www.xiaoheihe.cn"},
        ]
        result = _filter_cookies_for_domain(cookies, "api.xiaoheihe.cn")
        names = {c["name"] for c in result}
        self.assertEqual(names, {"parent", "api_only"})


class NormalizeHeadersBlockTestCase(TestCase):
    """Tests for _normalize_headers_block()."""

    def test_empty_dict(self):
        r = _normalize_headers_block({})
        self.assertEqual(r, {'enabled': True, 'help': '', 'values': {}})

    def test_flat_form_with_headers(self):
        r = _normalize_headers_block({'X-API-Key': ''})
        self.assertEqual(r['enabled'], True)
        self.assertEqual(r['help'], '')
        self.assertEqual(r['values'], {'X-API-Key': ''})

    def test_flat_form_with_enabled_false(self):
        r = _normalize_headers_block({'enabled': False, 'X-API-Key': ''})
        self.assertEqual(r['enabled'], False)
        self.assertEqual(r['values'], {'X-API-Key': ''})

    def test_flat_form_with_help(self):
        r = _normalize_headers_block({'help': 'Enter API key', 'X-API-Key': ''})
        self.assertEqual(r['help'], 'Enter API key')
        self.assertEqual(r['values'], {'X-API-Key': ''})

    def test_structured_form_basic(self):
        r = _normalize_headers_block({'values': {'X-API-Key': ''}})
        self.assertEqual(r['enabled'], True)
        self.assertEqual(r['help'], '')
        self.assertEqual(r['values'], {'X-API-Key': ''})

    def test_structured_form_with_enabled_and_help(self):
        r = _normalize_headers_block({
            'enabled': False,
            'help': 'Disabled section',
            'values': {'X-Key': 'default'},
        })
        self.assertEqual(r['enabled'], False)
        self.assertEqual(r['help'], 'Disabled section')
        self.assertEqual(r['values'], {'X-Key': 'default'})

    def test_structured_form_header_named_enabled(self):
        """A header literally named 'enabled' must go in values."""
        r = _normalize_headers_block({'values': {'enabled': 'my-val'}})
        self.assertEqual(r['enabled'], True)
        self.assertEqual(r['values'], {'enabled': 'my-val'})

    def test_structured_form_header_named_help(self):
        """A header literally named 'help' must go in values."""
        r = _normalize_headers_block({'values': {'help': 'hdr-val'}})
        self.assertEqual(r['help'], '')
        self.assertEqual(r['values'], {'help': 'hdr-val'})

    def test_non_dict_returns_default(self):
        r = _normalize_headers_block(None)
        self.assertEqual(r, {'enabled': True, 'help': '', 'values': {}})
        r2 = _normalize_headers_block("string")
        self.assertEqual(r2, {'enabled': True, 'help': '', 'values': {}})


class MergeHeadersBlockTestCase(TestCase):
    """Tests for _merge_headers_block()."""

    def test_merge_two_flat_blocks(self):
        r = _merge_headers_block({'X-A': '1'}, {'X-B': '2'})
        self.assertEqual(r['values'], {'X-A': '1', 'X-B': '2'})
        self.assertTrue(r['enabled'])

    def test_merge_override_same_key(self):
        r = _merge_headers_block({'X-A': '1'}, {'X-A': '2'})
        self.assertEqual(r['values'], {'X-A': '2'})

    def test_merge_disabled_wins(self):
        r = _merge_headers_block({'X-A': '1'}, {'enabled': False, 'X-B': '2'})
        self.assertFalse(r['enabled'])

    def test_merge_help_last_nonempty_wins(self):
        r = _merge_headers_block({'help': 'first', 'X-A': '1'}, {'X-B': '2'})
        self.assertEqual(r['help'], 'first')
        r2 = _merge_headers_block({'help': 'first', 'X-A': '1'}, {'help': 'second', 'X-B': '2'})
        self.assertEqual(r2['help'], 'second')

    def test_merge_empty_blocks(self):
        r = _merge_headers_block({}, {})
        self.assertEqual(r, {'enabled': True, 'help': '', 'values': {}})

    def test_merge_structured_and_flat(self):
        r = _merge_headers_block({'values': {'X-A': '1'}}, {'X-B': '2'})
        self.assertEqual(r['values'], {'X-A': '1', 'X-B': '2'})


class ExtractAuthBlockTestCase(TestCase):
    """Tests for _extract_auth_block() headers fields."""

    def test_no_headers_key(self):
        r = _extract_auth_block({})
        self.assertFalse(r['headers_active'])
        self.assertEqual(r['headers'], [])
        self.assertEqual(r['headers_help'], '')

    def test_empty_headers_dict(self):
        r = _extract_auth_block({'headers': {}})
        self.assertTrue(r['headers_active'])
        self.assertEqual(r['headers'], [])

    def test_headers_with_declared_names(self):
        r = _extract_auth_block({'headers': {'X-Key': ''}})
        self.assertTrue(r['headers_active'])
        self.assertEqual(r['headers'], ['X-Key'])

    def test_headers_disabled(self):
        r = _extract_auth_block({'headers': {'enabled': False, 'X-Key': ''}})
        self.assertFalse(r['headers_active'])
        self.assertEqual(r['headers'], [])

    def test_headers_with_help(self):
        r = _extract_auth_block({'headers': {'help': 'Enter key', 'X-Key': ''}})
        self.assertEqual(r['headers_help'], 'Enter key')

    def test_structured_headers(self):
        r = _extract_auth_block({'headers': {'values': {'X-Key': ''}}})
        self.assertTrue(r['headers_active'])
        self.assertEqual(r['headers'], ['X-Key'])


class BuildSectionAuthRequirementsTestCase(TestCase):
    """Tests for _build_section_auth_requirements()."""

    def test_no_section_auth_inherits_domain(self):
        domain_auth = {'headers': {'X-Key': ''}}
        r = _build_section_auth_requirements(
            section_data={'select_title': ['h1']},
            domain_auth=domain_auth,
        )
        self.assertTrue(r['headers_active'])
        self.assertEqual(r['headers'], ['X-Key'])
        self.assertEqual(r['source'], 'inherited')

    def test_auth_null_disables_headers(self):
        r = _build_section_auth_requirements(
            section_data={'auth': None},
            domain_auth={'headers': {'X-Key': ''}},
        )
        self.assertFalse(r['headers_active'])
        self.assertEqual(r['source'], 'disabled')

    def test_section_headers_merge(self):
        r = _build_section_auth_requirements(
            section_data={'auth': {'headers': {'X-Section': ''}}},
            domain_auth={'headers': {'X-Domain': ''}},
        )
        self.assertTrue(r['headers_active'])
        self.assertIn('X-Domain', r['headers'])
        self.assertIn('X-Section', r['headers'])
        self.assertEqual(r['source'], 'section')


class GetBestHeadersTestCase(TestCase):
    """Tests for get_best_headers() with real credential storage."""

    def setUp(self):
        self.base_dir = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        _cache.invalidate()
        shutil.rmtree(self.base_dir, ignore_errors=True)

    @override_settings(LD_SITE_ADAPTERS_DIR='/tmp/test_no_such_dir_get_best_headers')
    def test_no_credentials_returns_empty(self):
        # Use a temp dir that exists but has no credentials
        base = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        with override_settings(LD_SITE_ADAPTERS_DIR=base):
            result, status = get_best_headers(
                username='user1', hostname='example.com', scope='')
            self.assertEqual(result, {})

    @override_settings(LD_SITE_ADAPTERS_DIR='')
    def test_user_headers_returned(self):
        base = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        with override_settings(LD_SITE_ADAPTERS_DIR=base):
            save_user_header(username='user1', domain='example.com',
                             header_name='X-API-Key', value='secret123')
            result, _ = get_best_headers(
                username='user1', hostname='example.com', scope='')
            self.assertEqual(result, {'X-API-Key': 'secret123'})

    @override_settings(LD_SITE_ADAPTERS_DIR='')
    def test_shared_headers_fallback(self):
        base = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        with override_settings(LD_SITE_ADAPTERS_DIR=base):
            save_shared_header(domain='example.com',
                              header_name='X-Shared', value='shared-val')
            result, _ = get_best_headers(
                username='', hostname='example.com', scope='')
            self.assertEqual(result, {'X-Shared': 'shared-val'})

    @override_settings(LD_SITE_ADAPTERS_DIR='')
    def test_user_overrides_shared(self):
        base = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        with override_settings(LD_SITE_ADAPTERS_DIR=base):
            save_shared_header(domain='example.com',
                              header_name='X-Key', value='shared-val')
            save_user_header(username='user1', domain='example.com',
                             header_name='X-Key', value='user-val')
            result, _ = get_best_headers(
                username='user1', hostname='example.com', scope='')
            self.assertEqual(result['X-Key'], 'user-val')

    @override_settings(LD_SITE_ADAPTERS_DIR='')
    def test_user_and_shared_merge(self):
        base = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        with override_settings(LD_SITE_ADAPTERS_DIR=base):
            save_user_header(username='user1', domain='example.com',
                             header_name='X-User', value='u-val')
            save_shared_header(domain='example.com',
                              header_name='X-Shared', value='s-val')
            result, _ = get_best_headers(
                username='user1', hostname='example.com', scope='')
            self.assertEqual(result['X-User'], 'u-val')
            self.assertEqual(result['X-Shared'], 's-val')


class ResolverHeaderInjectionTestCase(TestCase):
    """Tests for resolver header injection with saved credentials."""

    def setUp(self):
        self.base_dir = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        _cache.invalidate()
        shutil.rmtree(self.base_dir, ignore_errors=True)

    def setup_adapter(self, domain, config_dict):
        adapter = {"domains": {domain: config_dict}}
        os.makedirs(os.path.join(self.base_dir, 'adapters', 'defaults'), exist_ok=True)
        with open(os.path.join(self.base_dir, 'adapters', 'config.jsonc'), 'w') as f:
            json.dump({
                "_adapters": [{"id": "defaults", "name": "defaults",
                               "source": "./defaults/adapters.jsonc"}]
            }, f)
        with open(os.path.join(self.base_dir, 'adapters', 'defaults', 'adapters.jsonc'), 'w') as f:
            json.dump(adapter, f)

    def test_empty_headers_dict_with_saved_credentials(self):
        """auth.headers: {} + saved header credential → injected."""
        self.setup_adapter('example.com', {
            'auth': {'headers': {}},
            'metadata': {'select_title': ['h1']},
        })
        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            save_user_header(username='user1', domain='example.com',
                            header_name='X-Saved', value='saved-val')
            config = get_metadata_config('https://example.com/page', username='user1')
            self.assertIsNotNone(config)
            self.assertEqual(config['headers'].get('X-Saved'), 'saved-val')

    def test_declared_plus_undeclared_saved_headers(self):
        """Declared header + saved undeclared header → both injected."""
        self.setup_adapter('example.com', {
            'auth': {'headers': {'X-Declared': ''}},
            'metadata': {'select_title': ['h1']},
        })
        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            save_user_header(username='user1', domain='example.com',
                            header_name='X-Undeclared', value='extra-val')
            config = get_metadata_config('https://example.com/page', username='user1')
            self.assertEqual(config['headers'].get('X-Undeclared'), 'extra-val')
            # X-Declared has no saved credential and no config default, so not in headers
            self.assertNotIn('X-Declared', config['headers'])

    def test_config_default_used_when_no_saved(self):
        """Declared header with non-empty default + no saved credential → default used."""
        self.setup_adapter('example.com', {
            'auth': {'headers': {'X-Default': 'default-val'}},
            'metadata': {'select_title': ['h1']},
        })
        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            config = get_metadata_config('https://example.com/page', username='user1')
            self.assertEqual(config['headers'].get('X-Default'), 'default-val')

    def test_saved_overrides_config_default(self):
        """Saved credential overrides config default value."""
        self.setup_adapter('example.com', {
            'auth': {'headers': {'X-Key': 'default-val'}},
            'metadata': {'select_title': ['h1']},
        })
        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            save_user_header(username='user1', domain='example.com',
                            header_name='X-Key', value='saved-val')
            config = get_metadata_config('https://example.com/page', username='user1')
            self.assertEqual(config['headers'].get('X-Key'), 'saved-val')

    def test_headers_disabled_no_injection(self):
        """auth.headers: {enabled: false} → no header injection."""
        self.setup_adapter('example.com', {
            'auth': {'headers': {'enabled': False, 'X-Key': 'default'}},
            'metadata': {'select_title': ['h1']},
        })
        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            save_user_header(username='user1', domain='example.com',
                            header_name='X-Key', value='saved-val')
            config = get_metadata_config('https://example.com/page', username='user1')
            self.assertNotIn('X-Key', config['headers'])

    def test_no_headers_key_no_injection(self):
        """No auth.headers key → no header injection (deferred feature)."""
        self.setup_adapter('example.com', {
            'auth': {'cookie': {'type': 'auto'}},
            'metadata': {'select_title': ['h1']},
        })
        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            save_user_header(username='user1', domain='example.com',
                            header_name='X-Saved', value='saved-val')
            config = get_metadata_config('https://example.com/page', username='user1')
            self.assertNotIn('X-Saved', config['headers'])

    def test_http_header_not_overwritten_by_saved(self):
        """Existing http header should not be overwritten by saved credential."""
        self.setup_adapter('example.com', {
            'defaults': {'http': {'X-Test': 'from-http'}},
            'auth': {'headers': {'X-Test': 'from-config-default'}},
            'metadata': {'select_title': ['h1']},
        })
        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            save_user_header(username='user1', domain='example.com',
                            header_name='X-Test', value='from-saved')
            config = get_metadata_config('https://example.com/page', username='user1')
            self.assertEqual(config['headers'].get('X-Test'), 'from-http')

    def test_section_cookie_falls_back_to_domain_user_cookie(self):
        """Section-level cookie config reads a domain-level saved cookie."""
        self.setup_adapter('example.com', {
            'metadata': {
                'auth': {'cookie': {'type': 'auto'}},
                'select_title': ['h1'],
            },
        })
        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            save_user_cookie(username='user1', domain='example.com',
                             cookie_str='session=abc')
            config = get_metadata_config('https://example.com/page', username='user1')
            self.assertEqual(config.get('_user_cookie'), 'session=abc')

    def test_section_cookie_does_not_fall_back_on_type_mismatch(self):
        """Domain cookie type mismatch still blocks cross-scope fallback."""
        self.setup_adapter('example.com', {
            'auth': {'cookie': {'type': 'auto'}},
            'metadata': {
                'auth': {'cookie': {'type': 'login'}},
                'select_title': ['h1'],
            },
        })
        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            save_user_cookie(username='user1', domain='example.com',
                             cookie_str='session=abc')
            config = get_metadata_config('https://example.com/page', username='user1')
            self.assertIsNone(config.get('_user_cookie'))


class GetAuthForRequestTestCase(TestCase):
    """Tests for get_auth_for_request() header injection."""

    def setUp(self):
        self.base_dir = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        shutil.rmtree(self.base_dir, ignore_errors=True)

    def test_saved_headers_injected_without_declaration(self):
        """get_auth_for_request reads saved headers even when auth.headers is {}."""
        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            save_user_header(username='user1', domain='example.com',
                            header_name='X-Saved', value='saved-val')
            result = get_auth_for_request(
                url='https://example.com/page',
                domain_key='example.com',
                section='metadata',
                merged_auth={'headers': {}},
                merged_http={},
                cookie_config={},
                username='user1',
                scope='',
            )
            self.assertEqual(result['headers'].get('X-Saved'), 'saved-val')

    def test_disabled_headers_not_injected(self):
        """get_auth_for_request skips headers when enabled=False."""
        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            save_user_header(username='user1', domain='example.com',
                            header_name='X-Saved', value='saved-val')
            result = get_auth_for_request(
                url='https://example.com/page',
                domain_key='example.com',
                section='metadata',
                merged_auth={'headers': {'enabled': False, 'X-Saved': ''}},
                merged_http={},
                cookie_config={},
                username='user1',
                scope='',
            )
            self.assertNotIn('X-Saved', result['headers'])

    def test_no_headers_key_not_injected(self):
        """get_auth_for_request does not inject when headers key absent."""
        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            save_user_header(username='user1', domain='example.com',
                            header_name='X-Saved', value='saved-val')
            result = get_auth_for_request(
                url='https://example.com/page',
                domain_key='example.com',
                section='metadata',
                merged_auth={'cookie': {'type': 'auto'}},
                merged_http={},
                cookie_config={},
                username='user1',
                scope='',
            )
            self.assertNotIn('X-Saved', result['headers'])

    def test_config_default_supplements_saved(self):
        """Config default supplements saved headers (different names)."""
        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            save_user_header(username='user1', domain='example.com',
                            header_name='X-Saved', value='saved-val')
            result = get_auth_for_request(
                url='https://example.com/page',
                domain_key='example.com',
                section='metadata',
                merged_auth={'headers': {'X-Default': 'default-val'}},
                merged_http={},
                cookie_config={},
                username='user1',
                scope='',
            )
            self.assertEqual(result['headers'].get('X-Saved'), 'saved-val')
            self.assertEqual(result['headers'].get('X-Default'), 'default-val')
