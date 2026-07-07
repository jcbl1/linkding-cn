import datetime
import re

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import formats, timezone

from bookmarks.models import BookmarkAsset, UserProfile
from bookmarks.tests.helpers import BookmarkFactoryMixin, HtmlTestMixin


class BookmarkDetailsModalTestCase(TestCase, BookmarkFactoryMixin, HtmlTestMixin):
    def setUp(self):
        user = self.get_or_create_test_user()
        self.client.force_login(user)

    def get_index_details_modal(self, bookmark):
        url = reverse("linkding:bookmarks.index") + f"?details={bookmark.id}"
        response = self.client.get(url)
        soup = self.make_soup(response.content.decode())
        return soup.select_one("ld-details-modal")

    def get_shared_details_modal(self, bookmark):
        url = reverse("linkding:bookmarks.shared") + f"?details={bookmark.id}"
        response = self.client.get(url)
        soup = self.make_soup(response.content.decode())
        return soup.select_one("ld-details-modal")

    def has_details_modal(self, response):
        soup = self.make_soup(response.content.decode())
        return soup.select_one("ld-details-modal") is not None

    def find_section(self, soup, label_text):
        """Find a detail-section by its label text."""
        for label in soup.find_all(["div", "span"], {"class": "detail-label"}):
            if label.text.strip() == label_text:
                return label.find_parent("div", {"class": "detail-section"})
        return None

    def find_weblink(self, soup, url):
        return soup.find("a", {"class": "weblink", "href": url})

    def count_weblinks(self, soup):
        return len(soup.find_all("a", {"class": "weblink"}))

    def find_asset(self, soup, asset):
        return soup.find("div", {"data-asset-id": asset.id})

    # ---- Access ----

    def test_access(self):
        # own bookmark
        bookmark = self.setup_bookmark()
        response = self.client.get(
            reverse("linkding:bookmarks.index") + f"?details={bookmark.id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.has_details_modal(response))

        # other user's bookmark
        other_user = self.setup_user()
        bookmark = self.setup_bookmark(user=other_user)
        response = self.client.get(
            reverse("linkding:bookmarks.index") + f"?details={bookmark.id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.has_details_modal(response))

        # non-existent bookmark
        response = self.client.get(
            reverse("linkding:bookmarks.index") + "?details=9999"
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.has_details_modal(response))

        # guest user
        self.client.logout()
        response = self.client.get(
            reverse("linkding:bookmarks.shared") + f"?details={bookmark.id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.has_details_modal(response))

    def test_access_with_sharing(self):
        other_user = self.setup_user()
        bookmark = self.setup_bookmark(shared=True, user=other_user)

        response = self.client.get(
            reverse("linkding:bookmarks.shared") + f"?details={bookmark.id}"
        )
        self.assertFalse(self.has_details_modal(response))

        profile = other_user.profile
        profile.enable_sharing = True
        profile.save()
        response = self.client.get(
            reverse("linkding:bookmarks.shared") + f"?details={bookmark.id}"
        )
        self.assertTrue(self.has_details_modal(response))

        self.client.logout()
        response = self.client.get(
            reverse("linkding:bookmarks.shared") + f"?details={bookmark.id}"
        )
        self.assertFalse(self.has_details_modal(response))

        profile.enable_public_sharing = True
        profile.save()
        response = self.client.get(
            reverse("linkding:bookmarks.shared") + f"?details={bookmark.id}"
        )
        self.assertTrue(self.has_details_modal(response))

    # ---- Title ----

    def test_displays_title(self):
        # with title
        bookmark = self.setup_bookmark(title="Test title")
        soup = self.get_index_details_modal(bookmark)
        title_el = soup.find("textarea", {"class": "bookmark-title-input"})
        self.assertIsNotNone(title_el)
        self.assertEqual(title_el.text.strip(), bookmark.title)

        # with URL only
        bookmark = self.setup_bookmark(title="")
        soup = self.get_index_details_modal(bookmark)
        title_el = soup.find("textarea", {"class": "bookmark-title-input"})
        self.assertIsNotNone(title_el)
        self.assertEqual(title_el.text.strip(), bookmark.url)

    # ---- Weblinks ----

    def test_website_link(self):
        bookmark = self.setup_bookmark()
        soup = self.get_index_details_modal(bookmark)
        link = soup.find("a", {"class": "detail-url-link"})
        self.assertIsNotNone(link)
        self.assertEqual(link["href"], bookmark.url)
        self.assertIn(bookmark.url, link.text)

        bookmark = self.setup_bookmark()
        soup = self.get_index_details_modal(bookmark)
        wrapper = soup.find("div", {"class": "detail-url-view"})
        image = wrapper.select_one("img.favicon")
        self.assertIsNotNone(image)
        self.assertEqual(image["src"], "/static/favicon.svg")

    def test_reader_mode_link(self):
        bookmark = self.setup_bookmark()
        soup = self.get_index_details_modal(bookmark)
        # URL is on its own line, weblinks has reader mode + internet archive
        self.assertEqual(self.count_weblinks(soup), 2)
        reader_mode_url = reverse("linkding:bookmarks.read", args=[bookmark.id])
        link = self.find_weblink(soup, reader_mode_url)
        self.assertIsNotNone(link)

    def test_internet_archive_link_with_snapshot_url(self):
        bookmark = self.setup_bookmark(web_archive_snapshot_url="https://example.com/")
        soup = self.get_index_details_modal(bookmark)
        link = self.find_weblink(soup, bookmark.web_archive_snapshot_url)
        self.assertIsNotNone(link)
        self.assertEqual(link["href"], bookmark.web_archive_snapshot_url)
        self.assertEqual(link.text.strip(), "Internet Archive")

    def test_internet_archive_link_with_fallback_url(self):
        date_added = timezone.datetime(2023, 8, 11, 21, 45, 11, tzinfo=datetime.UTC)
        bookmark = self.setup_bookmark(url="https://example.com/", added=date_added)
        fallback_url = "https://web.archive.org/web/20230811214511/https://example.com/"
        soup = self.get_index_details_modal(bookmark)
        link = self.find_weblink(soup, fallback_url)
        self.assertIsNotNone(link)

    def test_weblinks_respect_target_setting(self):
        bookmark = self.setup_bookmark(web_archive_snapshot_url="https://example.com/")
        profile = self.get_or_create_test_user().profile
        profile.bookmark_link_target = UserProfile.BOOKMARK_LINK_TARGET_BLANK
        profile.save()
        soup = self.get_index_details_modal(bookmark)
        website_link = soup.find("a", {"class": "detail-url-link"})
        self.assertEqual(website_link["target"], UserProfile.BOOKMARK_LINK_TARGET_BLANK)

        web_archive_link = self.find_weblink(soup, bookmark.web_archive_snapshot_url)
        self.assertEqual(web_archive_link["target"], UserProfile.BOOKMARK_LINK_TARGET_BLANK)

        profile.bookmark_link_target = UserProfile.BOOKMARK_LINK_TARGET_SELF
        profile.save()
        soup = self.get_index_details_modal(bookmark)
        website_link = soup.find("a", {"class": "detail-url-link"})
        self.assertEqual(website_link["target"], UserProfile.BOOKMARK_LINK_TARGET_SELF)

    # ---- Preview image ----

    def test_preview_image(self):
        # without image
        bookmark = self.setup_bookmark()
        soup = self.get_index_details_modal(bookmark)
        image = soup.select_one(".info-preview-image")
        self.assertIsNone(image)

        # with image, preview disabled
        bookmark = self.setup_bookmark(preview_image_file="example.png")
        soup = self.get_index_details_modal(bookmark)
        image = soup.select_one(".info-preview-image")
        self.assertIsNone(image)

        # preview enabled, no image
        profile = self.get_or_create_test_user().profile
        profile.enable_preview_images = True
        profile.save()
        bookmark = self.setup_bookmark()
        soup = self.get_index_details_modal(bookmark)
        image = soup.select_one(".info-preview-image")
        self.assertIsNone(image)

        # preview enabled, image present
        bookmark = self.setup_bookmark(preview_image_file="example.png")
        soup = self.get_index_details_modal(bookmark)
        image = soup.select_one(".info-preview-image")
        self.assertIsNotNone(image)
        self.assertEqual(image["src"], "/static/example.png")

    # ---- Tags ----

    def test_tags(self):
        # without tags: shows placeholder
        bookmark = self.setup_bookmark()
        soup = self.get_index_details_modal(bookmark)
        placeholder = soup.find("span", {"class": "info-placeholder"})
        self.assertIsNotNone(placeholder)

        # with tags: shows tag names
        bookmark = self.setup_bookmark(tags=[self.setup_tag(), self.setup_tag()])
        soup = self.get_index_details_modal(bookmark)
        for tag in bookmark.tags.all():
            tag_el = soup.find("span", {"class": "info-tag"}, string=f"#{tag.name}")
            self.assertIsNotNone(tag_el)

    # ---- Description ----

    def test_description(self):
        # without description — textarea exists but empty
        bookmark = self.setup_bookmark(description="")
        soup = self.get_index_details_modal(bookmark)
        textarea = soup.find("textarea", {"data-field": "description"})
        self.assertIsNotNone(textarea)
        self.assertEqual(textarea.text.strip(), "")

        # with description
        bookmark = self.setup_bookmark(description="Test description")
        soup = self.get_index_details_modal(bookmark)
        textarea = soup.find("textarea", {"data-field": "description"})
        self.assertIsNotNone(textarea)
        self.assertEqual(textarea.text.strip(), "Test description")

    # ---- Notes ----

    def test_notes(self):
        # without notes — textarea exists but empty
        bookmark = self.setup_bookmark()
        soup = self.get_index_details_modal(bookmark)
        textarea = soup.find("textarea", {"data-field": "notes"})
        self.assertIsNotNone(textarea)
        self.assertEqual(textarea.text.strip(), "")

        # with notes
        bookmark = self.setup_bookmark(notes="Test notes")
        soup = self.get_index_details_modal(bookmark)
        textarea = soup.find("textarea", {"data-field": "notes"})
        self.assertIsNotNone(textarea)
        self.assertEqual(textarea.text.strip(), "Test notes")

    # ---- Actions ----

    def test_delete_button(self):
        bookmark = self.setup_bookmark()
        modal = self.get_index_details_modal(bookmark)
        delete_button = modal.find("button", {"name": "trash"})
        self.assertIsNotNone(delete_button)
        self.assertEqual(delete_button["value"], str(bookmark.id))
        self.assertTrue(delete_button.has_attr("ld-confirm-button"))

    def test_actions_visibility(self):
        # own bookmark — has footer actions
        bookmark = self.setup_bookmark()
        soup = self.get_index_details_modal(bookmark)
        trash_btn = soup.find("button", {"name": "trash"})
        self.assertIsNotNone(trash_btn)

        # other user's bookmark — no footer actions
        other_user = self.setup_user(enable_sharing=True)
        bookmark = self.setup_bookmark(user=other_user, shared=True)
        soup = self.get_shared_details_modal(bookmark)
        trash_btn = soup.find("button", {"name": "trash"})
        self.assertIsNone(trash_btn)

    def test_status_buttons(self):
        profile = self.get_or_create_test_user().profile
        profile.enable_sharing = True
        profile.save()

        # own bookmark — has status chips
        bookmark = self.setup_bookmark()
        soup = self.get_index_details_modal(bookmark)
        archive_chip = soup.find(attrs={"data-chip-field": "is_archived"})
        shared_chip = soup.find(attrs={"data-chip-field": "shared"})
        unread_chip = soup.find(attrs={"data-chip-field": "unread"})
        self.assertIsNotNone(archive_chip)
        self.assertIsNotNone(unread_chip)
        self.assertIsNotNone(shared_chip)

        # not archived → uses #ld-icon-archive
        use = archive_chip.find("use")
        self.assertIn("ld-icon-archive", use.get("xlink:href", ""))

        # archived
        bookmark = self.setup_bookmark(is_archived=True)
        soup = self.get_index_details_modal(bookmark)
        archive_chip = soup.find(attrs={"data-chip-field": "is_archived"})
        use = archive_chip.find("use")
        self.assertIn("ld-icon-archive", use.get("xlink:href", ""))

    # ---- Date ----

    def test_date_added(self):
        bookmark = self.setup_bookmark()
        soup = self.get_index_details_modal(bookmark)

        expected_date = timezone.localtime(bookmark.date_added).strftime("%Y/%m/%d")
        dates = soup.find_all("span", {"class": "info-date"})
        date_texts = [d.get_text() for d in dates]
        self.assertTrue(any(expected_date in t for t in date_texts), f"Expected {expected_date} in {date_texts}")

    # ---- Assets ----

    def test_asset_list_visibility(self):
        # no assets
        bookmark = self.setup_bookmark()
        soup = self.get_index_details_modal(bookmark)
        section = self.find_section(soup, "Files")
        asset_list = section.find("div", {"class": "info-files"}) if section else None
        self.assertIsNone(asset_list)

        # with assets
        bookmark = self.setup_bookmark()
        self.setup_asset(bookmark)
        soup = self.get_index_details_modal(bookmark)
        section = self.find_section(soup, "Files")
        self.assertIsNotNone(section)
        asset_list = section.find("div", {"class": "info-files"})
        self.assertIsNotNone(asset_list)

    def test_asset_list(self):
        bookmark = self.setup_bookmark()
        assets = [self.setup_asset(bookmark) for _ in range(3)]
        soup = self.get_index_details_modal(bookmark)
        section = self.find_section(soup, "Files")
        asset_list = section.find("div", {"class": "info-files"})
        for asset in assets:
            asset_item = self.find_asset(asset_list, asset)
            self.assertIsNotNone(asset_item)

    def test_asset_actions_visibility(self):
        bookmark = self.setup_bookmark()
        asset = self.setup_asset(bookmark)
        soup = self.get_index_details_modal(bookmark)
        asset_item = self.find_asset(soup, asset)
        file_link = asset_item.find("a", {"class": "info-file-link"})
        delete_button = asset_item.find("button", {"name": "remove_asset"})
        self.assertIsNotNone(file_link)
        self.assertIsNotNone(delete_button)

        # shared bookmark — no delete
        other_user = self.setup_user(enable_sharing=True, enable_public_sharing=True)
        bookmark = self.setup_bookmark(shared=True, user=other_user)
        asset = self.setup_asset(bookmark)
        soup = self.get_shared_details_modal(bookmark)
        asset_item = self.find_asset(soup, asset)
        delete_button = asset_item.find("button", {"name": "remove_asset"})
        self.assertIsNone(delete_button)

    # ---- Non-editable (shared view) ----

    def test_non_editable_fields(self):
        other_user = self.setup_user(enable_sharing=True)
        bookmark = self.setup_bookmark(
            user=other_user, shared=True,
            description="Test desc", notes="Test notes",
        )
        soup = self.get_shared_details_modal(bookmark)
        # Title textarea should be disabled
        title_el = soup.find("textarea", {"class": "bookmark-title-input"})
        self.assertIsNotNone(title_el)
        self.assertTrue(title_el.has_attr("disabled"))
        # No textareas for description/notes (readonly divs instead)
        textarea = soup.find("textarea", {"data-field": "description"})
        self.assertIsNone(textarea)
