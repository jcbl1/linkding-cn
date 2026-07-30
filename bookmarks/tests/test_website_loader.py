from unittest import mock

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

    def test_load_website_metadata_prefers_description_over_og_description(self):
        with mock.patch(
            "bookmarks.services.website_loader.load_page"
        ) as mock_load_page:
            mock_load_page.return_value = self.render_html_document(
                "test title", "test description", og_description="test og description"
            )
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertEqual("test title", metadata.title)
            self.assertEqual("test description", metadata.description)

    def test_load_website_metadata_returns_empty_metadata_when_custom_loader_returns_none(
        self,
    ):
        custom_loader_module = mock.Mock()
        custom_loader_module._load_website_metadata.return_value = None

        with (
            mock.patch(
                "bookmarks.services.website_loader.search_config_for_domain",
                return_value={"loader": "custom.py"},
            ),
            mock.patch("os.path.exists", return_value=True),
            mock.patch(
                "bookmarks.services.website_loader.load_module",
                return_value=custom_loader_module,
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

    # --- Tests for enhanced metadata extraction (built-in defaults) ---

    def test_title_fallback_to_h1_with_title_class(self):
        """When og:title and <title> are missing in head, should fallback to body via full page load."""
        head_html = "<html><head></head></html>"
        body_html = """<html><head></head>
            <body><h1 class="article-title">Article Title</h1></body></html>"""
        with (
            mock.patch("bookmarks.services.website_loader.load_page", return_value=head_html),
            mock.patch("bookmarks.services.website_loader.load_full_page", return_value=body_html),
        ):
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertEqual("Article Title", metadata.title)

    def test_title_tag_takes_priority_over_h1(self):
        """<title> tag should be preferred over h1 when og:title is missing."""
        with mock.patch("bookmarks.services.website_loader.load_page") as mock_load:
            mock_load.return_value = """
            <html><head><title>Page Title</title></head>
            <body><h1 class="article-title">Article Title</h1></body></html>
            """
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertEqual("Page Title", metadata.title)

    def test_title_fallback_to_plain_h1(self):
        """When no og:title or <title> in head, should fallback to body h1 via full page load."""
        head_html = "<html><head></head></html>"
        body_html = """<html><head></head>
            <body><h1>Just H1</h1></body></html>"""
        with (
            mock.patch("bookmarks.services.website_loader.load_page", return_value=head_html),
            mock.patch("bookmarks.services.website_loader.load_full_page", return_value=body_html),
        ):
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertEqual("Just H1", metadata.title)

    def test_title_fallback_to_title_tag(self):
        """When no h1 at all, should use <title> tag."""
        with mock.patch("bookmarks.services.website_loader.load_page") as mock_load:
            mock_load.return_value = """
            <html><head><title>Only Title Tag</title></head>
            <body><p>No h1 here</p></body></html>
            """
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertEqual("Only Title Tag", metadata.title)

    def test_description_fallback_to_og_when_no_meta(self):
        """When meta description is missing, should use og:description."""
        with mock.patch("bookmarks.services.website_loader.load_page") as mock_load:
            mock_load.return_value = """
            <html><head>
            <meta property="og:description" content="OG Description">
            </head><body></body></html>
            """
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertEqual("OG Description", metadata.description)

    def test_description_fallback_to_twitter(self):
        """When both meta and og:description missing, should use twitter:description."""
        with mock.patch("bookmarks.services.website_loader.load_page") as mock_load:
            mock_load.return_value = """
            <html><head>
            <meta name="twitter:description" content="Twitter Description">
            </head><body></body></html>
            """
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertEqual("Twitter Description", metadata.description)

    def test_image_fallback_to_twitter_image(self):
        """When og:image is missing, should use twitter:image."""
        with mock.patch("bookmarks.services.website_loader.load_page") as mock_load:
            mock_load.return_value = """
            <html><head>
            <meta name="twitter:image" content="https://example.com/tw.png">
            </head><body></body></html>
            """
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertEqual("https://example.com/tw.png", metadata.preview_image)

    def test_image_fallback_to_preload_link(self):
        """When all meta images missing, should use link[rel=preload][as=image]."""
        with mock.patch("bookmarks.services.website_loader.load_page") as mock_load:
            mock_load.return_value = """
            <html><head>
            <link rel="preload" href="https://example.com/preload.png" as="image">
            </head><body></body></html>
            """
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertEqual("https://example.com/preload.png", metadata.preview_image)

    def test_json_ld_title_extraction(self):
        """Should extract title from JSON-LD when no meta tags."""
        with mock.patch("bookmarks.services.website_loader.load_page") as mock_load:
            mock_load.return_value = """
            <html><head></head>
            <body>
            <script type="application/ld+json">
            {"@type":"Article","headline":"JSON-LD Headline","description":"JSON-LD Desc","image":"https://example.com/ld.png"}
            </script>
            </body></html>
            """
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertEqual("JSON-LD Headline", metadata.title)
            self.assertEqual("JSON-LD Desc", metadata.description)
            self.assertEqual("https://example.com/ld.png", metadata.preview_image)

    def test_json_ld_graph_extraction(self):
        """Should handle @graph arrays in JSON-LD."""
        with mock.patch("bookmarks.services.website_loader.load_page") as mock_load:
            mock_load.return_value = """
            <html><head></head>
            <body>
            <script type="application/ld+json">
            {"@graph":[{"@type":"Article","name":"Graph Article","description":"Graph Desc"}]}
            </script>
            </body></html>
            """
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertEqual("Graph Article", metadata.title)
            self.assertEqual("Graph Desc", metadata.description)

    def test_json_ld_skip_non_content_types(self):
        """Should skip JSON-LD of types like WebSite, Organization."""
        with mock.patch("bookmarks.services.website_loader.load_page") as mock_load:
            mock_load.return_value = """
            <html><head><title>Real Title</title></head>
            <body>
            <script type="application/ld+json">
            {"@type":"WebSite","name":"Site Name"}
            </script>
            </body></html>
            """
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertEqual("Real Title", metadata.title)

    def test_json_ld_image_object(self):
        """Should handle image as object with url key."""
        with mock.patch("bookmarks.services.website_loader.load_page") as mock_load:
            mock_load.return_value = """
            <html><head></head>
            <body>
            <script type="application/ld+json">
            {"@type":"Article","image":{"url":"https://example.com/obj.png"}}
            </script>
            </body></html>
            """
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertEqual("https://example.com/obj.png", metadata.preview_image)

    def test_config_selectors_take_priority_over_defaults(self):
        """When config provides select_title, it should override defaults."""
        with (
            mock.patch("bookmarks.services.website_loader.load_page") as mock_load,
            mock.patch(
                "bookmarks.services.website_loader.search_config_for_domain",
                return_value={"select_title": [".custom-title"]},
            ),
        ):
            mock_load.return_value = """
            <html><head><title>Default Title</title></head>
            <body><h1 class="custom-title">Custom Title</h1></body></html>
            """
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertEqual("Custom Title", metadata.title)

    def test_relative_image_url_is_resolved(self):
        """Relative image URLs should be resolved to absolute."""
        with mock.patch("bookmarks.services.website_loader.load_page") as mock_load:
            mock_load.return_value = """
            <html><head>
            <meta property="og:image" content="/images/photo.png">
            </head><body></body></html>
            """
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertEqual("https://example.com/images/photo.png", metadata.preview_image)


    def test_no_full_page_fallback_when_title_found_in_head(self):
        """Should NOT load full page when title is found in head."""
        head_html = """<html><head>
            <title>Head Title</title>
            <meta property="og:title" content="OG Title">
            </head></html>"""
        with (
            mock.patch("bookmarks.services.website_loader.load_page", return_value=head_html),
            mock.patch("bookmarks.services.website_loader.load_full_page") as mock_full,
        ):
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertEqual("OG Title", metadata.title)
            mock_full.assert_not_called()

    def test_full_page_fallback_called_when_title_missing_in_head(self):
        """Should load full page when title is missing in head."""
        head_html = "<html><head><meta name='description' content='desc'></head></html>"
        body_html = """<html><head></head>
            <body><h1 class="article-title">Body Title</h1></body></html>"""
        with (
            mock.patch("bookmarks.services.website_loader.load_page", return_value=head_html),
            mock.patch("bookmarks.services.website_loader.load_full_page", return_value=body_html) as mock_full,
        ):
            metadata = website_loader.load_website_metadata("https://example.com")
            mock_full.assert_called_once()
            self.assertEqual("Body Title", metadata.title)


    def test_full_page_fallback_uses_config_selectors(self):
        """Body fallback should use config select_title when provided."""
        head_html = "<html><head></head></html>"
        body_html = """<html><head></head>
            <body><h1 class="my-custom-title">Custom Body Title</h1></body></html>"""
        with (
            mock.patch("bookmarks.services.website_loader.load_page", return_value=head_html),
            mock.patch("bookmarks.services.website_loader.load_full_page", return_value=body_html),
            mock.patch(
                "bookmarks.services.website_loader.search_config_for_domain",
                return_value={"select_title": [".my-custom-title"]},
            ),
        ):
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertEqual("Custom Body Title", metadata.title)

    def test_load_full_page_has_rate_limiting(self):
        """load_full_page should also apply per-domain rate limiting."""
        with (
            mock.patch("bookmarks.services.website_loader.requests.get") as mock_get,
            mock.patch("bookmarks.services.website_loader._throttle_domain") as mock_wait,
        ):
            mock_response = mock.Mock()
            mock_response.encoding = "utf-8"
            mock_response.text = "<html></html>"
            mock_get.return_value = mock_response
            website_loader.load_full_page("https://example.com")
            mock_wait.assert_called_once_with("example.com")

    def test_empty_response_returns_empty_string(self):
        """load_page should return empty string for empty response, not 'None'."""
        with mock.patch("bookmarks.services.website_loader.requests.get") as mock_get:
            mock_response = mock.Mock()
            mock_response.status_code = 200
            mock_response.iter_content = mock.Mock(return_value=iter([]))
            mock_get.return_value.__enter__ = mock.Mock(return_value=mock_response)
            mock_get.return_value.__exit__ = mock.Mock(return_value=False)
            result = website_loader.load_page("https://example.com")
            self.assertEqual("", result)
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
