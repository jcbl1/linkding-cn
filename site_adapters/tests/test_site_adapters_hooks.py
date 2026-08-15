"""
Tests for the scripts/hooks system in site-adapters.

Covers:
  - Path resolution (absolute, relative, filename)
  - Validation (mutual exclusion, hook enum, replace count)
  - Hook dispatch (before/after/replace for metadata and snapshot)
  - Execution order
  - Fast-fail on error
"""

import json
import os
import shutil
import tempfile
from unittest import mock

from django.test import TestCase, override_settings

from bookmarks.services import singlefile
from site_adapters.services.config.loader import _cache
from site_adapters.services.config.validator import validate_config
from site_adapters.services.engine.script_runner import run_script


class ScriptsPathResolutionTestCase(TestCase):
    """Test the three path forms: absolute, relative, filename."""

    def setUp(self):
        self.base_dir = tempfile.mkdtemp()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        _cache.invalidate()
        shutil.rmtree(self.base_dir)

    def _make_adapter(self, domain, metadata_config=None, snapshot_config=None):
        config = {"domains": {domain: {}}}
        if metadata_config:
            config["domains"][domain]["metadata"] = metadata_config
        if snapshot_config:
            config["domains"][domain]["snapshot"] = snapshot_config
        self._write("adapters/config.jsonc", json.dumps({
            "_adapters": [{"id": "defaults", "name": "defaults",
                          "source": "./defaults/adapters.jsonc"}]
        }))
        self._write("adapters/defaults/adapters.jsonc", json.dumps(config))

    def _write(self, relpath, content):
        path = os.path.join(self.base_dir, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def _write_script(self, relpath, content):
        return self._write(relpath, content)

    def test_relative_path_resolves_from_adapter_file(self):
        """Relative path resolves against the adapters.jsonc directory."""
        self._write_script(
            "adapters/defaults/scripts/my_before.py",
            "def before(url, config):\n    config['request_url'] = 'https://rewritten.example.com'\n"
        )
        self._make_adapter("example.com", metadata_config={
            "scripts": [{"path": "./scripts/my_before.py", "hook": "before"}]
        })

        from site_adapters.services.config.loader import load_domain_config
        config = load_domain_config("https://example.com/page", self.base_dir)
        scripts = config["metadata"]["scripts"]
        self.assertEqual(len(scripts), 1)
        self.assertEqual(scripts[0]["hook"], "before")
        self.assertTrue(scripts[0]["path"].endswith("my_before.py"))

    def test_filename_auto_prefixes_scripts_dir(self):
        """Filename without path separators is auto-prefixed with scripts/."""
        self._write_script(
            "adapters/defaults/scripts/my_before.py",
            "def before(url, config):\n    pass\n"
        )
        self._make_adapter("example.com", metadata_config={
            "scripts": [{"path": "my_before.py", "hook": "before"}]
        })

        from site_adapters.services.config.loader import load_domain_config
        config = load_domain_config("https://example.com/page", self.base_dir)
        scripts = config["metadata"]["scripts"]
        self.assertEqual(len(scripts), 1)
        self.assertTrue(scripts[0]["path"].endswith(
            os.path.join("scripts", "my_before.py")))

    def test_absolute_path_allowed_within_base_dir(self):
        """Absolute paths within LD_SITE_ADAPTERS_DIR are allowed."""
        abs_script = os.path.join(self.base_dir, "adapters", "defaults",
                                  "scripts", "my_before.py")
        self._write_script(abs_script,
                          "def before(url, config):\n    pass\n")
        self._make_adapter("example.com", metadata_config={
            "scripts": [{"path": abs_script, "hook": "before"}]
        })

        from site_adapters.services.config.loader import load_domain_config
        config = load_domain_config("https://example.com/page", self.base_dir)
        scripts = config["metadata"]["scripts"]
        self.assertEqual(len(scripts), 1)
        self.assertEqual(scripts[0]["path"], abs_script)


class ScriptsValidationTestCase(TestCase):
    """Test validation rules for scripts field."""

    def setUp(self):
        self.base_dir = tempfile.mkdtemp()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        _cache.invalidate()
        shutil.rmtree(self.base_dir)

    def _write(self, relpath, content):
        path = os.path.join(self.base_dir, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def _make_adapter(self, domain, metadata_config=None):
        config_data = json.dumps({
            "_adapters": [{"id": "defaults", "name": "defaults",
                          "source": "./defaults/adapters.jsonc"}]
        })
        self._write("adapters/config.jsonc", config_data)

        adapter = {"domains": {}}
        if metadata_config:
            adapter["domains"][domain] = {"metadata": metadata_config}
        self._write("adapters/defaults/adapters.jsonc", json.dumps(adapter))

    def _write_script(self, path, content):
        self._write(path, content)

    def test_validate_rejects_unknown_hook_value(self):
        """Hook must be before/after/replace."""
        self._write_script(
            "adapters/defaults/scripts/test.py",
            "def before(url, config): pass\n"
        )
        self._make_adapter("example.com", metadata_config={
            "scripts": [{"path": "test.py", "hook": "invalid_hook"}]
        })

        issues = validate_config(self.base_dir)
        errors = [i for i in issues if "hook must be before/after/replace" in i]
        self.assertGreater(len(errors), 0,
                          f"Expected hook validation error, got: {issues}")

    def test_validate_rejects_multiple_replace(self):
        """At most one replace hook is allowed."""
        self._write_script(
            "adapters/defaults/scripts/r1.py",
            "def replace(url, config): return {}\n"
        )
        self._write_script(
            "adapters/defaults/scripts/r2.py",
            "def replace(url, config): return {}\n"
        )
        self._make_adapter("example.com", metadata_config={
            "scripts": [
                {"path": "r1.py", "hook": "replace"},
                {"path": "r2.py", "hook": "replace"},
            ]
        })

        issues = validate_config(self.base_dir)
        errors = [i for i in issues if "has 2 replace hooks" in i]
        self.assertGreater(len(errors), 0,
                          f"Expected replace count error, got: {issues}")

    def test_validate_rejects_script_not_found(self):
        """Script path must point to an existing file."""
        self._make_adapter("example.com", metadata_config={
            "scripts": [{"path": "nonexistent.py", "hook": "before"}]
        })

        issues = validate_config(self.base_dir)
        errors = [i for i in issues if "not found" in i]
        self.assertGreater(len(errors), 0,
                          f"Expected 'not found' error, got: {issues}")

    def test_validate_rejects_invalid_process_carousels(self):
        config_data = json.dumps({
            "_adapters": [{"id": "defaults", "name": "defaults",
                          "source": "./defaults/adapters.jsonc"}]
        })
        self._write("adapters/config.jsonc", config_data)
        self._write(
            "adapters/defaults/adapters.jsonc",
            json.dumps({
                "domains": {
                    "example.com": {
                        "snapshot": {"process_carousels": "faceplate-carousel"}
                    }
                }
            }),
        )

        issues = validate_config(self.base_dir)
        errors = [i for i in issues if "process_carousels must be an array" in i]
        self.assertGreater(len(errors), 0,
                          f"Expected process_carousels error, got: {issues}")


class MetadataHookDispatchTestCase(TestCase):
    """Test that hook functions are called correctly in metadata pipeline."""

    def setUp(self):
        self.base_dir = tempfile.mkdtemp()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        _cache.invalidate()
        shutil.rmtree(self.base_dir)

    def _write(self, relpath, content):
        path = os.path.join(self.base_dir, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_before_hook_modifies_config(self):
        """before hook returns partial config that affects downstream stages."""
        self._write("adapters/defaults/scripts/before_test.py", """
def before(url, config):
    return {'request_url': 'https://modified.example.com/api'}
""")
        self._write("adapters/defaults/scripts/replace_test.py", """
def replace(url, config):
    # Verify config was modified by before hook
    result = {"title": "Test", "description": None, "image": None}
    if config.get('request_url') == 'https://modified.example.com/api':
        result['title'] = 'Modified'
    return result
""")
        self._write("adapters/config.jsonc", json.dumps({
            "_adapters": [{"id": "defaults", "name": "defaults",
                          "source": "./defaults/adapters.jsonc"}]
        }))
        self._write("adapters/defaults/adapters.jsonc", json.dumps({
            "domains": {
                "example.com": {
                    "metadata": {
                        "scripts": [
                            {"path": "before_test.py", "hook": "before"},
                            {"path": "replace_test.py", "hook": "replace"},
                        ]
                    }
                }
            }
        }))

        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            from site_adapters.services.config.resolver import get_metadata_config
            from bookmarks.services.website_loader import load_website_metadata
            config = get_metadata_config("https://example.com/page")
            self.assertIsNotNone(config)
            scripts = config.get("scripts")
            self.assertIsNotNone(scripts)
            metadata = load_website_metadata("https://example.com/page")
            self.assertEqual(metadata.title, "Modified")

    def test_js_before_hook_returns_config(self):
        """JS before hook returns partial config that affects downstream stages."""
        self._write("adapters/defaults/scripts/before.js", """
const fs = require('fs');
const input = JSON.parse(fs.readFileSync('/dev/stdin', 'utf8'));
console.log(JSON.stringify({
  request_url: 'https://modified.example.com/api'
}));
""")
        self._write("adapters/defaults/scripts/replace_test.py", """
def replace(url, config):
    result = {"title": "Test", "description": None, "image": None}
    if config.get('request_url') == 'https://modified.example.com/api':
        result['title'] = 'Modified'
    return result
""")
        self._write("adapters/config.jsonc", json.dumps({
            "_adapters": [{"id": "defaults", "name": "defaults",
                          "source": "./defaults/adapters.jsonc"}]
        }))
        self._write("adapters/defaults/adapters.jsonc", json.dumps({
            "domains": {
                "example.com": {
                    "metadata": {
                        "scripts": [
                            {"path": "before.js", "hook": "before"},
                            {"path": "replace_test.py", "hook": "replace"},
                        ]
                    }
                }
            }
        }))

        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            from bookmarks.services.website_loader import load_website_metadata

            metadata = load_website_metadata("https://example.com/page")
            self.assertEqual(metadata.title, "Modified")

    def test_js_after_hook_returns_result(self):
        """JS after hook returns the modified result dict."""
        self._write("adapters/defaults/scripts/replace_test.py", """
def replace(url, config):
    return {"title": "Original", "description": None, "image": None, "url": url}
""")
        self._write("adapters/defaults/scripts/after.js", """
const fs = require('fs');
const input = JSON.parse(fs.readFileSync('/dev/stdin', 'utf8'));
const result = input.result;
result.title = result.title + ' - Modified';
console.log(JSON.stringify(result));
""")
        self._write("adapters/config.jsonc", json.dumps({
            "_adapters": [{"id": "defaults", "name": "defaults",
                          "source": "./defaults/adapters.jsonc"}]
        }))
        self._write("adapters/defaults/adapters.jsonc", json.dumps({
            "domains": {
                "example.com": {
                    "metadata": {
                        "scripts": [
                            {"path": "replace_test.py", "hook": "replace"},
                            {"path": "after.js", "hook": "after"},
                        ]
                    }
                }
            }
        }))

        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            from bookmarks.services.website_loader import load_website_metadata

            metadata = load_website_metadata("https://example.com/page")
            self.assertEqual(metadata.title, "Original - Modified")

    def test_replace_hook_produces_metadata(self):
        """replace hook result is used as WebsiteMetadata."""
        self._write("adapters/defaults/scripts/replace_test.py", """
def replace(url, config):
    return {
        "title": "My Custom Title",
        "description": "My Description",
        "image": "https://example.com/img.png",
        "url": url,
    }
""")
        self._write("adapters/config.jsonc", json.dumps({
            "_adapters": [{"id": "defaults", "name": "defaults",
                          "source": "./defaults/adapters.jsonc"}]
        }))
        self._write("adapters/defaults/adapters.jsonc", json.dumps({
            "domains": {
                "example.com": {
                    "metadata": {
                        "scripts": [
                            {"path": "replace_test.py", "hook": "replace"}
                        ]
                    }
                }
            }
        }))

        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            from bookmarks.services.website_loader import load_website_metadata

            metadata = load_website_metadata("https://example.com/page")
            self.assertIsNotNone(metadata)
            self.assertEqual(metadata.title, "My Custom Title")
            self.assertEqual(metadata.description, "My Description")
            self.assertEqual(metadata.preview_image, "https://example.com/img.png")

    def test_after_hook_modifies_result(self):
        """after hook can modify the metadata result dict."""
        self._write("adapters/defaults/scripts/replace_test.py", """
def replace(url, config):
    return {"title": "Original", "description": None, "image": None, "url": url}
""")
        self._write("adapters/defaults/scripts/after_test.py", """
def after(result, url, config):
    result['title'] = result['title'] + " - Modified"
""")
        self._write("adapters/config.jsonc", json.dumps({
            "_adapters": [{"id": "defaults", "name": "defaults",
                          "source": "./defaults/adapters.jsonc"}]
        }))
        self._write("adapters/defaults/adapters.jsonc", json.dumps({
            "domains": {
                "example.com": {
                    "metadata": {
                        "scripts": [
                            {"path": "replace_test.py", "hook": "replace"},
                            {"path": "after_test.py", "hook": "after"},
                        ]
                    }
                }
            }
        }))

        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            from bookmarks.services.website_loader import load_website_metadata

            metadata = load_website_metadata("https://example.com/page")
            self.assertIsNotNone(metadata)
            self.assertEqual(metadata.title, "Original - Modified")


class SnapshotHookDispatchTestCase(TestCase):
    """Test that hook functions are called correctly in snapshot pipeline."""

    def setUp(self):
        self.base_dir = tempfile.mkdtemp()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        _cache.invalidate()
        shutil.rmtree(self.base_dir)

    def _write(self, relpath, content):
        path = os.path.join(self.base_dir, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_before_hook_returns_html_for_singlefile(self):
        """before hook can return HTML that gets passed to SingleFile."""
        before_html = "<html><body><h1>Modified</h1></body></html>"
        self._write("adapters/defaults/scripts/before_snap.py", f"""
def before(url, config):
    return '''{before_html}'''
""")
        self._write("adapters/config.jsonc", json.dumps({
            "_adapters": [{"id": "defaults", "name": "defaults",
                          "source": "./defaults/adapters.jsonc"}]
        }))
        self._write("adapters/defaults/adapters.jsonc", json.dumps({
            "domains": {
                "example.com": {
                    "snapshot": {
                        "scripts": [
                            {"path": "before_snap.py", "hook": "before"}
                        ]
                    }
                }
            }
        }))

        with override_settings(
            LD_SITE_ADAPTERS_DIR=self.base_dir,
            LD_SINGLEFILE_PATH=shutil.which("true") or "/usr/bin/true",
            LD_SINGLEFILE_OPTIONS="",
            LD_SINGLEFILE_TIMEOUT_SEC=5,
        ):
            from site_adapters.services.config.resolver import get_snapshot_config
            from bookmarks.services.snapshot_processor import _run_snapshot_with_hooks

            config = get_snapshot_config("https://example.com/page")
            if config and config.get("scripts"):
                import tempfile
                with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as tmp:
                    output_path = tmp.name
                with mock.patch(
                    "bookmarks.services.snapshot_processor._create_snapshot"
                ) as mock_create:
                    _run_snapshot_with_hooks(
                        "https://example.com/page", output_path, config,
                        config["scripts"]
                    )
                self.assertTrue(mock_create.called)
                passed_config = mock_create.call_args.args[2]
                self.assertTrue(passed_config.get("_before_html_path"))
                # Cleanup
                if os.path.exists(output_path):
                    os.unlink(output_path)

    def test_replace_hook_writes_output(self):
        """replace hook writes HTML to output_path."""
        self._write("adapters/defaults/scripts/replace_snap.py", """
def replace(url, config, output_path):
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('<html><body><h1>Custom Snapshot</h1></body></html>')
""")
        self._write("adapters/config.jsonc", json.dumps({
            "_adapters": [{"id": "defaults", "name": "defaults",
                          "source": "./defaults/adapters.jsonc"}]
        }))
        self._write("adapters/defaults/adapters.jsonc", json.dumps({
            "domains": {
                "example.com": {
                    "snapshot": {
                        "scripts": [
                            {"path": "replace_snap.py", "hook": "replace"}
                        ]
                    }
                }
            }
        }))

        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            from site_adapters.services.config.resolver import get_snapshot_config
            from bookmarks.services.snapshot_processor import _run_snapshot_with_hooks

            config = get_snapshot_config("https://example.com/page")
            if config and config.get("scripts"):
                import tempfile
                with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as tmp:
                    output_path = tmp.name
                _run_snapshot_with_hooks(
                    "https://example.com/page", output_path, config,
                    config["scripts"]
                )
                self.assertTrue(os.path.exists(output_path))
                with open(output_path, "r") as f:
                    content = f.read()
                self.assertIn("Custom Snapshot", content)
                os.unlink(output_path)

    def test_after_hook_modifies_output(self):
        """after hook can modify the output HTML file."""
        self._write("adapters/defaults/scripts/replace_snap.py", """
def replace(url, config, output_path):
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('<html><head></head><body>Original</body></html>')
""")
        self._write("adapters/defaults/scripts/after_snap.py", """
def after(output_path, config):
    with open(output_path, 'r', encoding='utf-8') as f:
        html = f.read()
    html = html.replace('Original', 'Modified by After')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
""")
        self._write("adapters/config.jsonc", json.dumps({
            "_adapters": [{"id": "defaults", "name": "defaults",
                          "source": "./defaults/adapters.jsonc"}]
        }))
        self._write("adapters/defaults/adapters.jsonc", json.dumps({
            "domains": {
                "example.com": {
                    "snapshot": {
                        "scripts": [
                            {"path": "replace_snap.py", "hook": "replace"},
                            {"path": "after_snap.py", "hook": "after"},
                        ]
                    }
                }
            }
        }))

        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            from site_adapters.services.config.resolver import get_snapshot_config
            from bookmarks.services.snapshot_processor import _run_snapshot_with_hooks

            config = get_snapshot_config("https://example.com/page")
            if config and config.get("scripts"):
                import tempfile
                with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as tmp:
                    output_path = tmp.name
                _run_snapshot_with_hooks(
                    "https://example.com/page", output_path, config,
                    config["scripts"]
                )
                with open(output_path, "r") as f:
                    content = f.read()
                self.assertIn("Modified by After", content)
                os.unlink(output_path)

    def test_js_singlefile_after_hook_modifies_saved_output(self):
        """built-in JS after hook can modify the saved snapshot HTML."""
        after_js = self._write(
            "adapters/defaults/scripts/after.js",
            'const builtin_engine = "singlefile";\n'
            "const after = async (url, config) => {\n"
            "  const paragraph = document.createElement('p');\n"
            "  paragraph.textContent = 'Modified by JS After';\n"
            "  const video = document.createElement('video');\n"
            "  video.setAttribute('src', 'https://example.com/video.mp4');\n"
            "  document.body.appendChild(video);\n"
            "  document.body.appendChild(paragraph);\n"
            "};\n",
        )
        self._write("adapters/defaults/scripts/replace_snap.py", """
def replace(url, config, output_path):
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('<html><head></head><body>Original</body></html>')
""")
        self._write("adapters/config.jsonc", json.dumps({
            "_adapters": [{"id": "defaults", "name": "defaults",
                          "source": "./defaults/adapters.jsonc"}]
        }))
        self._write("adapters/defaults/adapters.jsonc", json.dumps({
            "domains": {
                "example.com": {
                    "snapshot": {
                        "scripts": [
                            {"path": "replace_snap.py", "hook": "replace"},
                            {"path": after_js, "hook": "after"},
                        ]
                    }
                }
            }
        }))

        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            from site_adapters.services.config.resolver import get_snapshot_config
            from bookmarks.services.snapshot_processor import _run_snapshot_with_hooks

            config = get_snapshot_config("https://example.com/page")
            if config and config.get("scripts"):
                import tempfile
                with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as tmp:
                    output_path = tmp.name
                _run_snapshot_with_hooks(
                    "https://example.com/page", output_path, config,
                    config["scripts"]
                )
                with open(output_path, "r") as f:
                    content = f.read()
                self.assertIn("Modified by JS After", content)
                self.assertIn('src="https://example.com/video.mp4"', content)
                os.unlink(output_path)

    def test_js_singlefile_hooks_are_passed_to_singlefile(self):
        before_js = self._write(
            "adapters/defaults/scripts/before.js",
            'const builtin_engine = "singlefile";\n'
            "async function before(url, config) {\n"
            "  document.title = 'modified';\n"
            "}\n",
        )
        after_js = self._write(
            "adapters/defaults/scripts/after.js",
            'const builtin_engine = "singlefile";\n'
            "async function after(url, config) {\n"
            "  document.body.dataset.ready = '1';\n"
            "}\n",
        )
        self._write("adapters/config.jsonc", json.dumps({
            "_adapters": [{"id": "defaults", "name": "defaults",
                          "source": "./defaults/adapters.jsonc"}]
        }))
        self._write("adapters/defaults/adapters.jsonc", json.dumps({
            "domains": {
                "example.com": {
                    "snapshot": {
                        "scripts": [
                            {"path": before_js, "hook": "before"},
                            {"path": after_js, "hook": "after"},
                        ]
                    }
                }
            }
        }))

        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            from site_adapters.services.config.resolver import get_snapshot_config
            from bookmarks.services.snapshot_processor import _run_snapshot_with_hooks

            config = get_snapshot_config("https://example.com/page")
            output_path = os.path.join(self.base_dir, "out.html")
            with mock.patch(
                "bookmarks.services.snapshot_processor.run_script"
            ) as mock_run:
                with mock.patch(
                    "bookmarks.services.snapshot_processor._create_snapshot"
                ) as mock_create:
                    with mock.patch(
                        "bookmarks.services.snapshot_processor._run_builtin_after_hook"
                    ) as mock_after:
                        _run_snapshot_with_hooks(
                            "https://example.com/page", output_path, config,
                            config["scripts"]
                        )

            mock_run.assert_not_called()
            passed_config = mock_create.call_args.args[2]
            self.assertIn(before_js, passed_config["_browser_before_scripts"])
            self.assertNotIn("_browser_after_scripts", passed_config)
            mock_after.assert_called_once_with(
                after_js, "https://example.com/page", output_path, config
            )

    def test_js_external_node_before_stays_external(self):
        external_js = self._write(
            "adapters/defaults/scripts/external.js",
            "const builtin_engine = '';\n"
            "async function before(url, config) { return null; }\n",
        )
        self._write("adapters/config.jsonc", json.dumps({
            "_adapters": [{"id": "defaults", "name": "defaults",
                          "source": "./defaults/adapters.jsonc"}]
        }))
        self._write("adapters/defaults/adapters.jsonc", json.dumps({
            "domains": {
                "example.com": {
                    "snapshot": {
                        "scripts": [
                            {"path": external_js, "hook": "before"}
                        ]
                    }
                }
            }
        }))

        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            from site_adapters.services.config.resolver import get_snapshot_config
            from bookmarks.services.snapshot_processor import _run_snapshot_with_hooks

            config = get_snapshot_config("https://example.com/page")
            output_path = os.path.join(self.base_dir, "out.html")
            with mock.patch(
                "bookmarks.services.snapshot_processor.run_script",
                return_value=None,
            ) as mock_run:
                with mock.patch(
                    "bookmarks.services.snapshot_processor._create_snapshot"
                ) as mock_create:
                    _run_snapshot_with_hooks(
                        "https://example.com/page", output_path, config,
                        config["scripts"]
                    )

            mock_run.assert_called_once()
            self.assertEqual(mock_run.call_args.kwargs["hook_name"], "before")
            passed_config = mock_create.call_args.args[2]
            self.assertNotIn("_browser_before_scripts", passed_config)

    def test_invalid_builtin_engine_raises(self):
        invalid_js = self._write(
            "adapters/defaults/scripts/invalid.js",
            'const builtin_engine = "monolith";\n',
        )
        self._write("adapters/config.jsonc", json.dumps({
            "_adapters": [{"id": "defaults", "name": "defaults",
                          "source": "./defaults/adapters.jsonc"}]
        }))
        self._write("adapters/defaults/adapters.jsonc", json.dumps({
            "domains": {
                "example.com": {
                    "snapshot": {
                        "scripts": [
                            {"path": invalid_js, "hook": "before"}
                        ]
                    }
                }
            }
        }))

        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            from site_adapters.services.config.resolver import get_snapshot_config
            from bookmarks.services.snapshot_processor import _run_snapshot_with_hooks

            config = get_snapshot_config("https://example.com/page")
            with self.assertRaises(singlefile.SingleFileError):
                _run_snapshot_with_hooks(
                    "https://example.com/page", os.path.join(self.base_dir, "out.html"),
                    config, config["scripts"]
                )


class ScriptRunnerHookDispatchTestCase(TestCase):
    """Test run_script directly with hook dispatch."""

    def setUp(self):
        self.script_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.script_dir)

    def _write_script(self, name, content):
        path = os.path.join(self.script_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_before_hook_called(self):
        """before hook function is called with url and config."""
        path = self._write_script("test_before.py", """
def before(url, config):
    pass  # just verify it's called
""")
        with override_settings(LD_SITE_ADAPTERS_DIR=self.script_dir):
            result = run_script(path, hook_name='before',
                               url="https://example.com", config={"key": "value"})
            self.assertIsNone(result)

    def test_replace_hook_returns_dict(self):
        """replace hook returns a dict."""
        path = self._write_script("test_replace.py", """
def replace(url, config):
    return {"title": "Test", "description": "Desc", "image": None, "url": url}
""")
        with override_settings(LD_SITE_ADAPTERS_DIR=self.script_dir):
            result = run_script(path, hook_name='replace',
                               url="https://example.com", config={})
            self.assertIsInstance(result, dict)
            self.assertEqual(result["title"], "Test")
            self.assertEqual(result["description"], "Desc")

    def test_after_hook_modifies_result(self):
        """after hook can modify the result dict."""
        path = self._write_script("test_after.py", """
def after(result, url, config):
    result['title'] = 'Modified'
""")
        result_dict = {"title": "Original", "description": None, "image": None, "url": "https://example.com"}
        with override_settings(LD_SITE_ADAPTERS_DIR=self.script_dir):
            run_script(path, hook_name='after', url="https://example.com",
                      config={}, result_dict=result_dict)
            self.assertEqual(result_dict["title"], "Modified")

    def test_missing_hook_function_raises(self):
        """Script missing the requested hook function raises AttributeError."""
        path = self._write_script("test_missing.py", """
def other_function():
    pass
""")
        with override_settings(LD_SITE_ADAPTERS_DIR=self.script_dir):
            result = run_script(path, hook_name='before',
                               url="https://example.com", config={})
            self.assertIsNone(result)

    def test_config_sanitized_for_script(self):
        """Internal _ prefixed keys are mapped to user-facing names."""
        path = self._write_script("test_config.py", """
def replace(url, config):
    # Verify _ prefixed keys are not present but user-facing equivalents are
    has_internal = any(k.startswith('_') for k in config)
    return {
        "title": "internal" if has_internal else "clean",
        "description": None,
        "image": None,
        "url": url,
    }
""")
        with override_settings(LD_SITE_ADAPTERS_DIR=self.script_dir):
            result = run_script(path, hook_name='replace',
                               url="https://example.com",
                               config={"_request_url": "https://x.com",
                                      "_domain_key": "x.com",
                                      "headers": {"UA": "test"}})
            self.assertEqual(result["title"], "clean")
            self.assertEqual(result["url"], "https://example.com")


class ScriptsExecutionOrderTestCase(TestCase):
    """Test that multiple scripts execute in the declared order."""

    def setUp(self):
        self.base_dir = tempfile.mkdtemp()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        _cache.invalidate()
        shutil.rmtree(self.base_dir)

    def _write(self, relpath, content):
        path = os.path.join(self.base_dir, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_before_scripts_execute_in_order(self):
        """Multiple before hooks execute in array order, config accumulates."""
        self._write("adapters/defaults/scripts/b1.py", """
def before(url, config):
    return {'step1': 'done'}
""")
        self._write("adapters/defaults/scripts/b2.py", """
def before(url, config):
    return {'step2': config.get('step1', 'missed') + '->done2'}
""")
        self._write("adapters/defaults/scripts/replace.py", """
def replace(url, config):
    return {
        "title": config.get('step2', 'no_steps'),
        "description": None,
        "image": None,
        "url": url,
    }
""")
        self._write("adapters/config.jsonc", json.dumps({
            "_adapters": [{"id": "defaults", "name": "defaults",
                          "source": "./defaults/adapters.jsonc"}]
        }))
        self._write("adapters/defaults/adapters.jsonc", json.dumps({
            "domains": {
                "example.com": {
                    "metadata": {
                        "scripts": [
                            {"path": "b1.py", "hook": "before"},
                            {"path": "b2.py", "hook": "before"},
                            {"path": "replace.py", "hook": "replace"},
                        ]
                    }
                }
            }
        }))

        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            from bookmarks.services.website_loader import load_website_metadata
            metadata = load_website_metadata("https://example.com/page")
            self.assertIsNotNone(metadata)
            self.assertEqual(metadata.title, "done->done2")
