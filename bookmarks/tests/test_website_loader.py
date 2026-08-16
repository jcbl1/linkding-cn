import json
import os
import tempfile
from unittest import mock

import requests
from django.test import TestCase

from bookmarks.services import website_loader
from site_adapters.services.execution_log import collect_executions


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

    def test_load_page_captures_response_content_type(self):
        response = MockStreamingResponse(num_chunks=1, chunk_size=4)
        response.headers = {"Content-Type": "application/json"}
        config = {}

        with mock.patch("requests.get", return_value=response):
            website_loader.load_page("https://example.com/api", config)

        self.assertEqual(config["_response_content_type"], "application/json")

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

    # --- Tests for enhanced metadata extraction (built-in defaults) ---

    def test_empty_head_returns_none(self):
        """Empty head with no meta tags returns None for all fields."""
        head_html = "<html><head></head></html>"
        with mock.patch("bookmarks.services.website_loader.load_page", return_value=head_html):
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertIsNone(metadata.title)

    def test_body_selectors_beat_title_tag(self):
        """Body selectors in defaults (e.g. .article-title) take priority over <title> tag."""
        with mock.patch("bookmarks.services.website_loader.load_page") as mock_load:
            mock_load.return_value = """
            <html><head><title>Page Title</title></head>
            <body><h1 class="article-title">Article Title</h1></body></html>
            """
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertEqual("Article Title", metadata.title)

    def test_no_match_returns_none(self):
        """Empty page returns None for title."""
        head_html = "<html><head></head></html>"
        with mock.patch("bookmarks.services.website_loader.load_page", return_value=head_html):
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertIsNone(metadata.title)

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
            <html><head>
            <script type="application/ld+json">
            {"@type":"Article","headline":"JSON-LD Headline","description":"JSON-LD Desc","image":"https://example.com/ld.png"}
            </script>
            </head><body></body></html>
            """
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertEqual("JSON-LD Headline", metadata.title)
            self.assertEqual("JSON-LD Desc", metadata.description)
            self.assertEqual("https://example.com/ld.png", metadata.preview_image)

    def test_json_ld_graph_extraction(self):
        """Should handle @graph arrays in JSON-LD."""
        with mock.patch("bookmarks.services.website_loader.load_page") as mock_load:
            mock_load.return_value = """
            <html><head>
            <script type="application/ld+json">
            {"@graph":[{"@type":"Article","name":"Graph Article","description":"Graph Desc"}]}
            </script>
            </head><body></body></html>
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
                "bookmarks.services.website_loader.get_metadata_config",
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


    def test_default_og_title_extraction(self):
        """OG title in head is extracted with default settings."""
        head_html = """<html><head>
            <title>Head Title</title>
            <meta property="og:title" content="OG Title">
            </head></html>"""
        with mock.patch("bookmarks.services.website_loader.load_page", return_value=head_html) as mock_load:
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertEqual("OG Title", metadata.title)
            self.assertEqual(mock_load.call_count, 1)

    def test_load_full_page_enabled_finds_body_title(self):
        """When load_full_page is enabled in config, body selectors match."""
        body_html = """<html><head></head>
            <body><h1 class="article-title">Body Title</h1></body></html>"""
        config = {"select_title": [".article-title"], "load_full_page": True}
        with (
            mock.patch("bookmarks.services.website_loader.get_metadata_config", return_value=config),
            mock.patch("bookmarks.services.website_loader.load_page", return_value=body_html) as mock_load,
        ):
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertEqual(mock_load.call_count, 1)
            self.assertEqual("Body Title", metadata.title)


    def test_load_full_page_uses_config_selectors(self):
        """With load_full_page=True, config selectors find body elements."""
        body_html = """<html><head></head>
            <body><h1 class="my-custom-title">Custom Body Title</h1></body></html>"""
        config = {"select_title": [".my-custom-title"], "load_full_page": True}
        with (
            mock.patch("bookmarks.services.website_loader.get_metadata_config", return_value=config),
            mock.patch("bookmarks.services.website_loader.load_page", return_value=body_html) as mock_load,
        ):
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertEqual(mock_load.call_count, 1)
            self.assertEqual("Custom Body Title", metadata.title)

    def test_load_full_page_param_has_rate_limiting(self):
        """load_page with load_full_page=True should apply per-domain rate limiting."""
        with (
            mock.patch("bookmarks.services.website_loader.requests.get") as mock_get,
            mock.patch("bookmarks.services.website_loader._wait_for_domain") as mock_wait,
        ):
            mock_response = mock.Mock()
            mock_response.iter_content.return_value = [b"<html></html>"]
            mock_response.status_code = 200
            mock_get.return_value.__enter__.return_value = mock_response
            website_loader.load_page("https://example.com", load_full_page=True)
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

    def test_website_metadata_with_config_uses_cache(self):
        expected_html = '<html><head><title>Test Title</title></head></html>'
        config = {"http": {"timeout": 3}, "select_title": ["title"]}

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

        mock_load_page.assert_called_once_with(
            "https://fetch.example.com/item", config, load_full_page=True
        )
        self.assertEqual(metadata.url, "https://final.example.com/item")
        self.assertEqual(metadata.title, "Selected title")
        self.assertEqual(metadata.description, "Selected description")
        self.assertEqual(metadata.preview_image, "https://fetch.example.com/cover.jpg")

    def test_html_metadata_uses_standard_css_semantics(self):
        html = """
        <html><body>
          <meta property="og:description" content="CSS description">
          <meta property="og:image" content="/cover.jpg">
        </body></html>
        """
        config = {
            "select_description": ["meta[property='og:description']"],
            "select_image": ["meta[property='og:image']"],
            "headers": {},
        }

        with (
            mock.patch(
                "bookmarks.services.website_loader.get_metadata_config",
                return_value=config,
            ),
            mock.patch.object(website_loader, "load_page", return_value=html),
        ):
            metadata = website_loader.load_website_metadata("https://example.com/post")

        self.assertEqual(metadata.description, "CSS description")
        self.assertEqual(metadata.preview_image, "https://example.com/cover.jpg")

    def test_configured_xml_metadata_uses_selectors(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
          <entry>
            <title>XML title</title>
            <content type="html">&lt;p&gt;XML &lt;strong&gt;description&lt;/strong&gt;&lt;/p&gt;</content>
            <media:thumbnail url="https://preview.redd.it/pic.jpg?width=140&amp;auto=webp" />
          </entry>
        </feed>
        """
        config = {
            "content_type": "xml",
            "select_title": ["//atom:feed/atom:entry/atom:title"],
            "select_description": ["//atom:feed/atom:entry/atom:content"],
            "select_image": ["//atom:feed/atom:entry/media:thumbnail/@url"],
            "rewrite_image": [
                "^https://preview\\.redd\\.it/([^?]+).*$",
                "https://i.redd.it/\\1",
            ],
            "headers": {},
        }

        with (
            mock.patch(
                "bookmarks.services.website_loader.get_metadata_config",
                return_value=config,
            ),
            mock.patch.object(website_loader, "load_page", return_value=xml),
        ):
            metadata = website_loader.load_website_metadata("https://www.reddit.com/r/x/comments/y/post/")

        self.assertEqual(metadata.title, "XML title")
        self.assertEqual(metadata.description, "XML\ndescription")
        self.assertEqual(metadata.preview_image, "https://i.redd.it/pic.jpg")

    def test_configured_xml_metadata_binds_unprefixed_xpath_to_default_namespace(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
          <entry>
            <title>XML title</title>
            <content type="html">&lt;p&gt;XML &lt;strong&gt;description&lt;/strong&gt;&lt;/p&gt;</content>
            <media:thumbnail url="https://preview.redd.it/pic.jpg?width=140&amp;auto=webp" />
          </entry>
        </feed>
        """
        config = {
            "content_type": "xml",
            "select_title": ["//feed/entry/title"],
            "select_description": ["//feed/entry/content"],
            "select_image": ["//feed/entry/media:thumbnail/@url"],
            "rewrite_image": [
                "^https://preview\\.redd\\.it/([^?]+).*$",
                "https://i.redd.it/\\1",
            ],
            "headers": {},
        }

        with (
            mock.patch(
                "bookmarks.services.website_loader.get_metadata_config",
                return_value=config,
            ),
            mock.patch.object(website_loader, "load_page", return_value=xml),
        ):
            metadata = website_loader.load_website_metadata("https://www.reddit.com/r/x/comments/y/post/")

        self.assertEqual(metadata.title, "XML title")
        self.assertEqual(metadata.description, "XML\ndescription")
        self.assertEqual(metadata.preview_image, "https://i.redd.it/pic.jpg")

    def test_xml_metadata_without_namespace_keeps_plain_xpath(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <feed>
          <entry>
            <title>Plain XML title</title>
            <summary>Plain XML description</summary>
          </entry>
        </feed>
        """
        config = {
            "content_type": "xml",
            "select_title": ["//feed/entry/title"],
            "select_description": ["//feed/entry/summary"],
            "headers": {},
        }

        with (
            mock.patch(
                "bookmarks.services.website_loader.get_metadata_config",
                return_value=config,
            ),
            mock.patch.object(website_loader, "load_page", return_value=xml),
        ):
            metadata = website_loader.load_website_metadata("https://example.com/feed.xml")

        self.assertEqual(metadata.title, "Plain XML title")
        self.assertEqual(metadata.description, "Plain XML description")

    def test_xml_metadata_registers_prefixes_declared_on_nested_elements(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry xmlns:media="http://search.yahoo.com/mrss/">
            <title>Nested prefix title</title>
            <media:thumbnail url="https://example.com/nested.jpg" />
          </entry>
        </feed>
        """
        config = {
            "content_type": "xml",
            "select_title": ["//feed/entry/title"],
            "select_image": ["//feed/entry/media:thumbnail/@url"],
            "headers": {},
        }

        with (
            mock.patch(
                "bookmarks.services.website_loader.get_metadata_config",
                return_value=config,
            ),
            mock.patch.object(website_loader, "load_page", return_value=xml),
        ):
            metadata = website_loader.load_website_metadata("https://example.com/feed.xml")

        self.assertEqual(metadata.title, "Nested prefix title")
        self.assertEqual(metadata.preview_image, "https://example.com/nested.jpg")

    def test_xml_metadata_can_select_no_namespace_nodes_with_local_name(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <title>Atom title</title>
          <custom xmlns="">Plain child</custom>
        </feed>
        """
        config = {
            "content_type": "xml",
            "select_title": ["/feed/*[local-name()='custom']"],
            "headers": {},
        }

        with (
            mock.patch(
                "bookmarks.services.website_loader.get_metadata_config",
                return_value=config,
            ),
            mock.patch.object(website_loader, "load_page", return_value=xml),
        ):
            metadata = website_loader.load_website_metadata("https://example.com/feed.xml")

        self.assertEqual(metadata.title, "Plain child")

    def test_configured_json_metadata_uses_paths(self):
        body = json.dumps({
            "data": {
                "title": "JSON title",
                "items": [{"summary": "JSON description"}],
                "image": {"url": "/cover.jpg"},
            }
        })
        config = {
            "content_type": "json",
            "select_title": ["$.data.title"],
            "select_description": ["$.data.items[0].summary"],
            "select_image": ["$.data.image.url"],
            "headers": {},
        }

        with (
            mock.patch(
                "bookmarks.services.website_loader.get_metadata_config",
                return_value=config,
            ),
            mock.patch.object(website_loader, "load_page", return_value=body),
        ):
            metadata = website_loader.load_website_metadata("https://api.example.com/item")

        self.assertEqual(metadata.title, "JSON title")
        self.assertEqual(metadata.description, "JSON description")
        self.assertEqual(metadata.preview_image, "https://api.example.com/cover.jpg")

    def test_content_type_explicit_wins_over_selectors(self):
        body = json.dumps({"title": "JSON title"})
        config = {
            "content_type": "json",
            "select_title": ["$.title"],
            "headers": {},
        }

        with (
            mock.patch(
                "bookmarks.services.website_loader.get_metadata_config",
                return_value=config,
            ),
            mock.patch.object(website_loader, "load_page", return_value=body),
        ):
            metadata = website_loader.load_website_metadata("https://api.example.com/item")

        self.assertEqual(metadata.title, "JSON title")

    def test_content_type_inferred_from_selector_syntax(self):
        configs = [
            ({"select_title": ["$.title"]}, "json"),
            ({"select_title": ["//item/title"]}, "xml"),
            ({"select_title": [".title"]}, "html"),
        ]
        for config, expected in configs:
            with self.subTest(expected=expected):
                self.assertEqual(
                    website_loader.resolve_content_type(config),
                    expected,
                )

    def test_content_type_falls_back_to_response_header(self):
        config = {"_response_content_type": "application/atom+xml"}
        self.assertEqual(website_loader.resolve_content_type(config), "xml")

    def test_content_type_resolution_raises_without_signals(self):
        with self.assertRaises(website_loader.ContentTypeResolutionError):
            website_loader.resolve_content_type({})

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

    def test_load_website_metadata_for_test_uses_script_hooks(self):
        script_path = os.path.join(tempfile.gettempdir(), "reddit_metadata.py")
        config = {
            "scripts": [{"path": script_path, "hook": "replace"}],
            "headers": {},
        }
        metadata = website_loader.WebsiteMetadata(
            "https://example.com/post", "Example title", None, None
        )

        with (
            mock.patch(
                "bookmarks.services.website_loader.get_metadata_config",
                return_value=config,
            ),
            mock.patch(
                "bookmarks.services.website_loader._load_with_hooks",
                return_value=metadata,
            ) as mock_load_with_hooks,
        ):
            result, sources, returned_config = website_loader.load_website_metadata_for_test(
                "https://example.com/post"
            )

        self.assertIs(result, metadata)
        self.assertEqual(sources["scripts"], [script_path])
        self.assertIs(returned_config, config)
        mock_load_with_hooks.assert_called_once_with(
            "https://example.com/post",
            config,
            config["scripts"],
            username="",
        )


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

    def test_explicit_selector_blocks_fallback(self):
        """Explicit selectors should prevent JSON-LD fallback when selector matches."""
        html = '''<html><head>
        <meta name="custom-title" content="Custom Title">
        <meta name="twitter:title" content="TW Title">
        </head><body></body></html>'''
        config = {"select_title": ['meta[name="custom-title"]'], "headers": {}}
        with (
            mock.patch("bookmarks.services.website_loader.get_metadata_config", return_value=config),
            mock.patch.object(website_loader, "load_page", return_value=html),
        ):
            metadata = website_loader.load_website_metadata("https://example.com")
        self.assertEqual(metadata.title, "Custom Title")

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

    def test_returns_empty_metadata_after_max_retries(self):
        """Should return empty metadata after exhausting retries (HEAD behavior)."""
        fail_response = MockStreamingResponse(num_chunks=1, chunk_size=10, status_code=503)

        with (
            mock.patch("requests.get", return_value=fail_response),
            mock.patch("bookmarks.services.website_loader._wait_for_domain"),
            mock.patch("bookmarks.services.website_loader.time.sleep"),
        ):
            metadata = website_loader.load_website_metadata("https://example.com")
            self.assertIsNone(metadata.title)
            self.assertIsNone(metadata.description)
            self.assertIsNone(metadata.preview_image)

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

    def test_load_page_records_command_on_http_error(self):
        fail_response = MockStreamingResponse(num_chunks=1, chunk_size=10, status_code=403)

        with (
            mock.patch("requests.get", return_value=fail_response),
            mock.patch("bookmarks.services.website_loader._wait_for_domain"),
            collect_executions() as entries,
            self.assertRaises(website_loader.NonRetryableMetadataError),
        ):
            website_loader.load_page("https://example.com")

        self.assertTrue(
            any(
                e.get("step") == "metadata" and e.get("cmd")
                for e in entries
            )
        )

    def test_load_website_metadata_for_test_returns_http_error(self):
        config = {
            "select_title": [".title"],
            "headers": {},
            "_request_url": "https://example.com/post",
            "load_full_page": True,
        }

        with (
            mock.patch(
                "bookmarks.services.website_loader.get_metadata_config",
                return_value=config,
            ),
            mock.patch.object(
                website_loader,
                "load_page",
                side_effect=website_loader.NonRetryableMetadataError(
                    "Non-retryable metadata response: 403", 403
                ),
            ),
        ):
            metadata, sources, returned_config = website_loader.load_website_metadata_for_test(
                "https://example.com/post"
            )

        self.assertIsNone(metadata.title)
        self.assertEqual(sources["error"], "Non-retryable metadata response: 403")
        self.assertIs(returned_config, config)
