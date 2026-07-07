from django.core.paginator import Paginator
from django.template import RequestContext, Template
from django.test import RequestFactory, TestCase

from bookmarks.tests.helpers import BookmarkFactoryMixin


class PaginationTagTest(TestCase, BookmarkFactoryMixin):
    def render_template(
        self,
        num_items: int,
        page_size: int,
        current_page: int,
        url: str = "/test",
        frame: str = None,
    ) -> str:
        rf = RequestFactory()
        request = rf.get(url)
        request.user = self.get_or_create_test_user()
        request.user_profile = self.get_or_create_test_user().profile
        paginator = Paginator(range(0, num_items), page_size)
        page = paginator.page(current_page)

        context_dict = {"page": page}
        if frame:
            context_dict["pagination_frame"] = frame
        context = RequestContext(request, context_dict)
        template_to_render = Template("{% load pagination %}{% pagination page %}")
        return template_to_render.render(context)

    def assertPrevLinkDisabled(self, html: str):
        self.assertIn('class="page-nav prev disabled"', html)

    def assertPrevLink(
        self, html: str, page_number: int, href: str = None, frame: str = "_top"
    ):
        href = href if href else f"/test?page={page_number}"
        self.assertIn(f'href="{href}"'.replace("&", "&amp;"), html)
        self.assertIn('class="page-nav prev"', html)
        self.assertNotIn('class="page-nav prev disabled"', html)

    def assertNextLinkDisabled(self, html: str):
        self.assertIn('class="page-nav next disabled"', html)

    def assertNextLink(
        self, html: str, page_number: int, href: str = None, frame: str = "_top"
    ):
        href = href if href else f"/test?page={page_number}"
        self.assertIn(f'href="{href}"'.replace("&", "&amp;"), html)
        self.assertIn('class="page-nav next"', html)
        self.assertNotIn('class="page-nav next disabled"', html)

    def assertPageLink(
        self,
        html: str,
        page_number: int,
        active: bool,
        href: str = None,
        frame: str = "_top",
    ):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        href = href if href else f"/test?page={page_number}"
        # Find the page item with the matching number
        page_item = soup.select_one(f'li.page-item[data-number="{page_number}"]')
        self.assertIsNotNone(page_item, f"Page {page_number} not found")

        if active:
            self.assertIn("active", page_item.get("class", []))
        else:
            self.assertNotIn("active", page_item.get("class", []))

        # Check the link href
        link = page_item.select_one("a")
        self.assertIsNotNone(link)
        self.assertEqual(link.get("href"), href)

    def assertPageLinkNotRendered(self, html: str, page_number: int):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        page_item = soup.select_one(f'li.page-item[data-number="{page_number}"]')
        self.assertIsNone(page_item, f"Page {page_number} should not be rendered")

    def assertTruncationIndicators(self, html: str, count: int):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        gaps = soup.select("li.page-item.gap")
        self.assertEqual(len(gaps), count)

    def test_previous_disabled_on_page_1(self):
        rendered_template = self.render_template(100, 10, 1)
        self.assertPrevLinkDisabled(rendered_template)

    def test_previous_enabled_after_page_1(self):
        for page_number in range(2, 10):
            rendered_template = self.render_template(100, 10, page_number)
            self.assertPrevLink(rendered_template, page_number - 1)

    def test_next_disabled_on_last_page(self):
        rendered_template = self.render_template(100, 10, 10)
        self.assertNextLinkDisabled(rendered_template)

    def test_next_enabled_before_last_page(self):
        for page_number in range(1, 9):
            rendered_template = self.render_template(100, 10, page_number)
            self.assertNextLink(rendered_template, page_number + 1)

    def test_truncate_pages_start(self):
        current_page = 1
        rendered_template = self.render_template(100, 10, current_page)
        # All pages should be rendered (new pagination component renders all)
        for page_number in range(1, 11):
            self.assertPageLink(
                rendered_template,
                page_number,
                page_number == current_page,
            )
        self.assertTruncationIndicators(rendered_template, 0)

    def test_truncate_pages_middle(self):
        current_page = 5
        rendered_template = self.render_template(100, 10, current_page)
        # All pages should be rendered
        for page_number in range(1, 11):
            self.assertPageLink(
                rendered_template,
                page_number,
                page_number == current_page,
            )
        self.assertTruncationIndicators(rendered_template, 0)

    def test_truncate_pages_near_end(self):
        current_page = 9
        rendered_template = self.render_template(100, 10, current_page)
        # All pages should be rendered
        for page_number in range(1, 11):
            self.assertPageLink(
                rendered_template,
                page_number,
                page_number == current_page,
            )
        self.assertTruncationIndicators(rendered_template, 0)

    def test_respects_search_parameters(self):
        rendered_template = self.render_template(
            100, 10, 2, url="/test?q=cake&sort=title_asc&page=2"
        )
        self.assertPrevLink(
            rendered_template,
            1,
            href="/test?q=cake&sort=title_asc&page=1",
        )
        self.assertPageLink(
            rendered_template,
            1,
            False,
            href="/test?q=cake&sort=title_asc&page=1",
        )
        self.assertPageLink(
            rendered_template,
            2,
            True,
            href="/test?q=cake&sort=title_asc&page=2",
        )
        self.assertNextLink(
            rendered_template,
            3,
            href="/test?q=cake&sort=title_asc&page=3",
        )

    def test_removes_details_parameter(self):
        rendered_template = self.render_template(
            100, 10, 2, url="/test?details=1&page=2"
        )
        self.assertPrevLink(rendered_template, 1, href="/test?page=1")
        self.assertPageLink(rendered_template, 1, False, href="/test?page=1")
        self.assertPageLink(rendered_template, 2, True, href="/test?page=2")
        self.assertNextLink(rendered_template, 3, href="/test?page=3")

    def test_respects_pagination_frame(self):
        rendered_template = self.render_template(100, 10, 2, frame="my_frame")
        self.assertPrevLink(rendered_template, 1, frame="my_frame")
        self.assertPageLink(rendered_template, 1, False, frame="my_frame")
        self.assertPageLink(rendered_template, 2, True, frame="my_frame")
        self.assertNextLink(rendered_template, 3, frame="my_frame")
