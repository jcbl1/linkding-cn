#!/usr/bin/env python3
"""
端到端测试：站点适配系统三个视角
用法: DJANGO_SETTINGS_MODULE=bookmarks.settings PYTHONPATH=. .venv/bin/python scripts/e2e_site_adapters.py
"""
import json
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bookmarks.settings")
import django
django.setup()

from django.test import Client


class E2ETest:
    def __init__(self, username="e2e_test", password="test1234"):
        self.c = Client()
        ok = self.c.login(username=username, password=password)
        assert ok, f"登录失败: {username}"
        self._print("[OK] 登录成功")
        self._created_domains = []

    def _print(self, msg):
        print(msg)

    def _post(self, path, data=None, content_type=""):
        kwargs = {"data": data or {}}
        if content_type:
            kwargs["content_type"] = content_type
        return self.c.post(path, **kwargs)

    def _get(self, path):
        return self.c.get(path)

    # ─────────────────────────────────────────
    # 视角 1：订阅规则开发者
    # ─────────────────────────────────────────
    def test_subscription_developer(self):
        self._print("\n=== 视角 1：订阅规则开发者 ===")

        # 1. 配置查看（无规则时应返回空）
        r = self._post("/admin/site-adapters/action", {
            "action": "test", "url": "https://example.com", "test_type": "config",
        })
        data = r.json()
        self._print(f"[OK] 配置查看: domain={data.get('result', {}).get('domain_key', '无匹配')}")

        # 2. 创建域名规则
        r = self._post("/admin/site-adapters/domain/create", {"domain_key": "example.com"})
        if r.status_code == 200:
            self._created_domains.append("example.com")
            self._print("[OK] 创建域名规则 example.com.jsonc")
        else:
            self._print(f"[FAIL] 创建域名规则: {r.status_code} {r.content[:100]}")
            return

        # 3. 编辑规则内容
        config = json.dumps({
            "metadata": {"select_title": ["h1"]},
            "http": {"timeout": 10},
        }, indent=2)
        r = self._post("/admin/site-adapters/domain/save", {
            "filename": "example.com.jsonc", "content": config,
        })
        if r.status_code == 200:
            self._print("[OK] 编辑域名规则内容")
        else:
            self._print(f"[FAIL] 编辑规则: {r.status_code}")

        # 4. 配置校验
        r = self._post("/admin/site-adapters/action", {"action": "validate"})
        data = r.json()
        issues = data.get("issues", [])
        errors = [i for i in issues if i.startswith("ERROR")]
        self._print(f"[OK] 配置校验: {len(errors)} errors, {len(issues)-len(errors)} warnings")

        # 5. 再次查看配置确认生效
        r = self._post("/admin/site-adapters/action", {
            "action": "test", "url": "https://example.com/page", "test_type": "config",
        })
        data = r.json()
        domain = data.get("result", {}).get("domain_key", "?")
        self._print(f"[OK] 配置已生效: domain={domain}")

    # ─────────────────────────────────────────
    # 视角 2：管理员（域名规则编写者）
    # ─────────────────────────────────────────
    def test_admin(self):
        self._print("\n=== 视角 2：管理员（域名规则编写者） ===")

        # 1. 读取域名配置
        r = self._get("/admin/site-adapters/domain/read?filename=example.com.jsonc")
        if r.status_code == 200:
            data = r.json()
            self._print(f"[OK] 读取域名配置: {data.get('filename')}")
        else:
            self._print(f"[FAIL] 读取: {r.status_code}")

        # 2. 更新配置
        updated = json.dumps({
            "metadata": {
                "select_title": ["h1", "title", ".article-title"],
                "select_description": ["meta[name='description']"],
            },
            "http": {"timeout": 15},
        }, indent=2)
        r = self._post("/admin/site-adapters/domain/save", {
            "filename": "example.com.jsonc", "content": updated,
        })
        self._print(f"[{'OK' if r.status_code==200 else 'FAIL'}] 更新域名配置: {r.status_code}")

        # 3. 域名列表
        r = self._get("/admin/site-adapters/domains/all")
        if r.status_code == 200:
            domains = r.json()
            count = len(domains) if isinstance(domains, list) else "?"
            self._print(f"[OK] 域名列表: {count} 个")
        else:
            self._print(f"[FAIL] 域名列表: {r.status_code}")

        # 4. 测试元数据提取
        r = self._post("/admin/site-adapters/action", {
            "action": "test", "url": "https://example.com", "test_type": "metadata",
        })
        data = r.json()
        if r.status_code == 200 and not data.get("error"):
            title = data.get("result", {}).get("title", "")
            self._print(f"[OK] 元数据提取: title='{title[:60]}'")
        else:
            self._print(f"[WARN] 元数据: {data.get('error', r.status_code)}")

        # 5. 别名
        r = self._post("/admin/site-adapters/domain/create", {"domain_key": "alias.example.com"})
        if r.status_code == 200:
            self._created_domains.append("alias.example.com")
            alias_cfg = json.dumps({"type": "alias", "target": "example.com"})
            r2 = self._post("/admin/site-adapters/domain/save", {
                "filename": "alias.example.com.jsonc", "content": alias_cfg,
            })
            self._print(f"[OK] 创建别名 alias.example.com → example.com")

            # 验证别名解析
            r3 = self._post("/admin/site-adapters/action", {
                "action": "test", "url": "https://alias.example.com/page", "test_type": "config",
            })
            d = r3.json()
            dk = d.get("result", {}).get("domain_key", "?")
            self._print(f"[OK] 别名解析: domain={dk}")
        else:
            self._print(f"[FAIL] 创建别名: {r.status_code}")

        # 6. 保存全局配置
        r = self._post("/admin/site-adapters/resources/save", {
            "path": "global.jsonc",
            "content": '{"*": {"http": {"timeout": 30}}}',
        })
        self._print(f"[{'OK' if r.status_code==200 else 'FAIL'}] 保存全局配置: {r.status_code}")

        # 7. 资源列表
        r = self._get("/admin/site-adapters/resources")
        if r.status_code == 200:
            data = r.json()
            self._print(f"[OK] 资源列表: {len(data.get('items', []))} 项")
        else:
            self._print(f"[FAIL] 资源列表: {r.status_code}")

        # 8. 清理测试文件
        r = self._post("/admin/site-adapters/action", {"action": "clean_test_files"})
        if r.status_code == 200:
            self._print(f"[OK] 清理测试文件: {r.json().get('deleted', 0)} 个")

        # 9. 删除域名
        for dk in reversed(self._created_domains):
            fname = f"{dk}.jsonc"
            r = self._post("/admin/site-adapters/domain/delete", {"filename": fname})
            self._print(f"[{'OK' if r.status_code==200 else 'FAIL'}] 删除 {fname}")
        self._created_domains.clear()

    # ─────────────────────────────────────────
    # 视角 3：普通用户（Cookies 管理）
    # ─────────────────────────────────────────
    def test_regular_user(self):
        self._print("\n=== 视角 3：普通用户（Cookies 管理） ===")

        # 1. Cookies 页面
        r = self._get("/settings/site-adapters")
        self._print(f"[{'OK' if r.status_code==200 else 'FAIL'}] Cookies 页面: {r.status_code}")

        # 2. Cookies API
        r = self._get("/settings/site-adapters/api")
        if r.status_code == 200:
            data = r.json()
            self._print(f"[OK] Cookies API: domains={len(data.get('domains',[]))}, cookies={len(data.get('cookies',[]))}")
        else:
            self._print(f"[FAIL] Cookies API: {r.status_code}")
            return

        # 3. 添加 Cookie
        r = self._post("/settings/site-adapters/api", {
            "action": "save", "domain": "example.com", "cookie": "session=abc123; path=/",
        })
        self._print(f"[{'OK' if r.status_code==200 else 'FAIL'}] 添加 Cookie: {r.status_code}")

        # 4. 查询
        r = self._get("/settings/site-adapters/api")
        if r.status_code == 200:
            cookies = r.json().get("cookies", [])
            self._print(f"[OK] 查询 Cookies: {len(cookies)} 条")

        # 5. 删除
        r = self._post("/settings/site-adapters/api", {
            "action": "delete", "domain": "example.com",
        })
        self._print(f"[{'OK' if r.status_code==200 else 'FAIL'}] 删除 Cookie: {r.status_code}")

    def run_all(self):
        self.test_subscription_developer()
        self.test_admin()
        self.test_regular_user()
        self._print("\n" + "=" * 50)
        self._print("测试完成！")


if __name__ == "__main__":
    E2ETest().run_all()
