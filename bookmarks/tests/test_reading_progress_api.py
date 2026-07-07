from urllib.parse import urlencode

from django.urls import reverse
from rest_framework import status

from bookmarks.models import BookmarkAsset, ReadingProgress
from bookmarks.services import articles
from bookmarks.tests.helpers import BookmarkFactoryMixin, LinkdingApiTestCase


class ReadingProgressApiTestCase(LinkdingApiTestCase, BookmarkFactoryMixin):
    def setUp(self):
        self.authenticate()

    def _url(self, bookmark):
        return reverse("linkding:bookmark_reading_progress", args=[bookmark.id])

    def _anchor(self, exact="test paragraph", prefix="before ", suffix=" after", position=42):
        return {
            "text_position_start": position,
            "text_quote_exact": exact,
            "text_quote_prefix": prefix,
            "text_quote_suffix": suffix,
        }

    def test_get_returns_default_progress_without_creating_record(self):
        bookmark = self.setup_bookmark()

        response = self.get(self._url(bookmark))

        self.assertEqual(response.data["bookmark"], bookmark.id)
        self.assertIsNone(response.data["article_asset"])
        self.assertIsNone(response.data["text_position_start"])
        self.assertEqual(response.data["text_quote_exact"], "")
        self.assertIsNone(response.data["element_selector"])
        self.assertEqual(response.data["progress"], 0)
        self.assertEqual(response.data["scroll_top"], 0)
        self.assertFalse(
            ReadingProgress.objects.filter(user=self.user, bookmark=bookmark).exists()
        )

    def test_patch_saves_progress(self):
        bookmark = self.setup_bookmark()
        article_asset = self.setup_asset(
            bookmark=bookmark,
            asset_type=BookmarkAsset.TYPE_ARTICLE,
            content_type=BookmarkAsset.CONTENT_TYPE_HTML,
        )

        response = self.patch(
            self._url(bookmark),
            {
                "article_asset": article_asset.id,
                **self._anchor(),
                "progress": 0.35,
                "scroll_top": 1200,
                "scroll_height": 4000,
                "client_width": 1200,
                "client_height": 800,
            },
        )

        self.assertEqual(response.data["article_asset"], article_asset.id)
        self.assertEqual(response.data["text_position_start"], 42)
        self.assertEqual(response.data["text_quote_exact"], "test paragraph")
        self.assertEqual(response.data["progress"], 0.35)
        self.assertEqual(response.data["scroll_top"], 1200)

    def test_patch_saves_element_selector(self):
        bookmark = self.setup_bookmark()
        selector = {"tag": "IMG", "index": 2}

        response = self.patch(
            self._url(bookmark),
            {"element_selector": selector, "progress": 0.4},
        )

        self.assertEqual(response.data["element_selector"], selector)

    def test_post_form_saves_anchor_from_beacon_payload(self):
        bookmark = self.setup_bookmark()
        article_asset = self.setup_asset(
            bookmark=bookmark,
            asset_type=BookmarkAsset.TYPE_ARTICLE,
            content_type=BookmarkAsset.CONTENT_TYPE_HTML,
        )

        response = self.client.post(
            self._url(bookmark),
            urlencode(
                {
                    "article_asset": str(article_asset.id),
                    "text_position_start": "100",
                    "text_quote_exact": "visible text",
                    "text_quote_prefix": "before ",
                    "text_quote_suffix": " after",
                    "progress": "0.45",
                    "scroll_top": "1800",
                    "scroll_height": "5000",
                    "client_width": "1200",
                    "client_height": "1000",
                }
            ),
            content_type="application/x-www-form-urlencoded; charset=UTF-8",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        progress = ReadingProgress.objects.get(user=self.user, bookmark=bookmark)
        self.assertEqual(progress.text_position_start, 100)
        self.assertEqual(progress.text_quote_exact, "visible text")
        self.assertEqual(progress.progress, 0.45)
        self.assertEqual(progress.scroll_top, 1800)

    def test_post_form_saves_eof_from_beacon_payload(self):
        bookmark = self.setup_bookmark()
        article_asset = self.setup_asset(
            bookmark=bookmark,
            asset_type=BookmarkAsset.TYPE_ARTICLE,
            content_type=BookmarkAsset.CONTENT_TYPE_HTML,
        )

        response = self.client.post(
            self._url(bookmark),
            urlencode(
                {
                    "article_asset": str(article_asset.id),
                    "text_position_start": "",
                    "text_quote_exact": "",
                    "text_quote_prefix": "",
                    "text_quote_suffix": "",
                    "element_selector": "",
                    "progress": "1",
                    "scroll_top": "4000",
                    "scroll_height": "5000",
                    "client_width": "1200",
                    "client_height": "1000",
                }
            ),
            content_type="application/x-www-form-urlencoded; charset=UTF-8",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        progress = ReadingProgress.objects.get(user=self.user, bookmark=bookmark)
        self.assertIsNone(progress.text_position_start)
        self.assertEqual(progress.text_quote_exact, "")
        self.assertIsNone(progress.element_selector)
        self.assertEqual(progress.progress, 1)

    def test_post_form_saves_non_text_anchor_from_beacon_payload(self):
        bookmark = self.setup_bookmark()
        article_asset = self.setup_asset(
            bookmark=bookmark,
            asset_type=BookmarkAsset.TYPE_ARTICLE,
            content_type=BookmarkAsset.CONTENT_TYPE_HTML,
        )

        response = self.client.post(
            self._url(bookmark),
            urlencode(
                {
                    "article_asset": str(article_asset.id),
                    "text_position_start": "",
                    "text_quote_exact": "",
                    "text_quote_prefix": "",
                    "text_quote_suffix": "",
                    "element_selector": '{"tag":"IMG","index":2}',
                    "progress": "0.35",
                    "scroll_top": "800",
                    "scroll_height": "4000",
                    "client_width": "1200",
                    "client_height": "800",
                }
            ),
            content_type="application/x-www-form-urlencoded; charset=UTF-8",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        progress = ReadingProgress.objects.get(user=self.user, bookmark=bookmark)
        self.assertIsNone(progress.text_position_start)
        self.assertEqual(progress.text_quote_exact, "")
        self.assertEqual(
            progress.element_selector,
            {"tag": "IMG", "index": 2},
        )
        self.assertEqual(progress.progress, 0.35)

    def test_patch_updates_existing_progress(self):
        bookmark = self.setup_bookmark()

        self.patch(self._url(bookmark), {"progress": 0.25})
        self.patch(self._url(bookmark), {"progress": 0.75, "scroll_top": 300})

        progress = ReadingProgress.objects.get(user=self.user, bookmark=bookmark)
        self.assertEqual(progress.progress, 0.75)
        self.assertEqual(progress.scroll_top, 300)
        self.assertEqual(
            ReadingProgress.objects.filter(user=self.user, bookmark=bookmark).count(),
            1,
        )

    def test_patch_with_current_base_date_updates_existing_progress(self):
        bookmark = self.setup_bookmark()

        initial = self.patch(self._url(bookmark), {"progress": 0.25})
        response = self.patch(
            self._url(bookmark),
            {
                "base_date_modified": initial.data["date_modified"],
                "progress": 0.75,
            },
        )

        self.assertEqual(response.data["progress"], 0.75)
        self.assertNotEqual(response.data["date_modified"], initial.data["date_modified"])

    def test_patch_with_stale_base_date_does_not_overwrite_progress(self):
        bookmark = self.setup_bookmark()

        first = self.patch(self._url(bookmark), {"progress": 0.25})
        self.patch(
            self._url(bookmark),
            {
                "base_date_modified": first.data["date_modified"],
                "progress": 0.5,
            },
        )

        response = self.patch(
            self._url(bookmark),
            {
                "base_date_modified": first.data["date_modified"],
                "progress": 0.9,
            },
            expected_status_code=status.HTTP_409_CONFLICT,
        )

        self.assertEqual(
            response.data["detail"],
            "Reading progress has been updated elsewhere.",
        )
        progress = ReadingProgress.objects.get(user=self.user, bookmark=bookmark)
        self.assertEqual(progress.progress, 0.5)

    def test_409_returns_latest_date_modified_for_client_sync(self):
        """409 响应必须包含最新的 date_modified，客户端用它更新 baseDateModified 后下次 PATCH 成功。"""
        bookmark = self.setup_bookmark()

        # 设备 A 创建进度
        first = self.patch(self._url(bookmark), {"progress": 0.25})
        first_date = first.data["date_modified"]

        # 设备 B 更新进度（使用当前 date_modified）
        self.patch(
            self._url(bookmark),
            {"base_date_modified": first_date, "progress": 0.5},
        )

        # 设备 A 用过期的 date_modified → 409
        conflict = self.patch(
            self._url(bookmark),
            {"base_date_modified": first_date, "progress": 0.6},
            expected_status_code=status.HTTP_409_CONFLICT,
        )

        # 409 响应必须包含最新 date_modified（设备 B 写入后的值）
        latest_date = conflict.data["date_modified"]
        self.assertIsNotNone(latest_date)
        self.assertNotEqual(latest_date, first_date)

        # 设备 A 用最新 date_modified 重试 → 成功
        retry = self.patch(
            self._url(bookmark),
            {"base_date_modified": latest_date, "progress": 0.6},
        )
        self.assertEqual(retry.status_code, status.HTTP_200_OK)
        progress = ReadingProgress.objects.get(user=self.user, bookmark=bookmark)
        self.assertEqual(progress.progress, 0.6)

    def test_patch_without_base_date_modified_uses_last_write_wins(self):
        """不发送 base_date_modified 时走 last-write-wins，不返回 409。"""
        bookmark = self.setup_bookmark()

        # 创建进度
        self.patch(self._url(bookmark), {"progress": 0.25})

        # 不发送 base_date_modified → 应该成功（last-write-wins）
        response = self.patch(
            self._url(bookmark),
            {"progress": 0.75},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        progress = ReadingProgress.objects.get(user=self.user, bookmark=bookmark)
        self.assertEqual(progress.progress, 0.75)

    def test_delete_article_asset_preserves_progress(self):
        bookmark = self.setup_bookmark()
        article_asset = self.setup_asset(
            bookmark=bookmark,
            asset_type=BookmarkAsset.TYPE_ARTICLE,
            content_type=BookmarkAsset.CONTENT_TYPE_HTML,
        )
        progress = ReadingProgress.objects.create(
            user=self.user,
            bookmark=bookmark,
            article_asset=article_asset,
            progress=0.4,
            scroll_top=400,
        )

        articles.remove_article(article_asset)

        progress.refresh_from_db()
        self.assertIsNone(progress.article_asset)
        self.assertEqual(progress.progress, 0.4)
        self.assertEqual(progress.scroll_top, 400)

    def test_progress_is_scoped_to_current_user(self):
        bookmark = self.setup_bookmark()
        other_user = self.setup_user()

        ReadingProgress.objects.create(
            user=other_user,
            bookmark=bookmark,
            progress=0.9,
            scroll_top=900,
        )

        response = self.get(self._url(bookmark))

        self.assertEqual(response.data["progress"], 0)
        self.assertEqual(
            ReadingProgress.objects.filter(bookmark=bookmark).count(),
            1,
        )

    def test_shared_bookmark_progress_is_saved_for_reader(self):
        owner = self.setup_user(enable_sharing=True)
        bookmark = self.setup_bookmark(user=owner, shared=True)

        response = self.patch(self._url(bookmark), {"progress": 0.42})

        self.assertEqual(response.data["progress"], 0.42)
        self.assertTrue(
            ReadingProgress.objects.filter(
                user=self.user,
                bookmark=bookmark,
                progress=0.42,
            ).exists()
        )

    def test_patch_rejects_asset_from_different_bookmark(self):
        bookmark = self.setup_bookmark()
        other_bookmark = self.setup_bookmark()
        other_asset = self.setup_asset(
            bookmark=other_bookmark,
            asset_type=BookmarkAsset.TYPE_ARTICLE,
            content_type=BookmarkAsset.CONTENT_TYPE_HTML,
        )

        response = self.patch(
            self._url(bookmark),
            {"article_asset": other_asset.id},
            expected_status_code=status.HTTP_400_BAD_REQUEST,
        )

        self.assertIn("article_asset", response.data)

    def test_patch_rejects_non_article_asset(self):
        bookmark = self.setup_bookmark()
        snapshot_asset = self.setup_asset(
            bookmark=bookmark,
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            content_type=BookmarkAsset.CONTENT_TYPE_HTML,
        )

        response = self.patch(
            self._url(bookmark),
            {"article_asset": snapshot_asset.id},
            expected_status_code=status.HTTP_400_BAD_REQUEST,
        )

        self.assertIn("article_asset", response.data)

    def test_requires_authentication(self):
        self.client.credentials()
        bookmark = self.setup_bookmark()

        self.get(self._url(bookmark), expected_status_code=status.HTTP_401_UNAUTHORIZED)
