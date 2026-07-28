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
        # 重置健康检查器，标记为已初始化（跳过探测，避免干扰 mock）
        favicon_loader._provider_health.reset()
        with favicon_loader._provider_health._lock:
            favicon_loader._provider_health._initialized = True

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

    # --- find_cached_favicon_file ---

    def testfind_cached_favicon_file(self):
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = self.create_mock_response()
            favicon_loader.fetch_and_save_favicon("example.com")

        result = favicon_loader.find_cached_favicon_file("example.com")
        self.assertEqual(result, "example_com.png")

    def testfind_cached_favicon_file_missing(self):
        result = favicon_loader.find_cached_favicon_file("nonexistent.com")
        self.assertIsNone(result)

    def testfind_cached_favicon_file_prefers_svg(self):
        """当同一域名有多个扩展名时，优先返回 SVG。"""
        name = favicon_loader.domain_to_filename("example.com")
        for ext in [".png", ".ico", ".svg"]:
            path = Path(os.path.join(settings.LD_FAVICON_FOLDER, f"{name}{ext}"))
            path.write_bytes(mock_icon_data)

        result = favicon_loader.find_cached_favicon_file("example.com")
        self.assertEqual(result, f"{name}.svg")

    # --- domain_to_filename ---

    def test_domain_to_filename(self):
        self.assertEqual(favicon_loader.domain_to_filename("example.com"), "example_com")
        self.assertEqual(favicon_loader.domain_to_filename("sub.example.com"), "sub_example_com")
        self.assertEqual(favicon_loader.domain_to_filename("a-b.com"), "a_b_com")

    # --- _calculate_favicon_score ---

    def test_score_prefers_32px(self):
        """32px 应该得到最高分（SVG 除外）。"""
        score_32 = favicon_loader._calculate_favicon_score(32, "image/png", "icon")
        score_16 = favicon_loader._calculate_favicon_score(16, "image/png", "icon")
        score_48 = favicon_loader._calculate_favicon_score(48, "image/png", "icon")
        score_64 = favicon_loader._calculate_favicon_score(64, "image/png", "icon")
        score_128 = favicon_loader._calculate_favicon_score(128, "image/png", "icon")

        self.assertGreater(score_32, score_16)
        self.assertGreater(score_32, score_48)
        self.assertGreater(score_32, score_64)
        self.assertGreater(score_32, score_128)

    def test_score_prefers_closer_to_32px(self):
        """距离 32px 越近，分数越高。"""
        score_16 = favicon_loader._calculate_favicon_score(16, "image/png", "icon")
        score_48 = favicon_loader._calculate_favicon_score(48, "image/png", "icon")
        score_64 = favicon_loader._calculate_favicon_score(64, "image/png", "icon")
        score_128 = favicon_loader._calculate_favicon_score(128, "image/png", "icon")

        # 16px 和 48px 距离相同，但偏好更大尺寸（48px > 16px）
        self.assertGreater(score_48, score_16)
        self.assertGreater(score_48, score_64)
        self.assertGreater(score_64, score_128)

    def test_score_svg_always_best(self):
        """SVG 应该得到最高分。"""
        score_svg = favicon_loader._calculate_favicon_score(None, "image/svg+xml", "icon")
        score_32 = favicon_loader._calculate_favicon_score(32, "image/png", "icon")

        self.assertGreater(score_svg, score_32)

    def test_score_penalizes_apple_touch_icon(self):
        """apple-touch-icon 应该被降分。"""
        score_normal = favicon_loader._calculate_favicon_score(180, "image/png", "icon")
        score_apple = favicon_loader._calculate_favicon_score(180, "image/png", "apple-touch-icon")

        self.assertGreater(score_normal, score_apple)

    # --- _extract_best_ico_frame ---

    def _create_valid_ico(self, entries):
        """创建一个有效的 ICO 文件用于测试。

        entries: list of (width, height, is_png, data)
        """
        import struct

        count = len(entries)
        header = struct.pack("<HHH", 0, 1, count)

        # 计算每个 entry 的 offset
        dir_size = 6 + count * 16
        current_offset = dir_size

        directory = b""
        image_data = b""
        for width, height, is_png, data in entries:
            # Directory entry: width(1), height(1), colors(1), reserved(1), planes(2), bpp(2), size(4), offset(4)
            w = 0 if width == 256 else width
            h = 0 if height == 256 else height
            entry = struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(data), current_offset)
            directory += entry
            image_data += data
            current_offset += len(data)

        return header + directory + image_data

    def test_extract_ico_frame_with_png(self):
        """从 ICO 中提取 PNG 帧。"""
        # 创建一个包含 32x32 PNG 的 ICO
        png_data = b"\x89PNG" + b"\x00" * 100  # 假的 PNG 数据
        ico_data = self._create_valid_ico([(32, 32, True, png_data)])

        result = favicon_loader._extract_best_ico_frame(ico_data)
        self.assertEqual(result, png_data)

    def test_extract_ico_frame_prefers_closest_to_32(self):
        """应该选择最接近 32px 的 PNG 帧。"""
        png_16 = b"\x89PNG" + b"\x00" * 50
        png_32 = b"\x89PNG" + b"\x00" * 100
        png_64 = b"\x89PNG" + b"\x00" * 200
        ico_data = self._create_valid_ico([
            (16, 16, True, png_16),
            (32, 32, True, png_32),
            (64, 64, True, png_64),
        ])

        result = favicon_loader._extract_best_ico_frame(ico_data)
        self.assertEqual(result, png_32)

    def test_extract_ico_frame_skips_bmp(self):
        """应该跳过 BMP 帧，只提取 PNG 帧。"""
        bmp_data = b"BM" + b"\x00" * 100  # BMP 数据
        png_data = b"\x89PNG" + b"\x00" * 100
        ico_data = self._create_valid_ico([
            (32, 32, False, bmp_data),  # BMP，会被跳过
            (256, 256, True, png_data),  # PNG
        ])

        result = favicon_loader._extract_best_ico_frame(ico_data)
        self.assertEqual(result, png_data)

    def test_extract_ico_frame_returns_none_for_no_png(self):
        """如果没有 PNG 帧，应该返回 None。"""
        bmp_data = b"BM" + b"\x00" * 100
        ico_data = self._create_valid_ico([
            (32, 32, False, bmp_data),
        ])

        result = favicon_loader._extract_best_ico_frame(ico_data)
        self.assertIsNone(result)

    def test_extract_ico_frame_returns_none_for_invalid_data(self):
        """无效数据应该返回 None。"""
        self.assertIsNone(favicon_loader._extract_best_ico_frame(b""))
        self.assertIsNone(favicon_loader._extract_best_ico_frame(b"not an ico"))
        self.assertIsNone(favicon_loader._extract_best_ico_frame(b"\x00\x00\x01\x00"))

    def test_fetch_and_save_ico_extracts_png_frame(self):
        """ICO 文件应该被提取为 PNG。"""
        import struct

        # 创建一个有效的 ICO，包含 32x32 PNG 帧
        png_data = b"\x89PNG" + b"\x00" * 100
        ico_data = self._create_valid_ico([(32, 32, True, png_data)])

        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = self.create_mock_response(
                icon_data=ico_data, content_type="image/x-icon"
            )
            result = favicon_loader.fetch_and_save_favicon("example.com")

        # 应该保存为 .png（因为提取了 PNG 帧）
        self.assertEqual(result, "example_com.png")
        self.assertTrue(self.icon_exists("example_com.png"))

        # 文件内容应该是提取的 PNG 数据
        saved_data = self.get_icon_data("example_com.png")
        self.assertEqual(saved_data, png_data)

    def test_extract_ico_frame_returns_none_when_all_bmp(self):
        """所有帧都是 BMP 时，应返回 None，保留原 ICO。"""
        bmp_16 = b"BM" + b"\x00" * 50
        bmp_32 = b"BM" + b"\x00" * 100
        ico_data = self._create_valid_ico([
            (16, 16, False, bmp_16),
            (32, 32, False, bmp_32),
        ])

        result = favicon_loader._extract_best_ico_frame(ico_data)
        self.assertIsNone(result)

    def test_fetch_and_save_ico_keeps_original_when_no_png(self):
        """ICO 只有 BMP 帧时，应保留原 ICO 文件。"""
        bmp_data = b"BM" + b"\x00" * 100
        ico_data = self._create_valid_ico([(32, 32, False, bmp_data)])

        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = self.create_mock_response(
                icon_data=ico_data, content_type="image/x-icon"
            )
            result = favicon_loader.fetch_and_save_favicon("example.com")

        # 无法提取 PNG，保留原 ICO
        self.assertEqual(result, "example_com.ico")
        self.assertTrue(self.icon_exists("example_com.ico"))

    def test_score_32px_beats_128px(self):
        """关键行为回归：新评分下 32px 胜过 128px（旧评分下相反）。"""
        score_32 = favicon_loader._calculate_favicon_score(32, "image/png", "icon")
        score_128 = favicon_loader._calculate_favicon_score(128, "image/png", "icon")

        self.assertGreater(score_32, score_128)
        # 验证具体分值，防止未来改动意外反转
        self.assertEqual(score_32, 195)
        self.assertEqual(score_128, 120)
