from django.urls import reverse
from playwright.sync_api import expect, sync_playwright

from bookmarks.tests_e2e.helpers import LinkdingE2ETestCase


class CollapseSidePanelE2ETestCase(LinkdingE2ETestCase):
    def setUp(self) -> None:
        super().setUp()

    def assertSidePanelIsVisible(self):
        page = self.page.locator(".bookmarks-page")
        expect(page).to_have_class(lambda c: "sidebar-open" in c)
        expect(self.page.locator(".bookmarks-page .sidebar")).to_be_visible()
        expect(
            self.page.locator(".bookmarks-page [data-sidebar-toggle]")
        ).to_be_visible()

    def assertSidePanelIsHidden(self):
        page = self.page.locator(".bookmarks-page")
        expect(page).to_have_class(lambda c: "sidebar-closed" in c)
        expect(self.page.locator(".bookmarks-page .sidebar")).not_to_be_visible()
        expect(
            self.page.locator(".bookmarks-page [data-sidebar-toggle]")
        ).to_be_visible()

    def test_side_panel_should_be_visible_by_default(self):
        with sync_playwright() as p:
            self.open(reverse("linkding:bookmarks.index"), p)
            self.assertSidePanelIsVisible()

            self.page.goto(
                self.live_server_url + reverse("linkding:bookmarks.archived")
            )
            self.assertSidePanelIsVisible()

            self.page.goto(self.live_server_url + reverse("linkding:bookmarks.shared"))
            self.assertSidePanelIsVisible()

    def test_side_panel_should_be_hidden_when_collapsed(self):
        user = self.get_or_create_test_user()
        user.profile.show_sidebar = False
        user.profile.save()

        with sync_playwright() as p:
            self.open(reverse("linkding:bookmarks.index"), p)
            self.assertSidePanelIsHidden()

            self.page.goto(
                self.live_server_url + reverse("linkding:bookmarks.archived")
            )
            self.assertSidePanelIsHidden()

            self.page.goto(self.live_server_url + reverse("linkding:bookmarks.shared"))
            self.assertSidePanelIsHidden()

    def test_side_panel_toggle_button_should_toggle_visibility(self):
        with sync_playwright() as p:
            self.open(reverse("linkding:bookmarks.index"), p)

            # Initially visible
            self.assertSidePanelIsVisible()

            # Click toggle to hide
            self.page.locator("[data-sidebar-toggle]").click()
            self.assertSidePanelIsHidden()

            # Click toggle to show again
            self.page.locator("[data-sidebar-toggle]").click()
            self.assertSidePanelIsVisible()
