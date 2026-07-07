import io
import os.path
import tempfile
from pathlib import Path
from unittest import mock

import requests
from django.conf import settings
from django.test import TestCase, override_settings

from bookmarks.services import favicon_loader

mock_icon_data = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + b"mock_icon_data"
mock_ico_data = bytes([0x00, 0x00, 0x01, 0x00]) + b"mock_ico_data"
mock_svg_data = b"<svg>mock</svg>"


class MockStreamingResponse:
    def __init__(self, data=mock_icon_data, content_type="image/png"):
        self.content = data
        self.chunks = [data]
        self.headers = {"Content-Type": content_type}

    def iter_content(self, **kwargs):
        return self.chunks

    def raise_for_status(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        pass


class FaviconLoaderTestCase(TestCase):
    def setUp(self) -> None:
        self.temp_favicon_folder = tempfile.TemporaryDirectory()
        self.favicon_folder_override = self.settings(
            LD_FAVICON_FOLDER=self.temp_favicon_folder.name
        )
        self.favicon_folder_override.enable()

    def tearDown(self) -> None:
        self.temp_favicon_folder.cleanup()
        self.favicon_folder_override.disable()

    def create_mock_response(self, icon_data=mock_icon_data, content_type="image/png"):
        return MockStreamingResponse(icon_data, content_type)

    def clear_favicon_folder(self):
        folder = Path(settings.LD_FAVICON_FOLDER)
        for file in folder.iterdir():
            if file.is_file():
                file.unlink()

    def get_icon_path(self, filename):
        return Path(os.path.join(settings.LD_FAVICON_FOLDER, filename))

    def icon_exists(self, filename):
        return self.get_icon_path(filename).exists()

    def get_icon_data(self, filename):
        return self.get_icon_path(filename).read_bytes()

    def count_icons(self):
        files = [f for f in os.listdir(settings.LD_FAVICON_FOLDER)
                 if os.path.isfile(os.path.join(settings.LD_FAVICON_FOLDER, f))]
        return len(files)

    # --- fetch_and_save_favicon ---

    def test_fetch_and_save_favicon(self):
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = self.create_mock_response()
            result = favicon_loader.fetch_and_save_favicon("example.com")

            self.assertEqual(result, "example_com.png")
            self.assertTrue(self.icon_exists("example_com.png"))
            self.assertEqual(mock_icon_data, self.get_icon_data("example_com.png"))

    def test_fetch_creates_folder_if_not_exists(self):
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = self.create_mock_response()
            folder = Path(settings.LD_FAVICON_FOLDER)
            folder.rmdir()
            self.assertFalse(folder.exists())

            favicon_loader.fetch_and_save_favicon("example.com")
            self.assertTrue(folder.exists())

    def test_fetch_single_icon_per_domain(self):
        """同一域名只会产生一个文件，不管调用多少次。"""
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = self.create_mock_response()
            favicon_loader.fetch_and_save_favicon("example.com")
            favicon_loader.fetch_and_save_favicon("example.com")

            self.assertEqual(1, self.count_icons())
            self.assertTrue(self.icon_exists("example_com.png"))

    def test_fetch_multiple_icons_for_different_domains(self):
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = self.create_mock_response()
            favicon_loader.fetch_and_save_favicon("example.com")
            favicon_loader.fetch_and_save_favicon("sub.example.com")
            favicon_loader.fetch_and_save_favicon("other-domain.com")

            self.assertEqual(3, self.count_icons())
            self.assertTrue(self.icon_exists("example_com.png"))
            self.assertTrue(self.icon_exists("sub_example_com.png"))
            self.assertTrue(self.icon_exists("other_domain_com.png"))

    def test_fetch_replaces_existing_variant(self):
        """新扩展名的文件会替换旧扩展名的变体。"""
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = self.create_mock_response(
                content_type="image/x-icon",
                icon_data=mock_ico_data,
            )
            favicon_loader.fetch_and_save_favicon("example.com")

        self.assertTrue(self.icon_exists("example_com.ico"))
        self.assertEqual(self.count_icons(), 1)

        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = self.create_mock_response(
                content_type="image/png",
                icon_data=mock_icon_data,
            )
            result = favicon_loader.fetch_and_save_favicon("example.com")

        self.assertEqual(result, "example_com.png")
        self.assertTrue(self.icon_exists("example_com.png"))
        self.assertFalse(self.icon_exists("example_com.ico"))
        self.assertEqual(self.count_icons(), 1)
        self.assertTrue(self.get_icon_data("example_com.png").startswith(bytes([0x89, 0x50, 0x4E, 0x47])))

    def test_fetch_returns_empty_on_request_error(self):
        with mock.patch(
            "requests.get", side_effect=requests.exceptions.RequestException("boom")
        ):
            result = favicon_loader.fetch_and_save_favicon("example.com")
            self.assertEqual(result, "")

    def test_fetch_skips_data_uri_response(self):
        """Provider 返回 data URI 应被视为无效，尝试下一个 provider。"""
        with mock.patch("requests.get") as mock_get:
            data_uri_resp = MockStreamingResponse(
                data=b"data:image/gif;base64,R0lGODlhAQABAIAAAP",
                content_type="text/plain",
            )
            real_resp = self.create_mock_response()
            mock_get.side_effect = [data_uri_resp, real_resp]

            result = favicon_loader.fetch_and_save_favicon("example.com")
            self.assertEqual(result, "example_com.png")
            self.assertEqual(mock_get.call_count, 2)

    # --- Multi-provider fallback ---

    @override_settings(
        LD_FAVICON_PROVIDERS=[
            "https://failing.provider/{domain}",
            "https://fallback.provider/{domain}",
        ]
    )
    def test_multi_provider_fallback(self):
        with mock.patch("requests.get") as mock_get:
            ok_resp = self.create_mock_response()
            mock_get.side_effect = [requests.exceptions.RequestException("fail"), ok_resp]

            result = favicon_loader.fetch_and_save_favicon("example.com")
            self.assertEqual(result, "example_com.png")
            self.assertEqual(mock_get.call_count, 2)

    @override_settings(
        LD_FAVICON_PROVIDERS=[
            "https://custom.icons.com/?url={domain}",
        ]
    )
    def test_custom_provider_with_domain_param(self):
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = self.create_mock_response()
            favicon_loader.fetch_and_save_favicon("example.com")
            mock_get.assert_called_with(
                "https://custom.icons.com/?url=example.com",
                timeout=10,
            )

    @override_settings(
        LD_FAVICON_PROVIDERS=[
            "https://custom.icons.com/?url={url}",
        ]
    )
    def test_custom_provider_with_url_param(self):
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = self.create_mock_response()
            favicon_loader.fetch_and_save_favicon("example.com")
            mock_get.assert_called_with(
                "https://custom.icons.com/?url=https://example.com",
                timeout=10,
            )

    # --- File extension guessing ---

    def test_guess_file_extension(self):
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = self.create_mock_response(content_type="image/png")
            favicon_loader.fetch_and_save_favicon("example.com")
            self.assertTrue(self.icon_exists("example_com.png"))

        self.clear_favicon_folder()

        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = self.create_mock_response(
                content_type="image/x-icon"
            )
            favicon_loader.fetch_and_save_favicon("example.com")
            self.assertTrue(self.icon_exists("example_com.ico"))

    # --- _find_cached_favicon_file ---

    def test_find_cached_favicon_file(self):
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = self.create_mock_response()
            favicon_loader.fetch_and_save_favicon("example.com")

        result = favicon_loader._find_cached_favicon_file("example.com")
        self.assertEqual(result, "example_com.png")

    def test_find_cached_favicon_file_missing(self):
        result = favicon_loader._find_cached_favicon_file("nonexistent.com")
        self.assertIsNone(result)

    def test_find_cached_favicon_file_prefers_svg(self):
        """当同一域名有多个扩展名时，优先返回 SVG。"""
        name = favicon_loader.domain_to_filename("example.com")
        for ext in [".png", ".ico", ".svg"]:
            path = Path(os.path.join(settings.LD_FAVICON_FOLDER, f"{name}{ext}"))
            path.write_bytes(mock_icon_data)

        result = favicon_loader._find_cached_favicon_file("example.com")
        self.assertEqual(result, f"{name}.svg")

    # --- domain_to_filename ---

    def test_domain_to_filename(self):
        self.assertEqual(favicon_loader.domain_to_filename("example.com"), "example_com")
        self.assertEqual(favicon_loader.domain_to_filename("sub.example.com"), "sub_example_com")
        self.assertEqual(favicon_loader.domain_to_filename("a-b.com"), "a_b_com")
