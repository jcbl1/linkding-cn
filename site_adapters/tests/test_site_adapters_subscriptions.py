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

    def response(self, payload, headers=None):
        resp = mock.Mock()
        resp.status_code = 200
        resp.text = json.dumps(payload)
        resp.headers = headers or {}
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
            file_path = fetch_subscription(
                "https://example.test/bundle/", name="bundle", force=True
            )

        data = json.loads(open(file_path, encoding="utf-8").read())
        alias_config = data["domains"]["alias.com"]
        self.assertEqual(alias_config, "target.com")

    def test_fetch_subscription_accepts_legacy_file_url(self):
        """向后兼容：source 指向 adapters.jsonc 文件路径时也能正常工作。"""
        payload = {
            "domains": {"example.com": {"metadata": {"select_title": ["h1"]}}},
        }

        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            return_value=self.response(payload),
        ):
            file_path = fetch_subscription(
                "https://example.test/bundle/adapters.jsonc",
                name="bundle", force=True,
            )

        self.assertTrue(os.path.exists(file_path))

    def test_fetch_subscription_writes_meta_json(self):
        """下载后应在 _meta.json 中记录运行时状态。"""
        payload = {"domains": {"example.com": {}}}
        headers = {"ETag": '"abc123"', "Last-Modified": "Mon, 11 Aug 2026 00:00:00 GMT"}

        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            return_value=self.response(payload, headers=headers),
        ):
            fetch_subscription(
                "https://example.test/bundle/", name="bundle", force=True,
            )

        # 验证 _meta.json
        from site_adapters.services.subscriptions import _get_meta_entry
        entry = _get_meta_entry("https://example.test/bundle/")
        self.assertIsNotNone(entry.get("last_fetch"))
        self.assertEqual(entry.get("etag"), '"abc123"')
        self.assertEqual(entry.get("content_hash"), mock.ANY)

    def test_fetch_subscription_scripts_downloaded(self):
        """订阅源中包含 scripts 引用时，脚本应被下载到 scripts/ 目录。"""
        payload = {
            "domains": {
                "example.com": {
                    "snapshot": {
                        "scripts": [
                            {"path": "cleanup.js", "hook": "before"},
                            {"path": "subdir/extract.py", "hook": "replace"},
                        ]
                    }
                }
            }
        }

        # 模拟脚本下载响应
        def mock_get(url, *args, **kwargs):
            if "adapters.jsonc" in url:
                return self.response(payload)
            elif "cleanup.js" in url:
                resp = mock.Mock()
                resp.status_code = 200
                resp.text = "// cleanup script content"
                resp.headers = {}
                resp.raise_for_status.return_value = None
                return resp
            elif "extract.py" in url:
                resp = mock.Mock()
                resp.status_code = 200
                resp.text = "# extract script content"
                resp.headers = {}
                resp.raise_for_status.return_value = None
                return resp
            return mock.Mock(status_code=404)

        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            side_effect=mock_get,
        ):
            file_path = fetch_subscription(
                "https://example.test/bundle/", name="bundle", force=True,
            )

        # 验证脚本文件已下载
        scripts_dir = os.path.join(os.path.dirname(file_path), "scripts")
        self.assertTrue(os.path.exists(os.path.join(scripts_dir, "cleanup.js")))
        self.assertTrue(os.path.exists(os.path.join(scripts_dir, "subdir", "extract.py")))

        # 验证 adapters.jsonc 保持原样（路径不改写）
        data = json.loads(open(file_path, encoding="utf-8").read())
        scripts = data["domains"]["example.com"]["snapshot"]["scripts"]
        self.assertEqual(scripts[0]["path"], "cleanup.js")

    def test_resolve_script_ref_https_url_rejected(self):
        """HTTPS URL 不应作为订阅源脚本路径。"""
        from site_adapters.services.subscriptions import _resolve_script_ref
        url, name = _resolve_script_ref(
            "https://cdn.example.com/scripts/clean.js",
            "https://base.test/bundle/",
        )
        self.assertIsNone(url)
        self.assertIsNone(name)

    def test_resolve_script_ref_relative_resolves_against_base(self):
        from site_adapters.services.subscriptions import _resolve_script_ref
        url, name = _resolve_script_ref(
            "./scripts/a.js", "https://base.test/bundle/"
        )
        self.assertEqual(url, "https://base.test/bundle/scripts/a.js")
        self.assertEqual(name, "scripts/a.js")

    def test_resolve_script_ref_plain_name_infers_scripts_dir(self):
        """纯文件名推断在远端 scripts/ 目录。"""
        from site_adapters.services.subscriptions import _resolve_script_ref
        url, name = _resolve_script_ref(
            "cleanup.js", "https://base.test/bundle/"
        )
        self.assertEqual(url, "https://base.test/bundle/scripts/cleanup.js")
        self.assertEqual(name, "cleanup.js")

    def test_resolve_script_ref_dir_prefixed_name(self):
        """目录前缀名推断在远端 scripts/ 目录。"""
        from site_adapters.services.subscriptions import _resolve_script_ref
        url, name = _resolve_script_ref(
            "zhihu/extract.py", "https://base.test/bundle/"
        )
        self.assertEqual(url, "https://base.test/bundle/scripts/zhihu/extract.py")
        self.assertEqual(name, "zhihu/extract.py")

    def test_resolve_script_ref_http_rejected(self):
        from site_adapters.services.subscriptions import _resolve_script_ref
        url, name = _resolve_script_ref(
            "http://insecure.example.com/s.js",
            "https://base.test/bundle/",
        )
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

    def test_validate_subscription_url_rejects_private_hosts(self):
        with self.assertRaises(ValueError):
            validate_subscription_url("https://127.0.0.1/bundle.jsonc")

    def test_force_fetch_failure_returns_none(self):
        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            side_effect=Exception("boom"),
        ):
            self.assertIsNone(
                fetch_subscription(
                    "https://example.test/bundle/", name="bundle", force=True
                )
            )

    def test_is_safe_script_key_allows_subdirs(self):
        from site_adapters.services.subscriptions import _is_safe_script_key
        self.assertTrue(_is_safe_script_key("zhihu/extract.py"))
        self.assertTrue(_is_safe_script_key("cleanup.js"))

    def test_is_safe_script_key_rejects_dotdot(self):
        from site_adapters.services.subscriptions import _is_safe_script_key
        self.assertFalse(_is_safe_script_key("../escape.py"))
        self.assertFalse(_is_safe_script_key("foo/../bar.py"))

    def test_is_safe_script_key_rejects_dotfile(self):
        from site_adapters.services.subscriptions import _is_safe_script_key
        self.assertFalse(_is_safe_script_key(".hidden.py"))
        self.assertFalse(_is_safe_script_key("subdir/.hidden.py"))

    def test_collect_script_refs_scans_scripts_array(self):
        from site_adapters.services.subscriptions import _collect_script_refs
        data = {
            "domains": {
                "example.com": {
                    "metadata": {
                        "scripts": [
                            {"path": "before.py", "hook": "before"},
                            {"path": "after.py", "hook": "after"},
                        ]
                    },
                    "snapshot": {
                        "scripts": [
                            {"path": "replace.py", "hook": "replace"},
                        ]
                    },
                }
            }
        }
        refs = _collect_script_refs(data)
        self.assertIn("example.com", refs)
        self.assertEqual(set(refs["example.com"]), {"before.py", "after.py", "replace.py"})

    def test_collect_script_refs_ignores_old_script_field(self):
        """不应收集旧的 script 标量字段。"""
        from site_adapters.services.subscriptions import _collect_script_refs
        data = {
            "domains": {
                "example.com": {
                    "metadata": {
                        "script": "old_script.py",           # 旧格式，忽略
                        "scripts": [{"path": "new_script.py", "hook": "before"}],
                    }
                }
            }
        }
        refs = _collect_script_refs(data)
        self.assertEqual(refs["example.com"], ["new_script.py"])

    def test_cleanup_removes_unreferenced_scripts(self):
        """下载后应清理不再被引用的脚本文件。"""
        from site_adapters.services.subscriptions import _write_adapter_file

        temp_dir = os.path.join(self.base_dir, "adapters", "test-adapter.test")
        scripts_dir = os.path.join(temp_dir, "scripts")
        os.makedirs(scripts_dir, exist_ok=True)

        # 预先放一个旧脚本
        old_script = os.path.join(scripts_dir, "old_script.js")
        with open(old_script, "w") as f:
            f.write("// old")

        data = {
            "domains": {
                "example.com": {
                    "snapshot": {
                        "scripts": [{"path": "new_script.py", "hook": "before"}]
                    }
                }
            }
        }

        def mock_get(url, *args, **kwargs):
            resp = mock.Mock()
            resp.status_code = 200
            resp.text = "# new script"
            resp.headers = {}
            resp.raise_for_status.return_value = None
            return resp

        file_path = os.path.join(temp_dir, "adapters.jsonc")
        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            side_effect=mock_get,
        ):
            _write_adapter_file(file_path, "https://example.test/bundle/", data)

        # 旧脚本应被清理
        self.assertFalse(os.path.exists(old_script))
        # 新脚本应存在
        self.assertTrue(os.path.exists(os.path.join(scripts_dir, "new_script.py")))

    def test_normalize_source_strips_adapter_filename(self):
        from site_adapters.services.subscriptions import _normalize_source_to_directory
        self.assertEqual(
            _normalize_source_to_directory("https://example.test/bundle/adapters.jsonc"),
            "https://example.test/bundle",
        )
        self.assertEqual(
            _normalize_source_to_directory("https://example.test/bundle/"),
            "https://example.test/bundle/",
        )
        self.assertEqual(
            _normalize_source_to_directory("./defaults/adapters.jsonc"),
            "./defaults",
        )
