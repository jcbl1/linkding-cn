import json
import os
import tempfile
from unittest import mock

from django.test import TestCase

from bookmarks.services.snapshot_processor import _run_snapshot


class SnapshotProcessorTestCase(TestCase):
    def _run_raw_snapshot(self, content_type: str, content: str):
        with tempfile.NamedTemporaryFile("w", suffix=".tmp", delete=False) as tmp:
            output_path = tmp.name
        self.addCleanup(lambda: os.path.exists(output_path) and os.unlink(output_path))

        config = {"content_type": content_type}
        with mock.patch(
            "bookmarks.services.snapshot_processor.website_loader.load_page",
            return_value=content,
        ) as mock_load:
            _run_snapshot("https://example.com/item", output_path, config)

        mock_load.assert_called_once_with(
            "https://example.com/item",
            config,
            load_full_page=True,
        )
        with open(output_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), content)

    def test_json_snapshot_saves_raw_response(self):
        body = json.dumps({"title": "JSON snapshot"})
        self._run_raw_snapshot("json", body)

    def test_xml_snapshot_saves_raw_response(self):
        body = "<?xml version=\"1.0\"?><feed><entry><title>XML snapshot</title></entry></feed>"
        self._run_raw_snapshot("xml", body)
