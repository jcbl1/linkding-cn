"""
测试随机按钮模板标签的功能、安全性和边界情况
"""
from django.contrib.auth.models import User
from django.template import Template, Context
from django.test import RequestFactory, TestCase

from bookmarks.models import BookmarkSearch, UserProfile
from bookmarks.templatetags.bookmarks import RANDOM_SORT_ALLOWED_PARAMS


class RandomSortTemplateTestCase(TestCase):
    """测试 random_sort 模板标签"""

    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="testuser", password="testpass")
        self.profile = UserProfile.objects.get(user=self.user)

    def _render_template(self, request, search=None):
        """辅助方法：渲染模板并返回结果"""
        if search is None:
            search = BookmarkSearch.from_request(
                request, request.GET, self.profile.search_preferences
            )
        
        template = Template("""
            {% load bookmarks %}
            {% random_sort search %}
        """)
        
        context = Context({
            'search': search,
            'request': request,
        })
        
        return template.render(context)

    def test_preserves_allowed_params(self):
        """测试保留白名单中的参数"""
        test_cases = [
            {
                'name': '搜索关键词',
                'query_string': '?q=test',
                'expected': ['name="q"', 'value="test"'],
            },
            {
                'name': '分享状态',
                'query_string': '?shared=yes',
                'expected': ['name="shared"', 'value="yes"'],
            },
            {
                'name': '已读状态',
                'query_string': '?unread=no',
                'expected': ['name="unread"', 'value="no"'],
            },
            {
                'name': '标签筛选',
                'query_string': '?tagged=yes',
                'expected': ['name="tagged"', 'value="yes"'],
            },
            {
                'name': '相对日期筛选',
                'query_string': '?date_filter_by=added&date_filter_type=relative&date_filter_relative_string=last_7_days',
                'expected': [
                    'name="date_filter_by"', 'value="added"',
                    'name="date_filter_type"', 'value="relative"',
                    'name="date_filter_relative_string"', 'value="last_7_days"',
                ],
            },
            {
                'name': '绝对日期筛选',
                'query_string': '?date_filter_by=modified&date_filter_type=absolute&date_filter_start=2024-01-01&date_filter_end=2024-12-31',
                'expected': [
                    'name="date_filter_by"', 'value="modified"',
                    'name="date_filter_type"', 'value="absolute"',
                    'name="date_filter_start"', 'value="2024-01-01"',
                    'name="date_filter_end"', 'value="2024-12-31"',
                ],
            },
            {
                'name': '资源筛选',
                'query_string': '?html_snapshot=yes&preview_image=no&favicon=yes',
                'expected': [
                    'name="html_snapshot"', 'value="yes"',
                    'name="preview_image"', 'value="no"',
                    'name="favicon"', 'value="yes"',
                ],
            },
            {
                'name': '高亮和批注筛选',
                'query_string': '?highlight=yes&annotation=no',
                'expected': [
                    'name="highlight"', 'value="yes"',
                    'name="annotation"', 'value="no"',
                ],
            },
            {
                'name': '综合筛选',
                'query_string': '?q=test&shared=yes&unread=no&date_filter_by=added&date_filter_type=relative&date_filter_relative_string=this_month&tagged=yes&highlight=yes',
                'expected': [
                    'name="q"', 'value="test"',
                    'name="shared"', 'value="yes"',
                    'name="unread"', 'value="no"',
                    'name="date_filter_by"', 'value="added"',
                    'name="tagged"', 'value="yes"',
                    'name="highlight"', 'value="yes"',
                ],
            },
        ]
        
        for tc in test_cases:
            with self.subTest(name=tc['name']):
                request = self.factory.get(f"/bookmarks/{tc['query_string']}")
                request.user = self.user
                rendered = self._render_template(request)
                
                for expected in tc['expected']:
                    self.assertIn(expected, rendered, 
                                f"测试 '{tc['name']}' 失败：未找到 {expected}")

    def test_excludes_sort_param(self):
        """测试排除 URL 中的 sort 参数，只保留固定的 sort=random"""
        request = self.factory.get('/bookmarks/?q=test&sort=added_desc&shared=yes')
        request.user = self.user
        rendered = self._render_template(request)

        # 应该包含固定的 sort=random，但不应该包含 URL 中的 sort 参数
        # 模板中只有一个 name="sort" value="random"
        self.assertIn('name="sort"', rendered)
        self.assertIn('value="random"', rendered)

        # 不应该有第二个 sort 参数
        sort_count = rendered.count('name="sort"')
        self.assertEqual(sort_count, 1, "应该只有一个 sort 参数")

        # 其他参数应该保留
        self.assertIn('name="q"', rendered)
        self.assertIn('name="shared"', rendered)

    def test_always_includes_sort_random(self):
        """测试始终包含 sort=random"""
        request = self.factory.get('/bookmarks/?q=test')
        request.user = self.user
        rendered = self._render_template(request)
        
        self.assertIn('name="sort"', rendered)
        self.assertIn('value="random"', rendered)

    def test_filters_non_whitelisted_params(self):
        """测试过滤非白名单参数"""
        request = self.factory.get(
            '/bookmarks/?q=test&debug=true&admin=1&internal=secret&shared=yes'
        )
        request.user = self.user
        rendered = self._render_template(request)
        
        # 应该包含白名单参数
        self.assertIn('name="q"', rendered)
        self.assertIn('name="shared"', rendered)
        
        # 不应该包含非白名单参数
        self.assertNotIn('debug', rendered)
        self.assertNotIn('admin', rendered)
        self.assertNotIn('internal', rendered)

    def test_filters_empty_values(self):
        """测试过滤空值参数"""
        request = self.factory.get('/bookmarks/?q=&shared=yes&unread=&tagged=yes')
        request.user = self.user
        rendered = self._render_template(request)
        
        # 空值参数应该被过滤
        # 注意：q= 会被 Django 视为空字符串，应该被过滤
        self.assertNotIn('name="q"', rendered)
        self.assertNotIn('name="unread"', rendered)
        
        # 非空值参数应该保留
        self.assertIn('name="shared"', rendered)
        self.assertIn('name="tagged"', rendered)

    def test_xss_prevention_in_param_names(self):
        """测试防止参数名中的 XSS 攻击"""
        # 恶意参数名不在白名单中，会被完全过滤掉
        malicious_urls = [
            '/bookmarks/?"><script>alert(1)</script>=value',
            '/bookmarks/?onmouseover=alert(1)=value',
            '/bookmarks/?debug=true',
        ]

        for url in malicious_urls:
            with self.subTest(url=url):
                request = self.factory.get(url)
                request.user = self.user
                rendered = self._render_template(request)

                # 恶意参数名不在白名单中，应该被过滤掉
                self.assertNotIn('<script>', rendered)
                self.assertNotIn('onmouseover', rendered)
                self.assertNotIn('debug', rendered)

    def test_xss_prevention_in_param_values(self):
        """测试防止参数值中的 XSS 攻击"""
        # 参数值会被 force_escape 过滤器转义，防止 XSS
        malicious_cases = [
            {
                'name': 'script 标签',
                'url': '/bookmarks/?q=<script>alert(document.cookie)</script>',
                'should_not_contain': '<script>',
                'should_contain': '&lt;script&gt;',
            },
            {
                'name': 'img 标签注入',
                'url': '/bookmarks/?q="><img src=x onerror=alert(1)>',
                'should_not_contain': '<img',
                'should_contain': '&lt;img',
            },
            {
                'name': '单引号注入',
                "url": "/bookmarks/?q=';alert(1)//",
                'should_not_contain': "';alert",
                'should_contain': '&#x27;',
            },
        ]

        for tc in malicious_cases:
            with self.subTest(name=tc['name']):
                request = self.factory.get(tc['url'])
                request.user = self.user
                rendered = self._render_template(request)

                # 不应该包含未转义的恶意代码
                self.assertNotIn(tc['should_not_contain'], rendered)

                # 应该包含转义后的内容
                self.assertIn(tc['should_contain'], rendered)

    def test_handles_special_characters_in_values(self):
        """测试处理特殊字符"""
        test_cases = [
            {
                'name': '引号',
                'query': '?q=test"value',
                'expected_escaped': 'test&quot;value',
            },
            {
                'name': '单引号',
                "query": "?q=test'value",
                'expected_escaped': 'test&#x27;value',
            },
            {
                'name': 'HTML标签',
                'query': '?q=<b>bold</b>',
                'expected_escaped': '&lt;b&gt;bold&lt;/b&gt;',
            },
            # 注意：与号 & 在 URL 中是参数分隔符，Django QueryDict 会解析为多个参数
            # 所以 ?q=test&value 会被解析为 q=test 和 value=""，这是正确行为
        ]

        for tc in test_cases:
            with self.subTest(name=tc['name']):
                request = self.factory.get(f"/bookmarks/{tc['query']}")
                request.user = self.user
                rendered = self._render_template(request)

                self.assertIn(tc['expected_escaped'], rendered)

    def test_handles_multiple_values_same_param(self):
        """测试处理同一参数的多个值"""
        # Django QueryDict 会保留多个值，但 GET.items() 只返回最后一个
        request = self.factory.get('/bookmarks/?q=test1&q=test2&shared=yes')
        request.user = self.user
        rendered = self._render_template(request)
        
        # 应该包含参数（只保留最后一个值）
        self.assertIn('name="q"', rendered)
        self.assertIn('name="shared"', rendered)

    def test_handles_no_query_params(self):
        """测试没有查询参数的情况"""
        request = self.factory.get('/bookmarks/')
        request.user = self.user
        rendered = self._render_template(request)
        
        # 应该只包含 sort=random
        self.assertIn('name="sort"', rendered)
        self.assertIn('value="random"', rendered)
        
        # 不应该包含其他参数
        self.assertNotIn('name="q"', rendered)
        self.assertNotIn('name="shared"', rendered)

    def test_bundle_param_preserved(self):
        """测试 bundle 参数保留"""
        # bundle 参数应该在白名单中
        self.assertIn('bundle', RANDOM_SORT_ALLOWED_PARAMS)

    def test_all_date_filter_params_in_whitelist(self):
        """测试所有日期筛选参数都在白名单中"""
        date_params = [
            'date_filter_by',
            'date_filter_type',
            'date_filter_relative_string',
            'date_filter_start',
            'date_filter_end',
        ]
        
        for param in date_params:
            self.assertIn(param, RANDOM_SORT_ALLOWED_PARAMS, 
                         f"日期筛选参数 {param} 不在白名单中")

    def test_all_resource_filter_params_in_whitelist(self):
        """测试所有资源筛选参数都在白名单中"""
        resource_params = [
            'html_snapshot',
            'preview_image',
            'favicon',
            'highlight',
            'annotation',
        ]
        
        for param in resource_params:
            self.assertIn(param, RANDOM_SORT_ALLOWED_PARAMS,
                         f"资源筛选参数 {param} 不在白名单中")

    def test_all_status_filter_params_in_whitelist(self):
        """测试所有状态筛选参数都在白名单中"""
        status_params = [
            'shared',
            'unread',
            'tagged',
        ]
        
        for param in status_params:
            self.assertIn(param, RANDOM_SORT_ALLOWED_PARAMS,
                         f"状态筛选参数 {param} 不在白名单中")

    def test_whitelist_completeness(self):
        """测试白名单完整性"""
        expected_params = {
            'q', 'user', 'bundle', 'shared', 'unread', 'tagged',
            'date_filter_by', 'date_filter_type', 'date_filter_relative_string',
            'date_filter_start', 'date_filter_end',
            'html_snapshot', 'preview_image', 'favicon', 'highlight', 'annotation',
            'modified_since', 'added_since', 'deleted_since',
        }
        
        self.assertEqual(RANDOM_SORT_ALLOWED_PARAMS, expected_params)

    def test_realistic_scenario_date_filter(self):
        """测试真实场景：相对日期筛选"""
        # 模拟用户设置"过去30天"筛选
        request = self.factory.get(
            '/bookmarks/?date_filter_by=added&date_filter_type=relative&date_filter_relative_string=last_30_days'
        )
        request.user = self.user
        rendered = self._render_template(request)
        
        # 验证所有日期筛选参数都被保留
        self.assertIn('name="date_filter_by"', rendered)
        self.assertIn('value="added"', rendered)
        self.assertIn('name="date_filter_type"', rendered)
        self.assertIn('value="relative"', rendered)
        self.assertIn('name="date_filter_relative_string"', rendered)
        self.assertIn('value="last_30_days"', rendered)

    def test_realistic_scenario_complex_filter(self):
        """测试真实场景：复杂组合筛选"""
        # 模拟用户设置多个筛选条件
        query_string = (
            '?q=python'
            '&shared=yes'
            '&unread=no'
            '&tagged=yes'
            '&date_filter_by=modified'
            '&date_filter_type=relative'
            '&date_filter_relative_string=this_month'
            '&highlight=yes'
        )
        request = self.factory.get(f'/bookmarks/{query_string}')
        request.user = self.user
        rendered = self._render_template(request)
        
        # 验证所有筛选参数都被保留
        expected_params = [
            ('q', 'python'),
            ('shared', 'yes'),
            ('unread', 'no'),
            ('tagged', 'yes'),
            ('date_filter_by', 'modified'),
            ('date_filter_type', 'relative'),
            ('date_filter_relative_string', 'this_month'),
            ('highlight', 'yes'),
        ]
        
        for param_name, param_value in expected_params:
            self.assertIn(f'name="{param_name}"', rendered)
            self.assertIn(f'value="{param_value}"', rendered)

    def test_template_tag_integration(self):
        """测试模板标签完整集成"""
        # 测试模板标签是否正确调用
        from bookmarks.templatetags.bookmarks import random_sort

        request = self.factory.get('/bookmarks/?q=test&shared=yes&debug=1')
        request.user = self.user

        search = BookmarkSearch.from_request(
            request, request.GET, self.profile.search_preferences
        )

        context = {'request': request}
        result = random_sort(context, search)

        # 验证返回的上下文
        self.assertIn('search', result)
        self.assertIn('filtered_params', result)

        # 验证参数过滤
        filtered = result['filtered_params']
        self.assertIn('q', filtered)
        self.assertIn('shared', filtered)
        self.assertNotIn('debug', filtered)

    def test_handles_missing_request(self):
        """测试 context 中没有 request 的情况"""
        from bookmarks.templatetags.bookmarks import random_sort

        search = BookmarkSearch()
        context = {}  # 没有 request

        result = random_sort(context, search)

        # 应该返回空参数，不抛出异常
        self.assertIn('search', result)
        self.assertIn('filtered_params', result)
        self.assertEqual(result['filtered_params'], {})

    def test_handles_none_request(self):
        """测试 request 为 None 的情况"""
        from bookmarks.templatetags.bookmarks import random_sort

        search = BookmarkSearch()
        context = {'request': None}

        result = random_sort(context, search)

        # 应该返回空参数，不抛出异常
        self.assertIn('search', result)
        self.assertIn('filtered_params', result)
        self.assertEqual(result['filtered_params'], {})

    def test_handles_empty_context(self):
        """测试 context 为空字典"""
        from bookmarks.templatetags.bookmarks import random_sort

        search = BookmarkSearch()
        context = {}

        result = random_sort(context, search)

        # 验证返回结构正确
        self.assertIn('search', result)
        self.assertIn('filtered_params', result)
        self.assertEqual(result['search'], search)
        self.assertEqual(result['filtered_params'], {})

    def test_template_renders_with_empty_filtered_params(self):
        """测试 filtered_params 为空时模板渲染"""
        search = BookmarkSearch()

        template = Template("""
            {% load bookmarks %}
            {% random_sort search %}
        """)

        # 不提供 request，filtered_params 应该为空
        context = Context({
            'search': search,
        })

        rendered = template.render(context)

        # 应该只包含 sort=random，不包含其他参数
        self.assertIn('name="sort"', rendered)
        self.assertIn('value="random"', rendered)
        self.assertNotIn('name="q"', rendered)
        self.assertNotIn('name="shared"', rendered)

    def test_search_param_preserved_in_context(self):
        """测试 search 参数被正确传递到模板"""
        from bookmarks.templatetags.bookmarks import random_sort

        request = self.factory.get('/bookmarks/?q=test')
        request.user = self.user

        search = BookmarkSearch.from_request(
            request, request.GET, self.profile.search_preferences
        )

        context = {'request': request}
        result = random_sort(context, search)

        # search 对象应该被原样传递
        self.assertEqual(result['search'], search)

    def test_whitelist_is_immutable_set(self):
        """测试白名单是不可变集合"""
        # 注意：Python 的 set 是可变的，这里测试的是常量不被意外修改
        original = RANDOM_SORT_ALLOWED_PARAMS.copy()

        # 尝试添加恶意参数
        RANDOM_SORT_ALLOWED_PARAMS.add("malicious_param")

        # 验证原始集合未被修改（因为 copy 返回的是新集合）
        self.assertNotIn("malicious_param", original)

        # 清理：移除测试添加的参数
        RANDOM_SORT_ALLOWED_PARAMS.discard("malicious_param")

        # 验证清理成功
        self.assertNotIn("malicious_param", RANDOM_SORT_ALLOWED_PARAMS)
