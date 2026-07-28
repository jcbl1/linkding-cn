import os
import tempfile
from unittest import mock

from django.conf import settings
from django.test import TestCase, override_settings

from bookmarks.services import singlefile


class SingleFileServiceTestCase(TestCase):
    def setUp(self):
        self.temp_html_filepath = None

    def tearDown(self):
        if self.temp_html_filepath and os.path.exists(self.temp_html_filepath):
            os.remove(self.temp_html_filepath)

    def create_test_file(self, *args, **kwargs):
        self.temp_html_filepath = tempfile.mkstemp(suffix=".tmp")[1]

    def successful_singlefile_process(self):
        process = mock.Mock()
        process.returncode = 0
        process.wait.side_effect = lambda timeout=None: open(self.temp_html_filepath, "w").close()
        return process

    def test_create_snapshot_failure(self):
        with mock.patch("subprocess.Popen") as mock_popen:
            mock_popen.side_effect = OSError("missing binary")

            with self.assertRaises(singlefile.SingleFileError):
                singlefile.create_snapshot("http://example.com", "nonexistentfile.tmp")

        # so also check that it raises error if output file isn't created
        with (
            mock.patch("subprocess.Popen"),
            self.assertRaises(singlefile.SingleFileError),
        ):
            singlefile.create_snapshot("http://example.com", "nonexistentfile.tmp")

    def test_create_snapshot_does_not_accept_stale_output_file(self):
        self.create_test_file()
        mock_process = mock.Mock()
        mock_process.wait.return_value = 0

        with (
            mock.patch("subprocess.Popen", return_value=mock_process),
            self.assertRaises(singlefile.SingleFileError),
        ):
            singlefile.create_snapshot("http://example.com", self.temp_html_filepath)

    def test_create_snapshot_accepts_nonzero_exit_code_with_fresh_output_file(self):
        self.create_test_file()
        mock_process = mock.Mock()
        mock_process.returncode = 1
        mock_process.wait.side_effect = lambda timeout=None: open(self.temp_html_filepath, "w").close()

        with mock.patch("subprocess.Popen", return_value=mock_process):
            singlefile.create_snapshot("http://example.com", self.temp_html_filepath)

    def test_create_snapshot_empty_options(self):
        mock_process = self.successful_singlefile_process()
        self.create_test_file()

        with mock.patch("subprocess.Popen", return_value=mock_process) as mock_popen:
            singlefile.create_snapshot("http://example.com", self.temp_html_filepath)

            called_args = mock_popen.call_args.args[0]
            self.assertEqual(called_args[0], "single-file")
            self.assertEqual(called_args[-2], "http://example.com")
            self.assertEqual(called_args[-1], self.temp_html_filepath)
            self.assertIn(
                "--browser-arg=--disable-blink-features=AutomationControlled",
                called_args,
            )
            self.assertIn(f"--user-agent={settings.LD_DEFAULT_USER_AGENT}", called_args)
            self.assertEqual(
                called_args.count("--browser-arg=--headless=new"),
                1,
            )
            self.assertEqual(
                called_args.count("--browser-arg=--user-data-dir=chromium-profile"),
                1,
            )
            self.assertEqual(
                called_args.count("--browser-arg=--no-sandbox"),
                1,
            )
            self.assertEqual(
                called_args.count("--browser-arg=--load-extension=uBOLite.chromium.mv3"),
                1,
            )

    @override_settings(
        LD_SINGLEFILE_OPTIONS='--some-option "some value" --another-option "another value" --third-option="third value"'
    )
    def test_create_snapshot_custom_options(self):
        mock_process = self.successful_singlefile_process()
        self.create_test_file()

        with mock.patch("subprocess.Popen", return_value=mock_process) as mock_popen:
            singlefile.create_snapshot("http://example.com", self.temp_html_filepath)

            called_args = mock_popen.call_args.args[0]
            self.assertEqual(called_args[0], "single-file")
            self.assertEqual(called_args[-2], "http://example.com")
            self.assertEqual(called_args[-1], self.temp_html_filepath)
            self.assertIn("--some-option", called_args)
            self.assertIn("some value", called_args)
            self.assertIn("--another-option", called_args)
            self.assertIn("another value", called_args)
            self.assertIn("--third-option=third value", called_args)
            self.assertIn(
                "--browser-arg=--disable-blink-features=AutomationControlled",
                called_args,
            )
            self.assertIn(f"--user-agent={settings.LD_DEFAULT_USER_AGENT}", called_args)

    def test_create_snapshot_default_timeout_setting(self):
        mock_process = self.successful_singlefile_process()
        self.create_test_file()

        with mock.patch("subprocess.Popen", return_value=mock_process):
            singlefile.create_snapshot("http://example.com", self.temp_html_filepath)

            mock_process.wait.assert_called_with(timeout=120)

    @override_settings(LD_SINGLEFILE_TIMEOUT_SEC=180)
    def test_create_snapshot_custom_timeout_setting(self):
        mock_process = self.successful_singlefile_process()
        self.create_test_file()

        with mock.patch("subprocess.Popen", return_value=mock_process):
            singlefile.create_snapshot("http://example.com", self.temp_html_filepath)

            mock_process.wait.assert_called_with(timeout=180)

    def test_singlefile_args_support_bool_and_numbers(self):
        self.assertEqual(
            singlefile.get_custom_options({
                "singlefile_args": {
                    "--remove-hidden-elements": True,
                    "--browser-wait-delay": 2000,
                    "--remove-frames": False,
                }
            }),
            ["--remove-hidden-elements", "--browser-wait-delay=2000"],
        )

    def test_create_snapshot_injects_request_url_headers_and_browser_script(self):
        mock_process = self.successful_singlefile_process()
        self.create_test_file()

        config = {
            "_request_url": "https://fetch.example.com",
            "headers": {"User-Agent": "UA", "Accept-Language": "zh-CN"},
            "keep_elements": [".article"],
        }

        with mock.patch("subprocess.Popen", return_value=mock_process) as mock_popen:
            singlefile.create_snapshot("https://original.example.com", self.temp_html_filepath, config)

        called_args = mock_popen.call_args.args[0]
        self.assertEqual(called_args[-2], "https://fetch.example.com")
        self.assertIn("--user-agent=UA", called_args)
        self.assertIn("--http-header=Accept-Language: zh-CN", called_args)
        self.assertTrue(any(arg.startswith("--browser-script=") for arg in called_args))

    def test_create_snapshot_passes_browser_script_for_lazy_image_fix(self):
        mock_process = self.successful_singlefile_process()
        self.create_test_file()

        with mock.patch("subprocess.Popen", return_value=mock_process) as mock_popen:
            singlefile.create_snapshot(
                "https://example.com",
                self.temp_html_filepath,
                {"script": "/tmp/custom_snapshot.js"},
            )

        # Browser script is always passed for default lazy image fix
        self.assertTrue(any("--browser-script=" in arg for arg in mock_popen.call_args.args[0]))

    def test_create_snapshot_ignores_missing_cookie_config_file(self):
        mock_process = self.successful_singlefile_process()
        self.create_test_file()

        with mock.patch("subprocess.Popen", return_value=mock_process) as mock_popen:
            singlefile.create_snapshot(
                "https://example.com",
                self.temp_html_filepath,
                {"cookie": {"file": "/tmp/does-not-exist.json"}},
            )

        self.assertFalse(any(arg.startswith("--browser-cookies-file=") for arg in mock_popen.call_args.args[0]))

    def test_generated_browser_script_reads_vendor_file(self):
        script_path = singlefile._build_browser_script({"keep_elements": [".article"]})
        self.addCleanup(lambda: os.path.exists(script_path) and os.remove(script_path))

        with open(script_path, encoding="utf-8") as f:
            script = f.read()

        self.assertIn("window.__linkding_cleanup_config", script)
        self.assertIn("single-file-on-before-capture-request", script)
        self.assertIn('"keep": [".article"]', script)
