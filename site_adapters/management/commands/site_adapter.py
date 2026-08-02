import json
import os
import shutil
import tempfile

from django.conf import settings
from django.core.management.base import BaseCommand

from site_adapters.services.config.validator import classify_field
from site_adapters.services.auth.cookies import (
    get_cookie_for_domain,
    has_cookie_for_domain,
    load_cookie_file,
    verify_and_refresh,
)
from site_adapters.services.config import parse_jsonc
from site_adapters.services.config.loader import show_config
from site_adapters.services.config.validator import validate_config
from site_adapters.services.config.resolver import (
    get_metadata_config,
    get_reader_config,
    get_snapshot_config,
)
from site_adapters.services.subscriptions import (
    fetch_subscription,
)
from bookmarks.services.website_loader import load_website_metadata


class Command(BaseCommand):
    help = "Manage and test site adapter configuration"

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="cmd", required=True)

        validate = sub.add_parser("validate")
        validate.add_argument("base_dir", nargs="?", default=None)
        validate.add_argument("--file", default="")

        show = sub.add_parser("show-config")
        show.add_argument("url")
        show.add_argument("--dir", default=None)

        metadata = sub.add_parser("metadata")
        metadata.add_argument("url")

        cookie = sub.add_parser("cookie")
        cookie.add_argument("url")
        cookie.add_argument("--section", choices=("metadata", "snapshot"), default="metadata")

        pipeline = sub.add_parser("pipeline")
        pipeline.add_argument("url")
        pipeline.add_argument("--output", "-o", default=None)
        pipeline.add_argument("--skip-snapshot", action="store_true")

        subscription = sub.add_parser("validate-subscription")
        subscription.add_argument("source")

        from_us = sub.add_parser("from-userscript")
        from_us.add_argument("source")
    def handle(self, *args, **opts):
        return getattr(self, f"handle_{opts['cmd'].replace('-', '_')}")(opts)

    def handle_validate(self, opts):
        base_dir = opts["base_dir"] or settings.LD_SITE_ADAPTERS_DIR
        issues = validate_config(base_dir, domain_filename=opts["file"])
        if not issues:
            self.stdout.write(self.style.SUCCESS("site adapters ok"))
            return
        for issue in issues:
            style = self.style.ERROR if issue.startswith("ERROR") else self.style.WARNING
            self.stdout.write(style(issue))

    def handle_show_config(self, opts):
        result = show_config(opts["url"], opts["dir"] or settings.LD_SITE_ADAPTERS_DIR)
        self.stdout.write(json.dumps(result, indent=2, ensure_ascii=False))

    def handle_metadata(self, opts):
        config = get_metadata_config(opts["url"])
        metadata = load_website_metadata(opts["url"], ignore_cache=True)
        self.stdout.write(json.dumps({
            "config": config,
            "metadata": metadata.to_dict(),
        }, indent=2, ensure_ascii=False, default=str))

    def handle_cookie(self, opts):
        config = get_metadata_config(opts["url"]) if opts["section"] == "metadata" else get_snapshot_config(opts["url"])
        if not config:
            self.stdout.write(self.style.ERROR("no matching domain config"))
            return
        domain_key = config.get("_domain_key", "")
        cookie_config = config.get("cookie", {})
        cookie_file = cookie_config.get("file", "")
        before = load_cookie_file(cookie_file) if cookie_file else get_cookie_for_domain(domain_key)
        after = before
        if cookie_config:
            after = verify_and_refresh(
                cookie_config,
                opts["url"],
                domain_key,
                {"url": opts["url"], "status": 0, "title": "", "body_preview": ""},
            )
        self.stdout.write(json.dumps({
            "domain": domain_key,
            "cookie_file": cookie_file,
            "has_cookie": bool((load_cookie_file(cookie_file) if cookie_file else None) or has_cookie_for_domain(domain_key)),
            "refreshed": bool(after and after != before),
        }, indent=2, ensure_ascii=False))

    def handle_pipeline(self, opts):
        from bookmarks.services import reader_processor
        from bookmarks.services.snapshot_processor import create_snapshot

        url = opts["url"]
        result = {
            "metadata_config": get_metadata_config(url),
            "snapshot_config": get_snapshot_config(url),
            "reader_config": get_reader_config(url),
            "metadata": load_website_metadata(url, ignore_cache=True).to_dict(),
        }
        tmp_dir = None
        snapshot_path = opts["output"]
        try:
            if not opts["skip_snapshot"]:
                if not snapshot_path:
                    tmp_dir = tempfile.mkdtemp()
                    snapshot_path = os.path.join(tmp_dir, "snapshot.html")
                create_snapshot(url, snapshot_path)
                result["snapshot"] = {"path": snapshot_path, "size": os.path.getsize(snapshot_path)}
                with open(snapshot_path, encoding="utf-8") as f:
                    html = f.read()
                result["reader"] = reader_processor.parse_html(html, url=url)
            else:
                result["reader"] = reader_processor.parse_url(url)
            self.stdout.write(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        finally:
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    def handle_validate_subscription(self, opts):
        try:
            data, root = self._load_subscription(opts["source"])
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"load failed: {exc}"))
            return
        issues = self._validate_subscription_data(data, root)
        if not issues:
            self.stdout.write(self.style.SUCCESS(f"subscription ok, {len(data.get('domains', {}))} domains"))
            return
        for issue in issues:
            style = self.style.ERROR if issue.startswith("ERROR") else self.style.WARNING
            self.stdout.write(style(issue))

    def _load_subscription(self, source: str):
        if os.path.isdir(source):
            return self._load_subscription_dir(source)
        if source.startswith("https://"):
            root = fetch_subscription(source, force=True)
            if not root:
                raise ValueError("subscription fetch failed")
            return self._load_subscription_dir(root)
        if source.startswith("http://"):
            raise ValueError("subscription url must use HTTPS")
        with open(source, encoding="utf-8") as f:
            data = parse_jsonc(f.read())
        if not isinstance(data, dict):
            raise ValueError("subscription top-level must be an object")
        if not isinstance(data.get("domains"), dict):
            data = {
                **data,
                "domains": {
                    key: value for key, value in data.items()
                    if key not in ("*", "scripts", "domains") and not key.startswith("_")
                },
            }
        return data, os.path.dirname(os.path.abspath(source))

    def _load_subscription_dir(self, path: str):
        root = os.path.abspath(path)
        # 尝试读取单文件格式
        sub_file = os.path.join(root, "subscription.jsonc")
        if os.path.exists(sub_file):
            with open(sub_file, encoding="utf-8") as f:
                data = parse_jsonc(f.read())
            if isinstance(data, dict) and isinstance(data.get("domains"), dict):
                # 记录 scripts 目录中的文件
                scripts_dir = os.path.join(root, "scripts")
                if os.path.isdir(scripts_dir):
                    data["_available_scripts"] = os.listdir(scripts_dir)
                return data, root
        # 回退：尝试目录格式（兼容旧格式）
        data = {"domains": {}}
        global_path = os.path.join(root, "global.jsonc")
        if os.path.exists(global_path):
            with open(global_path, encoding="utf-8") as f:
                global_data = parse_jsonc(f.read())
            data["*"] = global_data.get("*", {}) if isinstance(global_data, dict) else {}
        domains_dir = os.path.join(root, "domains")
        if os.path.isdir(domains_dir):
            for fname in sorted(os.listdir(domains_dir)):
                if not (fname.endswith(".jsonc") or fname.endswith(".json")):
                    continue
                domain_key = fname.rsplit(".", 1)[0]
                fpath = os.path.join(domains_dir, fname)
                with open(fpath, encoding="utf-8") as f:
                    data["domains"][domain_key] = parse_jsonc(f.read())
        return data, root

    def _validate_subscription_data(self, data: dict, root: str):
        issues = []
        domains = data.get("domains", {})
        if not isinstance(domains, dict):
            return ["ERROR: domains must be an object"]
        for domain_key, value in domains.items():
            if "/" in domain_key or "\\" in domain_key or ".." in domain_key:
                issues.append(f"ERROR: invalid domain key: {domain_key}")
                continue
            if isinstance(value, str):
                continue
            if not isinstance(value, dict):
                issues.append(f"ERROR: {domain_key} must be an object")
                continue
            if value.get("type") == "alias":
                if not value.get("target"):
                    issues.append(f"ERROR: {domain_key} alias missing target")
                continue
            for section in ("metadata", "snapshot", "reader"):
                sec = value.get(section, {})
                if not sec:
                    continue
                if not isinstance(sec, dict):
                    issues.append(f"ERROR: {domain_key}.{section} must be an object")
                    continue
                for field, field_value in sec.items():
                    if classify_field(section, field) == "unknown":
                        issues.append(f"WARN: {domain_key}.{section}.{field} is unknown")
                    if field == "script" or field.endswith("_script"):
                        self._check_subscription_script(issues, root, domain_key, section, field, field_value)
        return issues

    def _check_subscription_script(self, issues, root, domain_key, section, field, value):
        if not value:
            return
        if not isinstance(value, str):
            issues.append(f"ERROR: {domain_key}.{section}.{field} must be a string path or URL")
            return
        # URL 引用：只检查格式
        if value.startswith("http://") or value.startswith("https://"):
            return
        # 相对路径：相对于根目录解析
        if value.startswith("./") or value.startswith("../"):
            script_path = os.path.normpath(os.path.join(root, value))
        else:
            script_path = os.path.normpath(os.path.join(root, "scripts", value))
        if not os.path.exists(script_path):
            issues.append(f"WARN: {domain_key}.{section}.{field} script not found locally: {value}")


    def handle_from_userscript(self, opts):
        """Generate a site adapter config from a Tampermonkey userscript."""
        import re

        source = opts["source"]

        if os.path.isfile(source):
            with open(source, encoding="utf-8") as f:
                content = f.read()
        else:
            self.stdout.write(self.style.ERROR(f"File not found: {source}"))
            return

        # Parse UserScript metadata block
        block_match = re.search(
            r"//\s*==UserScript==\s*\n(.*?)//\s*==/UserScript==",
            content, re.DOTALL,
        )
        if not block_match:
            self.stdout.write(self.style.ERROR("No UserScript metadata block found"))
            return

        block = block_match.group(1)
        matches = re.findall(r"//\s*@match\s+(.+)", block)
        grants = re.findall(r"//\s*@grant\s+(.+)", block)
        name_match = re.search(r"//\s*@name\s+(.+)", block)
        name = name_match.group(1).strip() if name_match else "unknown"

        if not matches:
            self.stdout.write(self.style.ERROR("No @match found in userscript"))
            return

        # Convert @match patterns to domain keys
        domains = []
        for pattern in matches:
            pattern = pattern.strip()
            # *://*.example.com/* -> *.example.com
            m = re.match(r"\*://(?:\*\.)?([^/]+?)(?:/.*)?$", pattern)
            if m:
                domains.append(m.group(1))
            else:
                # https://example.com/path -> example.com
                m2 = re.match(r"https?://([^/]+)", pattern)
                if m2:
                    domains.append(m2.group(1))

        if not domains:
            self.stdout.write(self.style.ERROR("Could not extract domains from @match"))
            return

        # Determine if script needs GM_xmlhttpRequest (suggests auth needed)
        needs_auth = "GM_xmlhttpRequest" in grants or "GM_xmlhttpRequest" in content

        # Generate config
        for domain in domains:
            config = {"metadata": {}, "snapshot": {}, "reader": {}}
            if needs_auth:
                config["auth"] = {
                    "cookie": {"type": "login"},
                }

            self.stdout.write(self.style.SUCCESS(f"\nGenerated config for {domain} (from {name}):"))
            self.stdout.write(json.dumps({domain: config}, indent=2, ensure_ascii=False))

        # Check for DOM selectors in the script
        selector_patterns = re.findall(
            r"""(?:querySelector|querySelectorAll|getElementById|getElementsByClassName)\s*\(\s*['"]([^'"]+)['"]""",
            content,
        )
        if selector_patterns:
            self.stdout.write(f"\nDetected selectors in script (may help configure metadata/snapshot):")
            for sel in set(selector_patterns[:10]):
                self.stdout.write(f"  - {sel}")
