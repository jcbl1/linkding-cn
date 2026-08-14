from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from bookmarks.models import Bookmark, User
from bookmarks.services.bookmarks import create_bookmark
from bookmarks.services.importer import import_netscape_html
from bookmarks.tests.helpers import (
    BookmarkFactoryMixin,
    BookmarkHtmlTag,
    ImportTestMixin,
    disable_logging,
)
from bookmarks.utils import normalize_url


class BookmarkUniqueConstraintTest(TestCase, BookmarkFactoryMixin):
    """Test the unique constraint on (owner, url_normalized)"""

    def test_unique_constraint_prevents_duplicates(self):
        user = self.get_or_create_test_user()
        url = "https://example.com/page"

        self.setup_bookmark(url=url)
        with self.assertRaises(IntegrityError):
            Bookmark.objects.create(
                url=url,
                title="Duplicate",
                date_added=timezone.now(),
                date_modified=timezone.now(),
                owner=user,
            )

    def test_unique_constraint_allows_same_url_different_users(self):
        user1 = self.get_or_create_test_user()
        user2 = User.objects.create_user("user2", "user2@example.com", "pass")
        url = "https://example.com/page"

        self.setup_bookmark(url=url, user=user1)
        # Should not raise
        Bookmark.objects.create(
            url=url,
            title="User 2",
            date_added=timezone.now(),
            date_modified=timezone.now(),
            owner=user2,
        )
        self.assertEqual(Bookmark.objects.filter(url=url).count(), 2)

    def test_unique_constraint_treats_normalized_urls_as_same(self):
        user = self.get_or_create_test_user()
        url1 = "https://example.com/"
        url2 = "https://example.com"

        self.assertEqual(normalize_url(url1), normalize_url(url2))

        self.setup_bookmark(url=url1)
        with self.assertRaises(IntegrityError):
            Bookmark.objects.create(
                url=url2,
                title="Duplicate",
                date_added=timezone.now(),
                date_modified=timezone.now(),
                owner=user,
            )


class BookmarkCreateDedupTest(TestCase, BookmarkFactoryMixin):
    """Test that create_bookmark handles deduplication correctly"""

    @disable_logging
    def test_create_bookmark_upserts_on_duplicate_url(self):
        user = self.get_or_create_test_user()
        url = "https://example.com/page"

        bookmark1 = Bookmark(url=url, title="First")
        result1 = create_bookmark(bookmark1, "tag1", user)

        bookmark2 = Bookmark(url=url, title="Second")
        result2 = create_bookmark(bookmark2, "tag2", user)

        # Should return the same bookmark (updated)
        self.assertEqual(result1.id, result2.id)
        self.assertEqual(Bookmark.objects.filter(owner=user).count(), 1)

        # Should have updated content
        updated = Bookmark.objects.get(id=result1.id)
        self.assertEqual(updated.title, "Second")

    @disable_logging
    def test_create_bookmark_uses_normalized_url_for_dedup(self):
        user = self.get_or_create_test_user()
        url1 = "https://example.com/"
        url2 = "https://example.com"

        bookmark1 = Bookmark(url=url1, title="First")
        result1 = create_bookmark(bookmark1, "", user)

        bookmark2 = Bookmark(url=url2, title="Second")
        result2 = create_bookmark(bookmark2, "", user)

        self.assertEqual(result1.id, result2.id)
        self.assertEqual(Bookmark.objects.filter(owner=user).count(), 1)


class BookmarkImportDedupTest(TestCase, BookmarkFactoryMixin, ImportTestMixin):
    """Test that importer uses normalized URL for deduplication"""

    def test_import_uses_normalized_url_for_dedup(self):
        user = self.get_or_create_test_user()

        # First import with trailing slash
        html_tags = [
            BookmarkHtmlTag(
                href="https://example.com/",
                title="Title 1",
                add_date="1",
                last_modified="1",
            ),
        ]
        import_html = self.render_html(tags=html_tags)

        import_netscape_html(import_html, user)
        self.assertEqual(Bookmark.objects.filter(owner=user).count(), 1)

        # Second import without trailing slash (same normalized URL)
        html_tags = [
            BookmarkHtmlTag(
                href="https://example.com",
                title="Title 2",
                add_date="2",
                last_modified="2",
            ),
        ]
        import_html = self.render_html(tags=html_tags)
        import_netscape_html(import_html, user)

        # Should still have only one bookmark (updated, not duplicated)
        self.assertEqual(Bookmark.objects.filter(owner=user).count(), 1)
        bookmark = Bookmark.objects.filter(owner=user).first()
        self.assertEqual(bookmark.title, "Title 2")

    def test_import_deduplicates_urls_within_same_batch(self):
        user = self.get_or_create_test_user()
        html_tags = [
            BookmarkHtmlTag(
                href="https://example.com/",
                title="First title",
                tags="tag1",
                add_date="1",
                last_modified="1",
            ),
            BookmarkHtmlTag(
                href="https://example.com",
                title="Second title",
                tags="tag2",
                add_date="2",
                last_modified="2",
            ),
        ]
        import_html = self.render_html(tags=html_tags)

        result = import_netscape_html(import_html, user)

        self.assertEqual(result.total, 2)
        self.assertEqual(result.success, 2)
        self.assertEqual(result.failed, 0)
        self.assertEqual(Bookmark.objects.filter(owner=user).count(), 1)
        bookmark = Bookmark.objects.get(owner=user)
        self.assertEqual(bookmark.title, "Second title")
        self.assertEqual(bookmark.tags.count(), 2)

    def test_import_deduplicates_urls_across_batches(self):
        user = self.get_or_create_test_user()
        html_tags = [
            BookmarkHtmlTag(
                href=f"https://example.com/{index}",
                title=f"Title {index}",
                tags=f"tag-{index}",
                add_date="1",
                last_modified="1",
            )
            for index in range(201)
        ]
        html_tags[200] = BookmarkHtmlTag(
            href="https://example.com/0",
            title="Duplicate title",
            tags="duplicate-tag",
            add_date="2",
            last_modified="2",
        )
        import_html = self.render_html(tags=html_tags)

        result = import_netscape_html(import_html, user)

        self.assertEqual(result.total, 201)
        self.assertEqual(result.success, 201)
        self.assertEqual(result.failed, 0)
        self.assertEqual(Bookmark.objects.filter(owner=user).count(), 200)
        bookmark = Bookmark.objects.get(owner=user, url="https://example.com/0")
        self.assertEqual(bookmark.title, "Duplicate title")
        self.assertEqual(bookmark.tags.count(), 2)
