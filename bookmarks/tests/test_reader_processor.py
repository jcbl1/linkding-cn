from unittest import mock

from django.test import TestCase

from bookmarks.services import reader_processor


class ReaderProcessorTestCase(TestCase):
    def test_reader_defuddle_args_are_passed_to_defuddle(self):
        config = {"defuddle_args": {"contentSelector": ".article", "ignored": True}}

        with (
            mock.patch(
                "bookmarks.services.reader_processor.get_reader_config",
                return_value=config,
            ),
            mock.patch(
                "bookmarks.services.reader_processor._parse_url_with_options",
                return_value={"title": "ok"},
            ) as mock_parse,
        ):
            result = reader_processor.parse_url("https://example.com")

        self.assertEqual(result, {"title": "ok"})
        mock_parse.assert_called_once_with(
            "https://example.com",
            {"contentSelector": ".article"},
        )
