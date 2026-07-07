from django.contrib.auth.models import User
from django.http import HttpResponse
from django.template import RequestContext, Template
from django.test import RequestFactory, TestCase

from bookmarks.middlewares import LinkdingMiddleware
from bookmarks.models import BookmarkSearch, UserProfile
from bookmarks.tests.helpers import BookmarkFactoryMixin, HtmlTestMixin
from bookmarks.views import contexts


class TagTreeContextTest(TestCase, BookmarkFactoryMixin):
    """Tests for the recursive smart-tree co-occurrence context."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.user = self.get_or_create_test_user()
        self.profile = self.user.profile
        self.profile.tag_grouping = UserProfile.TAG_GROUPING_SMART_TREE
        self.profile.save()

    def _make_request(self, url="/test"):
        rf = RequestFactory()
        request = rf.get(url)
        request.user = self.user
        middleware = LinkdingMiddleware(lambda r: HttpResponse())
        middleware(request)
        return request

    def _get_tag_cloud(self, url="/test"):
        request = self._make_request(url)
        search = BookmarkSearch.from_request(
            request, request.GET, request.user_profile.search_preferences
        )
        return contexts.ActiveTagCloudContext(request, search)

    # ------------------------------------------------------------------
    # Basic mode tests
    # ------------------------------------------------------------------

    def _get_children(self, path_names):
        """Helper: get children for a path using the AJAX endpoint logic."""
        request = self._make_request()
        search = BookmarkSearch.from_request(
            request, request.GET, request.user_profile.search_preferences
        )
        rc = contexts.ActiveBookmarksContext(request)
        return contexts.get_tag_tree_children(rc, request.user, search, path_names)

    def test_smart_tree_mode_produces_tag_tree(self):
        tag_a = self.setup_tag(name="alpha")
        tag_b = self.setup_tag(name="beta")
        self.setup_bookmark(tags=[tag_a, tag_b])

        tag_cloud = self._get_tag_cloud()

        self.assertEqual(tag_cloud.tag_grouping, UserProfile.TAG_GROUPING_SMART_TREE)
        self.assertEqual(tag_cloud.groups, [])
        self.assertTrue(len(tag_cloud.tag_tree) > 0)

    def test_smart_tree_roots_sorted_by_count(self):
        tag_a = self.setup_tag(name="alpha")
        tag_b = self.setup_tag(name="beta")
        tag_c = self.setup_tag(name="gamma")

        self.setup_bookmark(tags=[tag_a, tag_b, tag_c])
        self.setup_bookmark(tags=[tag_a, tag_b])
        self.setup_bookmark(tags=[tag_a])

        tag_cloud = self._get_tag_cloud()

        root_names = [node.name for node in tag_cloud.tag_tree]
        self.assertEqual(root_names[0], "alpha")
        self.assertEqual(root_names[1], "beta")
        self.assertEqual(root_names[2], "gamma")

    def test_smart_tree_root_count(self):
        tag_a = self.setup_tag(name="alpha")
        tag_b = self.setup_tag(name="beta")

        self.setup_bookmark(tags=[tag_a, tag_b])
        self.setup_bookmark(tags=[tag_a])

        tag_cloud = self._get_tag_cloud()

        alpha = next(n for n in tag_cloud.tag_tree if n.name == "alpha")
        self.assertEqual(alpha.count, 2)
        self.assertEqual(alpha.co_count, 0)  # roots have co_count=0

        beta = next(n for n in tag_cloud.tag_tree if n.name == "beta")
        self.assertEqual(beta.count, 1)

    # ------------------------------------------------------------------
    # Co-occurrence children tests
    # ------------------------------------------------------------------

    def test_children_sorted_by_co_count(self):
        tag_a = self.setup_tag(name="alpha")
        tag_b = self.setup_tag(name="beta")
        tag_c = self.setup_tag(name="gamma")

        # alpha+beta: 3 co-occurrences, alpha+gamma: 1
        self.setup_bookmark(tags=[tag_a, tag_b, tag_c])
        self.setup_bookmark(tags=[tag_a, tag_b])
        self.setup_bookmark(tags=[tag_a, tag_b])

        children = self._get_children(["alpha"])
        self.assertEqual(children[0].name, "beta")
        self.assertEqual(children[0].co_count, 3)
        self.assertEqual(children[1].name, "gamma")
        self.assertEqual(children[1].co_count, 1)

    def test_no_co_occurrences(self):
        tag_a = self.setup_tag(name="alpha")
        self.setup_bookmark(tags=[tag_a])

        children = self._get_children(["alpha"])
        self.assertEqual(children, [])

    # ------------------------------------------------------------------
    # Recursive / multi-level tests
    # ------------------------------------------------------------------

    def test_grandchildren_present(self):
        """Children of children should appear when co-occurring with the
        full ancestor path."""
        tag_a = self.setup_tag(name="alpha")
        tag_b = self.setup_tag(name="beta")
        tag_c = self.setup_tag(name="gamma")

        self.setup_bookmark(tags=[tag_a, tag_b, tag_c])

        children = self._get_children(["alpha"])
        beta_child = next(c for c in children if c.name == "beta")
        self.assertTrue(beta_child.has_children)

        grandchildren = self._get_children(["alpha", "beta"])
        gc_names = [c.name for c in grandchildren]
        self.assertIn("gamma", gc_names)
        self.assertNotIn("alpha", gc_names)

    def test_grandchild_co_count_uses_full_path(self):
        """Grandchild co_count = bookmarks with ALL ancestor tags,
        not just the pairwise parent+child intersection."""
        tag_a = self.setup_tag(name="alpha")
        tag_b = self.setup_tag(name="beta")
        tag_c = self.setup_tag(name="gamma")

        # 3 bookmarks: all have alpha+beta, only 1 also has gamma
        self.setup_bookmark(tags=[tag_a, tag_b, tag_c])
        self.setup_bookmark(tags=[tag_a, tag_b])
        self.setup_bookmark(tags=[tag_a, tag_b])

        # Additionally, beta+gamma co-occur WITHOUT alpha
        self.setup_bookmark(tags=[tag_b, tag_c])

        # Under alpha>beta, gamma should have co_count=1 (not 2)
        grandchildren = self._get_children(["alpha", "beta"])
        gamma_gc = next(c for c in grandchildren if c.name == "gamma")
        self.assertEqual(gamma_gc.co_count, 1)

    def test_no_cycles(self):
        """A triangle A-B-C-A must NOT produce an infinite tree."""
        tag_a = self.setup_tag(name="alpha")
        tag_b = self.setup_tag(name="beta")
        tag_c = self.setup_tag(name="gamma")

        self.setup_bookmark(tags=[tag_a, tag_b, tag_c])

        # Under alpha: beta should appear, but alpha must NOT
        # appear as a descendant of beta (cycle prevention).
        children = self._get_children(["alpha"])
        beta_child = next(c for c in children if c.name == "beta")
        self.assertTrue(beta_child.has_children)

        grandchildren = self._get_children(["alpha", "beta"])
        gc_names = [c.name for c in grandchildren]
        self.assertNotIn("alpha", gc_names)
        self.assertIn("gamma", gc_names)

        # Under alpha>beta>gamma: must not contain alpha or beta
        great_grandchildren = self._get_children(["alpha", "beta", "gamma"])
        ggc_names = [c.name for c in great_grandchildren]
        self.assertNotIn("alpha", ggc_names)
        self.assertNotIn("beta", ggc_names)

    def test_dense_path_goes_deep(self):
        """A chain of tags all on the same bookmarks should expand fully."""
        tags = [self.setup_tag(name=f"t{i}") for i in range(10)]
        for _ in range(10):
            self.setup_bookmark(tags=tags)

        # Walk the deepest path via AJAX-style calls
        path = [tags[0].name]
        depth = 0
        while True:
            children = self._get_children(path)
            if not children:
                break
            depth += 1
            path.append(children[0].name)

        self.assertGreater(depth, 3)

    def test_naturally_sparse_tree(self):
        """Tags with no shared bookmarks produce no children."""
        tag_a = self.setup_tag(name="alpha")
        tag_b = self.setup_tag(name="beta")
        self.setup_bookmark(tags=[tag_a])
        self.setup_bookmark(tags=[tag_b])

        tag_cloud = self._get_tag_cloud()
        for root in tag_cloud.tag_tree:
            self.assertFalse(root.has_children)

        children_a = self._get_children(["alpha"])
        children_b = self._get_children(["beta"])
        self.assertEqual(children_a, [])
        self.assertEqual(children_b, [])

    def test_all_co_occurring_children_shown(self):
        """Every tag that shares a bookmark with the root appears."""
        tag_a = self.setup_tag(name="alpha")
        co_tags = [self.setup_tag(name=f"t{i}") for i in range(15)]
        for t in co_tags:
            self.setup_bookmark(tags=[tag_a, t])

        children = self._get_children(["alpha"])
        child_names = {c.name for c in children}
        for t in co_tags:
            self.assertIn(t.name, child_names)

    def test_only_roots_rendered(self):
        """Initial build should only produce roots, not recurse into children."""
        tag_a = self.setup_tag(name="alpha")
        tag_b = self.setup_tag(name="beta")
        tag_c = self.setup_tag(name="gamma")
        self.setup_bookmark(tags=[tag_a, tag_b, tag_c])

        tag_cloud = self._get_tag_cloud()

        # All roots should exist
        self.assertEqual(len(tag_cloud.tag_tree), 3)
        # But children should NOT be pre-built (AJAX loads them)
        for root in tag_cloud.tag_tree:
            self.assertEqual(len(root.children), 0)

    # ------------------------------------------------------------------
    # Selected tags
    # ------------------------------------------------------------------

    def test_selected_tags(self):
        tag_a = self.setup_tag(name="alpha")
        tag_b = self.setup_tag(name="beta")
        self.setup_bookmark(tags=[tag_a, tag_b])

        request = self._make_request("/test?q=%23alpha")
        search = BookmarkSearch.from_request(
            request, request.GET, request.user_profile.search_preferences
        )
        tag_cloud = contexts.ActiveTagCloudContext(request, search)

        self.assertTrue(tag_cloud.has_selected_tags)
        self.assertEqual(len(tag_cloud.selected_tags), 1)
        self.assertEqual(tag_cloud.selected_tags[0].name, "alpha")


    # ------------------------------------------------------------------
    # Path query string tests
    # ------------------------------------------------------------------

    def test_root_path_query_includes_only_root_tag(self):
        tag_a = self.setup_tag(name="alpha")
        tag_b = self.setup_tag(name="beta")
        self.setup_bookmark(tags=[tag_a, tag_b])

        tag_cloud = self._get_tag_cloud()

        alpha = next(n for n in tag_cloud.tag_tree if n.name == "alpha")
        self.assertIn("%23alpha", alpha.path_query_string)
        self.assertNotIn("%23beta", alpha.path_query_string)

    def test_child_path_query_includes_root_and_child(self):
        tag_a = self.setup_tag(name="alpha")
        tag_b = self.setup_tag(name="beta")
        tag_c = self.setup_tag(name="gamma")

        # alpha+beta co-occur, beta+gamma co-occur
        self.setup_bookmark(tags=[tag_a, tag_b])
        self.setup_bookmark(tags=[tag_b, tag_c])

        children = self._get_children(["alpha"])
        beta_child = next(c for c in children if c.name == "beta")
        self.assertIn("%23alpha", beta_child.path_query_string)
        self.assertIn("%23beta", beta_child.path_query_string)

    def test_grandchild_path_query_includes_full_path(self):
        tag_a = self.setup_tag(name="alpha")
        tag_b = self.setup_tag(name="beta")
        tag_c = self.setup_tag(name="gamma")

        self.setup_bookmark(tags=[tag_a, tag_b, tag_c])
        self.setup_bookmark(tags=[tag_a, tag_b])

        grandchildren = self._get_children(["alpha", "beta"])
        gamma_gc = next(c for c in grandchildren if c.name == "gamma")
        self.assertIn("%23alpha", gamma_gc.path_query_string)
        self.assertIn("%23beta", gamma_gc.path_query_string)
        self.assertIn("%23gamma", gamma_gc.path_query_string)


    def test_cjk_tags_sorted_by_pinyin(self):
        """CJK tags should sort by pinyin, after English tags."""
        tag_a = self.setup_tag(name="apple")
        tag_z = self.setup_tag(name="zebra")
        tag_cn1 = self.setup_tag(name="日记")  # ri ji
        tag_cn2 = self.setup_tag(name="思考")  # si kao
        tag_cn3 = self.setup_tag(name="灵感")  # ling gan

        # All on one bookmark so they all appear
        self.setup_bookmark(tags=[tag_a, tag_z, tag_cn1, tag_cn2, tag_cn3])

        tag_cloud = self._get_tag_cloud()
        root_names = [n.name for n in tag_cloud.tag_tree]

        # English tags first, sorted alphabetically
        eng_idx_a = root_names.index("apple")
        eng_idx_z = root_names.index("zebra")
        self.assertLess(eng_idx_a, eng_idx_z)

        # CJK tags come after English, sorted by pinyin
        cjk_indices = [root_names.index(n) for n in ["日记", "思考", "灵感"]]
        for idx in cjk_indices:
            self.assertGreater(idx, eng_idx_z)

        # pinyin order: ling(灵) < ri(日) < si(思)
        self.assertLess(root_names.index("灵感"), root_names.index("日记"))
        self.assertLess(root_names.index("日记"), root_names.index("思考"))


class TagTreeTemplateTest(TestCase, BookmarkFactoryMixin, HtmlTestMixin):
    """Tests for the recursive tag-tree template rendering."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.user = self.get_or_create_test_user()
        self.profile = self.user.profile
        self.profile.tag_grouping = UserProfile.TAG_GROUPING_SMART_TREE
        self.profile.save()

    def _render(self, url="/test"):
        rf = RequestFactory()
        request = rf.get(url)
        request.user = self.user
        middleware = LinkdingMiddleware(lambda r: HttpResponse())
        middleware(request)
        search = BookmarkSearch.from_request(
            request, request.GET, request.user_profile.search_preferences
        )
        tag_cloud = contexts.ActiveTagCloudContext(request, search)
        ctx = RequestContext(request, {"tag_cloud": tag_cloud})
        tpl = Template(
            "{% include 'bookmarks/sidebar/modules/tags/tree.html' %}"
        )
        return tpl.render(ctx)

    def _root_node(self, soup, tag_name):
        """Find a root-level tag-tree-node by name (direct child of .tag-tree-roots)."""
        roots = soup.select_one(".tag-tree-roots")
        for li in roots.find_all("li", recursive=False):
            if li.get("data-tag-name") == tag_name:
                return li
        return None

    def test_renders_roots(self):
        tag_a = self.setup_tag(name="alpha")
        tag_b = self.setup_tag(name="beta")
        self.setup_bookmark(tags=[tag_a, tag_b])

        soup = self.make_soup(self._render())
        roots = soup.select(".tag-tree-roots > .tag-tree-node")
        self.assertEqual(len(roots), 2)

    def test_roots_mark_has_children(self):
        tag_a = self.setup_tag(name="alpha")
        tag_b = self.setup_tag(name="beta")
        tag_c = self.setup_tag(name="gamma")

        self.setup_bookmark(tags=[tag_a, tag_b, tag_c])

        soup = self.make_soup(self._render())

        alpha = self._root_node(soup, "alpha")
        self.assertIsNotNone(alpha)
        # Children are NOT pre-rendered (loaded via AJAX)
        self.assertIsNone(alpha.select_one("ul.tag-tree-children"))
        # But the node is marked as expandable
        self.assertEqual(alpha.get("data-has-children"), "true")
        self.assertIsNotNone(alpha.select_one(".tag-tree-toggle"))

    def test_root_shows_count(self):
        tag_a = self.setup_tag(name="alpha")
        tag_b = self.setup_tag(name="beta")
        self.setup_bookmark(tags=[tag_a, tag_b])
        self.setup_bookmark(tags=[tag_a])

        soup = self.make_soup(self._render())

        alpha = self._root_node(soup, "alpha")
        self.assertIn("2", alpha.select_one(".tag-tree-count").text)

        beta = self._root_node(soup, "beta")
        self.assertIn("1", beta.select_one(".tag-tree-count").text)

    def test_no_children_pre_rendered(self):
        """Children should not be in the initial HTML (loaded via AJAX)."""
        tag_a = self.setup_tag(name="alpha")
        tag_b = self.setup_tag(name="beta")
        self.setup_bookmark(tags=[tag_a, tag_b])

        soup = self.make_soup(self._render())

        # No tag-tree-children UL should exist in the initial render
        children_uls = soup.select("ul.tag-tree-children")
        self.assertEqual(len(children_uls), 0)

    def test_toggle_menu_tree_mode(self):
        """In tree mode: active item shows 'Tree mode', no grouping section."""
        tag_a = self.setup_tag(name="alpha")
        self.setup_bookmark(tags=[tag_a])

        rf = RequestFactory()
        request = rf.get("/test")
        request.user = self.user
        middleware = LinkdingMiddleware(lambda r: HttpResponse())
        middleware(request)
        search = BookmarkSearch.from_request(
            request, request.GET, request.user_profile.search_preferences
        )
        tag_cloud = contexts.ActiveTagCloudContext(request, search)
        ctx = RequestContext(request, {"tag_cloud": tag_cloud})
        tpl = Template(
            "{% include 'bookmarks/sidebar/modules/tags/index.html' %}"
        )
        soup = self.make_soup(tpl.render(ctx))

        # Active item should be "Tree mode"
        active = soup.select(".menu-link-active")
        active_texts = [a.text.strip() for a in active]
        self.assertIn("Tree mode", active_texts)
        # No grouping options in tree mode
        self.assertEqual(len(soup.select(".menu-divider")), 1)  # only after "Manage tags"

    def test_toggle_menu_flat_mode(self):
        """In flat mode: active item shows 'Flat mode', grouping section visible."""
        self.profile.tag_grouping = UserProfile.TAG_GROUPING_ALPHABETICAL
        self.profile.save()

        tag_a = self.setup_tag(name="alpha")
        self.setup_bookmark(tags=[tag_a])

        rf = RequestFactory()
        request = rf.get("/test")
        request.user = self.user
        middleware = LinkdingMiddleware(lambda r: HttpResponse())
        middleware(request)
        search = BookmarkSearch.from_request(
            request, request.GET, request.user_profile.search_preferences
        )
        tag_cloud = contexts.ActiveTagCloudContext(request, search)
        ctx = RequestContext(request, {"tag_cloud": tag_cloud})
        tpl = Template(
            "{% include 'bookmarks/sidebar/modules/tags/index.html' %}"
        )
        soup = self.make_soup(tpl.render(ctx))

        active = soup.select(".menu-link-active")
        active_texts = [a.text.strip() for a in active]
        self.assertIn("Flat mode", active_texts)
        self.assertIn("Alphabetical grouping", active_texts)
        # Two dividers: after "Manage tags" and between mode/grouping
        self.assertEqual(len(soup.select(".menu-divider")), 2)
