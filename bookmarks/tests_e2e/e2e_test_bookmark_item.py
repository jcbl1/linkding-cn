import re
from datetime import timedelta
from unittest import skip

from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from bookmarks.tests_e2e.helpers import LinkdingE2ETestCase


class BookmarkItemE2ETestCase(LinkdingE2ETestCase):
    def test_quick_edit_switches_from_description_editor(self):
        now = timezone.now()
        first_bookmark = self.setup_bookmark(
            title="First bookmark",
            description="First description",
            added=now,
        )
        second_bookmark = self.setup_bookmark(
            title="Second bookmark",
            description="Second description",
            added=now - timedelta(minutes=1),
        )

        with sync_playwright() as p:
            self.open(reverse("linkding:bookmarks.index"), p)

            first_item = self.locate_bookmark(first_bookmark.title)
            second_item = self.locate_bookmark(second_bookmark.title)
            first_description_button = first_item.locator(
                'button.quick-edit-btn[data-quick-edit="description"]'
            )
            first_title_button = first_item.locator(
                'button.quick-edit-btn[data-quick-edit="title"]'
            )
            second_description_button = second_item.locator(
                'button.quick-edit-btn[data-quick-edit="description"]'
            )

            first_description_button.click()
            expect(
                first_item.locator(
                    ".description-container .quick-edit-description-textarea"
                )
            ).to_be_visible()
            expect(first_item.locator(".inline-edit-desc")).to_have_count(0)
            expect(first_description_button).to_have_class(re.compile(r"\bactive\b"))
            expect(first_description_button).to_have_attribute("aria-pressed", "true")
            expect(first_title_button).to_have_attribute("aria-pressed", "false")

            first_title_button.click()
            expect(
                first_item.locator(
                    ".description-container .quick-edit-description-textarea"
                )
            ).to_have_count(0)
            expect(first_item.locator(".quick-edit-title-input")).to_be_visible()
            expect(first_description_button).to_have_attribute(
                "aria-pressed", "false"
            )
            expect(first_title_button).to_have_class(re.compile(r"\bactive\b"))
            expect(first_title_button).to_have_attribute("aria-pressed", "true")

            second_description_button.click()
            expect(
                second_item.locator(
                    ".description-container .quick-edit-description-textarea"
                )
            ).to_be_visible()
            expect(second_item.locator(".inline-edit-desc")).to_have_count(0)
            expect(second_description_button).to_have_class(re.compile(r"\bactive\b"))
            expect(first_title_button).to_have_attribute("aria-pressed", "false")

            first_title_button.click()
            expect(
                second_item.locator(
                    ".description-container .quick-edit-description-textarea"
                )
            ).to_have_count(0)
            expect(first_item.locator(".quick-edit-title-input")).to_be_visible()
            expect(second_description_button).to_have_attribute(
                "aria-pressed", "false"
            )
            expect(first_title_button).to_have_attribute("aria-pressed", "true")

    @skip("Fails in CI, needs investigation")
    def test_toggle_notes_should_show_hide_notes(self):
        bookmark = self.setup_bookmark(notes="Test notes")

        with sync_playwright() as p:
            page = self.open(reverse("linkding:bookmarks.index"), p)

            notes = self.locate_bookmark(bookmark.title).locator(".inline-edit-notes")
            expect(notes).to_be_hidden()

            toggle_notes = page.locator("li button.toggle-notes")
            toggle_notes.click()
            expect(notes).to_be_visible()

            toggle_notes.click()
            expect(notes).to_be_hidden()
