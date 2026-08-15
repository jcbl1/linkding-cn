from unittest import mock

from django.test import TestCase

from bookmarks.services import reader_processor


class ReaderProcessorTestCase(TestCase):
    def test_normalize_html_for_reader_unwraps_shadow_templates(self):
        html = """
        <div>
          <my-widget>
            <template shadowrootmode="open">
              <img src="/image.jpg" alt="gallery">
            </template>
          </my-widget>
        </div>
        """

        normalized = reader_processor._normalize_html_for_reader(html)

        self.assertNotIn("shadowrootmode", normalized)
        self.assertIn('<img alt="gallery" src="/image.jpg"/>', normalized)

    def test_parse_html_normalizes_before_calling_defuddle(self):
        with (
            mock.patch(
                "bookmarks.services.reader_processor.get_reader_config",
                return_value=None,
            ),
            mock.patch(
                "bookmarks.services.reader_processor._normalize_html_for_reader",
                return_value="<p>normalized</p>",
            ),
            mock.patch(
                "bookmarks.services.defuddle.parse_html",
                return_value={"title": "ok"},
            ) as mock_defuddle,
        ):
            result = reader_processor.parse_html("<p>original</p>", "https://example.com")

        self.assertEqual(result, {"title": "ok"})
        mock_defuddle.assert_called_once_with("<p>normalized</p>", url="https://example.com", options=None)

    def test_reader_defuddle_args_are_passed_to_defuddle(self):
        config = {"defuddle_args": {"contentSelector": ".article", "ignored": True}}

        with (
            mock.patch(
                "bookmarks.services.reader_processor.get_reader_config",
                return_value=config,
            ),
            mock.patch(
                "bookmarks.services.defuddle.parse_url",
                return_value={"title": "ok"},
            ) as mock_parse,
        ):
            result = reader_processor.parse_url("https://example.com")

        self.assertEqual(result, {"title": "ok"})
        mock_parse.assert_called_once_with("https://example.com", options={"contentSelector": ".article"})
