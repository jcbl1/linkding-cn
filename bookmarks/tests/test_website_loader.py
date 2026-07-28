from unittest import mock
import json
import os
import tempfile

import requests
from django.test import TestCase

from bookmarks.services import website_loader


class MockStreamingResponse:
    def __init__(
        self,
        num_chunks,
        chunk_size,
        insert_head_after_chunk=None,
        status_code=200,
    ):
        self.chunks = []
        self.status_code = status_code
        for index in range(num_chunks):
            chunk = "".zfill(chunk_size)
            self.chunks.append(chunk.encode("utf-8"))

            if index == insert_head_after_chunk:
                self.chunks.append(b"</head>")

    def iter_content(self, **kwargs):
        return self.chunks

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        pass


class WebsiteLoaderTestCase(TestCase):
    def setUp(self):
        # clear cached metadata before test run
        website_loader._load_website_metadata_cached.cache_clear()
        website_loader._load_website_metadata_config_cached.cache_clear()
        from site_adapters.services.config.loader import _cache
        _cache.invalidate()

    def render_html_document(
        self, title, description="", og_description="", og_image=""
    ):
        meta_description = (
            f'<meta name="description" content="{description}">' if description else ""
        )
        meta_og_description = (
            f'<meta property="og:description" content="{og_description}">'
            if og_description
            else ""
        )
        meta_og_image = (
            f'<meta property="og:image" content="{og_image}">' if og_image else ""
        )
        return f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <title>{title}</title>
            {meta_description}
            {meta_og_description}
            {meta_og_image}
        </head>
        <body></body>
        </html>
        """

    def test_load_page_returns_content(self):
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = MockStreamingResponse(
                num_chunks=10, chunk_size=1024
            )
            content = website_loader.load_page("https://example.com")

            expected_content_size = 10 * 1024
            self.assertEqual(expected_content_size, len(content))

    def test_load_page_limits_large_documents(self):
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = MockStreamingResponse(
                num_chunks=10, chunk_size=1024 * 1000
            )
            content = website_loader.load_page("https://example.com")

            # Should have read six chunks, after which content exceeds the max of 5MB
            expected_content_size = 6 * 1024 * 1000
            self.assertEqual(expected_content_size, len(content))

    def test_load_page_stops_reading_at_end_of_head(self):
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = MockStreamingResponse(
                num_chunks=10, chunk_size=1024 * 1000, insert_head_after_chunk=0
            )
            content = website_loader.load_page("https://example.com")

            # Should have read first chunk, and second chunk containing closing head tag
            expected_content_size = 1 * 1024 * 1000 + len("</head>")
            self.assertEqual(expected_content_size, len(content))

    def test_load_page_removes_bytes_after_end_of_head(self):
        with mock.patch("requests.get") as mock_get:
            mock_response = MockStreamingResponse(num_chunks=1, chunk_size=0)
            mock_response.chunks[0] = "<head>人</head>".encode()
            # add a single byte that can't be decoded to utf-8
            mock_response.chunks[0] += 0xFF.to_bytes(1, "big")
            mock_get.return_value = mock_response
            content = website_loader.load_page("https://example.com")

            # verify that byte after head was removed, content parsed as utf-8
            self.assertEqual(content, "<head>人</head>")

    def test_load_page_raises_retryable_error_on_timeout(self):
        with (
            mock.patch("requests.get", side_effect=requests.exceptions.Timeout("boom")),
            self.assertRaises(website_loader.RetryableMetadataError),
        ):
            website_loader.load_page("https://example.com")

    def test_load_page_raises_retryable_error_on_rate_limit(self):
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = MockStreamingResponse(
                num_chunks=1, chunk_size=128, status_code=429
            )

            with self.assertRaises(website_loader.RetryableMetadataError):
                website_loader.load_page("https://example.com")

    def test_load_page_raises_retryable_error_on_server_error(self):
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = MockStreamingResponse(
                num_chunks=1, chunk_size=128, status_code=500
            )

            with self.assertRaises(website_loader.RetryableMetadataError):
                website_loader.load_page("https://example.com")

    def test_load_website_metadata(self):
        with mock.patch(
            "bookmarks.services.website_loader.load_page"
        ) as mock_load_page:
            mock_load_page.return_value = self.render_html_document(
                "test title", "test description"
            )
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertEqual("test title", metadata.title)
            self.assertEqual("test description", metadata.description)
            self.assertIsNone(metadata.preview_image)

    def test_load_website_metadata_trims_title_and_description(self):
        with mock.patch(
            "bookmarks.services.website_loader.load_page"
        ) as mock_load_page:
            mock_load_page.return_value = self.render_html_document(
                "  test title  ", "  test description  "
            )
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertEqual("test title", metadata.title)
            self.assertEqual("test description", metadata.description)

    def test_load_website_metadata_using_og_description(self):
        with mock.patch(
            "bookmarks.services.website_loader.load_page"
        ) as mock_load_page:
            mock_load_page.return_value = self.render_html_document(
                "test title", "", og_description="test og description"
            )
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertEqual("test title", metadata.title)
            self.assertEqual("test og description", metadata.description)

    def test_load_website_metadata_using_og_image(self):
        with mock.patch(
            "bookmarks.services.website_loader.load_page"
        ) as mock_load_page:
            mock_load_page.return_value = self.render_html_document(
                "test title", og_image="http://example.com/image.jpg"
            )
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertEqual("http://example.com/image.jpg", metadata.preview_image)

    def test_load_website_metadata_gets_absolute_og_image_path_when_path_starts_with_dots(
        self,
    ):
        with mock.patch(
            "bookmarks.services.website_loader.load_page"
        ) as mock_load_page:
            mock_load_page.return_value = self.render_html_document(
                "test title", og_image="../image.jpg"
            )
            metadata = website_loader.load_website_metadata(
                "https://example.com/a/b/page.html"
            )
            self.assertEqual("https://example.com/a/image.jpg", metadata.preview_image)

    def test_load_website_metadata_gets_absolute_og_image_path_when_path_starts_with_slash(
        self,
    ):
        with mock.patch(
            "bookmarks.services.website_loader.load_page"
        ) as mock_load_page:
            mock_load_page.return_value = self.render_html_document(
                "test title", og_image="/image.jpg"
            )
            metadata = website_loader.load_website_metadata(
                "https://example.com/a/b/page.html"
            )
            self.assertEqual("https://example.com/image.jpg", metadata.preview_image)

    def test_load_website_metadata_prefers_og_description_over_meta_description(self):
        with mock.patch(
            "bookmarks.services.website_loader.load_page"
        ) as mock_load_page:
            mock_load_page.return_value = self.render_html_document(
                "test title", "test description", og_description="test og description"
            )
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertEqual("test title", metadata.title)
            # og:description is now preferred (fivefilters-informed priority)
            self.assertEqual("test og description", metadata.description)

    def test_load_website_metadata_returns_empty_metadata_when_script_returns_none(
        self,
    ):
        with (
            mock.patch(
                "bookmarks.services.website_loader.get_metadata_config",
                return_value={"script": "custom.py"},
            ),
            mock.patch("os.path.exists", return_value=True),
            mock.patch(
                "bookmarks.services.website_loader.run_script",
                return_value=None,
            ),
        ):
            metadata = website_loader.load_website_metadata("https://x.com/example")

        self.assertEqual("https://x.com/example", metadata.url)
        self.assertIsNone(metadata.title)
        self.assertIsNone(metadata.description)
        self.assertIsNone(metadata.preview_image)

    def test_website_metadata_ignore_cache(self):
        expected_html = '<html><head><title>Test Title</title><meta name="description" content="Test Description"><meta property="og:image" content="/images/test.jpg"></head></html>'

        with mock.patch.object(
            website_loader, "load_page", return_value=expected_html
        ) as mock_load_page:
            website_loader.load_website_metadata("https://example.com")
            mock_load_page.assert_called_once()

            website_loader.load_website_metadata("https://example.com")
            mock_load_page.assert_called_once()

            website_loader.load_website_metadata(
                "https://example.com", ignore_cache=True
            )
            self.assertEqual(mock_load_page.call_count, 2)

    def test_website_metadata_with_config_uses_cache(self):
        expected_html = '<html><head><title>Test Title</title></head></html>'
        config = {"http": {"timeout": 3}}

        with (
            mock.patch(
                "bookmarks.services.website_loader.get_metadata_config",
                return_value=config,
            ),
            mock.patch.object(
                website_loader, "load_page", return_value=expected_html
            ) as mock_load_page,
        ):
            website_loader.load_website_metadata("https://example.com")
            website_loader.load_website_metadata("https://example.com")

        mock_load_page.assert_called_once()

    def test_website_metadata_uses_request_rewrite_and_selectors(self):
        html = """
        <html><head></head><body>
          <h1 class="title">Selected title</h1>
          <p class="desc">Selected description</p>
          <img class="cover" src="/cover.jpg">
        </body></html>
        """
        config = {
            "_request_url": "https://fetch.example.com/item",
            "_rewrite_url": "https://final.example.com/item",
            "select_title": [".title"],
            "select_description": [".desc"],
            "select_image": [".cover"],
            "headers": {},
        }

        with (
            mock.patch(
                "bookmarks.services.website_loader.get_metadata_config",
                return_value=config,
            ),
            mock.patch.object(
                website_loader, "load_page", return_value=html
            ) as mock_load_page,
        ):
            metadata = website_loader.load_website_metadata("https://original.example.com/item")

        mock_load_page.assert_called_once_with("https://fetch.example.com/item", config)
        self.assertEqual(metadata.url, "https://final.example.com/item")
        self.assertEqual(metadata.title, "Selected title")
        self.assertEqual(metadata.description, "Selected description")
        self.assertEqual(metadata.preview_image, "https://fetch.example.com/cover.jpg")

    def test_build_request_cookies_prefers_cookie_config_file(self):
        fd, path = tempfile.mkstemp()
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        with open(path, "w", encoding="utf-8") as f:
            json.dump([{"name": "a", "value": "1"}, {"name": "b", "value": "2"}], f)

        cookies = website_loader.build_request_cookies({
            "cookie": {"file": path},
            "headers": {"Cookie": "ignored=1"},
        })

        self.assertEqual(cookies, {"a": "1", "b": "2"})

    def test_load_website_metadata_for_test_returns_selector_sources(self):
        html = """
        <html><body>
          <h1 class="title">Selected title</h1>
          <p class="desc">Selected description</p>
        </body></html>
        """
        config = {
            "select_title": [".title"],
            "select_description": [".desc"],
            "headers": {},
        }

        with (
            mock.patch(
                "bookmarks.services.website_loader.get_metadata_config",
                return_value=config,
            ),
            mock.patch.object(website_loader, "load_page", return_value=html),
        ):
            metadata, sources, returned_config = website_loader.load_website_metadata_for_test("https://example.com")

        self.assertEqual(metadata.title, "Selected title")
        self.assertEqual(sources["title"]["selector"], ".title")
        self.assertEqual(sources["description"]["selector"], ".desc")
        self.assertIs(returned_config, config)


class ContentTypeDetectionTestCase(TestCase):
    def test_detect_content_type_returns_content_type_from_head_request(self):
        with mock.patch("requests.head") as mock_head:
            mock_response = mock.Mock()
            mock_response.status_code = 200
            mock_response.headers = {"Content-Type": "application/pdf"}
            mock_head.return_value = mock_response

            result = website_loader.detect_content_type("https://example.com/doc.pdf")

            self.assertEqual(result, "application/pdf")
            mock_head.assert_called_once()

    def test_detect_content_type_strips_charset(self):
        with mock.patch("requests.head") as mock_head:
            mock_response = mock.Mock()
            mock_response.status_code = 200
            mock_response.headers = {"Content-Type": "text/html; charset=utf-8"}
            mock_head.return_value = mock_response

            result = website_loader.detect_content_type("https://example.com")

            self.assertEqual(result, "text/html")

    def test_detect_content_type_returns_lowercase(self):
        with mock.patch("requests.head") as mock_head:
            mock_response = mock.Mock()
            mock_response.status_code = 200
            mock_response.headers = {"Content-Type": "Application/PDF"}
            mock_head.return_value = mock_response

            result = website_loader.detect_content_type("https://example.com/doc.pdf")

            self.assertEqual(result, "application/pdf")

    def test_detect_content_type_falls_back_to_get_when_head_fails(self):
        with (
            mock.patch("requests.head") as mock_head,
            mock.patch("requests.get") as mock_get,
        ):
            mock_head.side_effect = requests.RequestException("HEAD failed")

            mock_response = mock.Mock()
            mock_response.status_code = 200
            mock_response.headers = {"Content-Type": "application/pdf"}
            mock_response.__enter__ = mock.Mock(return_value=mock_response)
            mock_response.__exit__ = mock.Mock(return_value=False)
            mock_get.return_value = mock_response

            result = website_loader.detect_content_type("https://example.com/doc.pdf")

            self.assertEqual(result, "application/pdf")
            mock_head.assert_called_once()
            mock_get.assert_called_once()

    def test_detect_content_type_returns_none_when_both_head_and_get_fail(self):
        with (
            mock.patch("requests.head") as mock_head,
            mock.patch("requests.get") as mock_get,
        ):
            mock_head.side_effect = requests.RequestException("HEAD failed")
            mock_get.side_effect = requests.RequestException("GET failed")

            result = website_loader.detect_content_type("https://example.com/doc.pdf")

            self.assertIsNone(result)

    def test_detect_content_type_returns_none_for_non_200_status(self):
        with (
            mock.patch("requests.head") as mock_head,
            mock.patch("requests.get") as mock_get,
        ):
            mock_head_response = mock.Mock()
            mock_head_response.status_code = 404
            mock_head.return_value = mock_head_response

            mock_get_response = mock.Mock()
            mock_get_response.status_code = 404
            mock_get_response.__enter__ = mock.Mock(return_value=mock_get_response)
            mock_get_response.__exit__ = mock.Mock(return_value=False)
            mock_get.return_value = mock_get_response

            result = website_loader.detect_content_type("https://example.com/doc.pdf")

            self.assertIsNone(result)

    def test_is_pdf_content_type(self):
        self.assertTrue(website_loader.is_pdf_content_type("application/pdf"))
        self.assertTrue(website_loader.is_pdf_content_type("application/x-pdf"))
        self.assertFalse(website_loader.is_pdf_content_type("text/html"))
        self.assertFalse(website_loader.is_pdf_content_type(None))
        self.assertFalse(website_loader.is_pdf_content_type(""))


class MetadataFallbacksTestCase(TestCase):
    """Test Twitter card and JSON-LD metadata fallbacks."""

    def setUp(self):
        website_loader._load_website_metadata_cached.cache_clear()
        website_loader._load_website_metadata_config_cached.cache_clear()
        from site_adapters.services.config.loader import _cache
        _cache.invalidate()

    def test_twitter_title_fallback(self):
        html = '<html><head><meta name="twitter:title" content="TW Title"></head><body></body></html>'
        with mock.patch.object(website_loader, "load_page", return_value=html):
            metadata = website_loader.load_website_metadata("https://example.com")
        self.assertEqual(metadata.title, "TW Title")

    def test_twitter_description_fallback(self):
        html = '<html><head><title>T</title><meta name="twitter:description" content="TW Desc"></head><body></body></html>'
        with mock.patch.object(website_loader, "load_page", return_value=html):
            metadata = website_loader.load_website_metadata("https://example.com")
        self.assertEqual(metadata.description, "TW Desc")

    def test_twitter_image_fallback(self):
        html = '<html><head><title>T</title><meta name="twitter:image" content="https://x.com/img.jpg"></head><body></body></html>'
        with mock.patch.object(website_loader, "load_page", return_value=html):
            metadata = website_loader.load_website_metadata("https://example.com")
        self.assertEqual(metadata.preview_image, "https://x.com/img.jpg")

    def test_json_ld_article(self):
        html = '''<html><head>
        <script type="application/ld+json">
        {"@type": "Article", "headline": "Article Title", "description": "Art Desc", "image": "https://x.com/ld.jpg"}
        </script></head><body></body></html>'''
        with mock.patch.object(website_loader, "load_page", return_value=html):
            metadata = website_loader.load_website_metadata("https://example.com")
        self.assertEqual(metadata.title, "Article Title")
        self.assertEqual(metadata.description, "Art Desc")
        self.assertEqual(metadata.preview_image, "https://x.com/ld.jpg")

    def test_json_ld_graph(self):
        html = '''<html><head>
        <script type="application/ld+json">
        {"@graph": [
            {"@type": "WebSite", "name": "Skip Me"},
            {"@type": "NewsArticle", "headline": "Graph Title", "description": "Graph Desc"}
        ]}
        </script></head><body></body></html>'''
        with mock.patch.object(website_loader, "load_page", return_value=html):
            metadata = website_loader.load_website_metadata("https://example.com")
        self.assertEqual(metadata.title, "Graph Title")
        self.assertEqual(metadata.description, "Graph Desc")

    def test_json_ld_image_object(self):
        html = '''<html><head><title>T</title>
        <script type="application/ld+json">
        {"@type": "Article", "headline": "H", "image": {"url": "https://x.com/obj.jpg"}}
        </script></head><body></body></html>'''
        with mock.patch.object(website_loader, "load_page", return_value=html):
            metadata = website_loader.load_website_metadata("https://example.com")
        self.assertEqual(metadata.preview_image, "https://x.com/obj.jpg")

    def test_json_ld_image_array(self):
        html = '''<html><head><title>T</title>
        <script type="application/ld+json">
        {"@type": "Article", "headline": "H", "image": ["https://x.com/first.jpg", "https://x.com/second.jpg"]}
        </script></head><body></body></html>'''
        with mock.patch.object(website_loader, "load_page", return_value=html):
            metadata = website_loader.load_website_metadata("https://example.com")
        self.assertEqual(metadata.preview_image, "https://x.com/first.jpg")

    def test_json_ld_skips_web_site_type(self):
        html = '''<html><head><title>Page Title</title>
        <script type="application/ld+json">
        {"@type": "WebSite", "name": "Site Name", "description": "Site Desc"}
        </script></head><body></body></html>'''
        with mock.patch.object(website_loader, "load_page", return_value=html):
            metadata = website_loader.load_website_metadata("https://example.com")
        # Should fall back to <title>, not JSON-LD WebSite name
        self.assertEqual(metadata.title, "Page Title")

    def test_twitter_over_json_ld(self):
        """Twitter card should be preferred over JSON-LD."""
        html = '''<html><head>
        <meta name="twitter:title" content="TW Title">
        <script type="application/ld+json">
        {"@type": "Article", "headline": "LD Title"}
        </script></head><body></body></html>'''
        with mock.patch.object(website_loader, "load_page", return_value=html):
            metadata = website_loader.load_website_metadata("https://example.com")
        self.assertEqual(metadata.title, "TW Title")

    def test_og_over_twitter(self):
        """OG tags should be preferred over Twitter cards."""
        html = '''<html><head>
        <meta property="og:title" content="OG Title">
        <meta name="twitter:title" content="TW Title">
        </head><body></body></html>'''
        with mock.patch.object(website_loader, "load_page", return_value=html):
            metadata = website_loader.load_website_metadata("https://example.com")
        self.assertEqual(metadata.title, "OG Title")

    def test_explicit_selector_blocks_twitter_fallback(self):
        """Explicit selectors should prevent all fallbacks."""
        html = '''<html><head>
        <meta name="twitter:title" content="TW Title">
        </head><body><h1 class="t">Explicit</h1></body></html>'''
        config = {"select_title": [".t"], "headers": {}}
        with (
            mock.patch("bookmarks.services.website_loader.get_metadata_config", return_value=config),
            mock.patch.object(website_loader, "load_page", return_value=html),
        ):
            metadata = website_loader.load_website_metadata("https://example.com")
        self.assertEqual(metadata.title, "Explicit")

    def test_json_ld_invalid_json_ignored(self):
        """Invalid JSON-LD should be silently ignored."""
        html = '''<html><head><title>Page</title>
        <script type="application/ld+json">{invalid json</script>
        </head><body></body></html>'''
        with mock.patch.object(website_loader, "load_page", return_value=html):
            metadata = website_loader.load_website_metadata("https://example.com")
        self.assertEqual(metadata.title, "Page")


class MetadataRetryTestCase(TestCase):
    """Test exponential backoff retry on RetryableMetadataError."""

    def setUp(self):
        website_loader._load_website_metadata_cached.cache_clear()
        website_loader._load_website_metadata_config_cached.cache_clear()
        from site_adapters.services.config.loader import _cache
        _cache.invalidate()

    def test_retries_on_retryable_error_then_succeeds(self):
        """Should retry on 503 and succeed on second attempt."""
        fail_response = MockStreamingResponse(num_chunks=1, chunk_size=10, status_code=503)
        ok_html = '<html><head><title>OK</title></head><body></body></html>'
        ok_response = MockStreamingResponse(num_chunks=1, chunk_size=0, status_code=200)
        ok_response.chunks[0] = ok_html.encode()

        with (
            mock.patch("requests.get", side_effect=[fail_response, ok_response]),
            mock.patch("bookmarks.services.website_loader._wait_for_domain"),
            mock.patch("bookmarks.services.website_loader.time.sleep") as mock_sleep,
        ):
            metadata = website_loader.load_website_metadata("https://example.com")
        self.assertEqual(metadata.title, "OK")
        mock_sleep.assert_called_once_with(1.0)

    def test_raises_after_max_retries(self):
        """Should raise RetryableMetadataError after exhausting retries."""
        fail_response = MockStreamingResponse(num_chunks=1, chunk_size=10, status_code=503)

        with (
            mock.patch("requests.get", return_value=fail_response),
            mock.patch("bookmarks.services.website_loader._wait_for_domain"),
            mock.patch("bookmarks.services.website_loader.time.sleep"),
            self.assertRaises(website_loader.RetryableMetadataError),
        ):
            website_loader.load_website_metadata("https://example.com")

    def test_exponential_backoff_delays(self):
        """Delays should be 1s, 2s, 4s."""
        fail_response = MockStreamingResponse(num_chunks=1, chunk_size=10, status_code=503)
        ok_html = '<html><head><title>OK</title></head><body></body></html>'
        ok_response = MockStreamingResponse(num_chunks=1, chunk_size=0, status_code=200)
        ok_response.chunks[0] = ok_html.encode()

        with (
            mock.patch("requests.get", side_effect=[fail_response, fail_response, fail_response, ok_response]),
            mock.patch("bookmarks.services.website_loader._wait_for_domain"),
            mock.patch("bookmarks.services.website_loader.time.sleep") as mock_sleep,
        ):
            metadata = website_loader.load_website_metadata("https://example.com")
        self.assertEqual(metadata.title, "OK")
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        self.assertEqual(delays, [1.0, 2.0, 4.0])

    def test_no_retry_on_non_retryable_error(self):
        """Should NOT retry on 403 (NonRetryableMetadataError)."""
        fail_response = MockStreamingResponse(num_chunks=1, chunk_size=10, status_code=403)

        with (
            mock.patch("requests.get", return_value=fail_response),
            mock.patch("bookmarks.services.website_loader._wait_for_domain"),
            mock.patch("bookmarks.services.website_loader.time.sleep") as mock_sleep,
        ):
            metadata = website_loader.load_website_metadata("https://example.com")
        self.assertIsNone(metadata.title)
        mock_sleep.assert_not_called()
