"""
Snapshot hook scripts for site-adapters.

Each script defines one or more hook functions. The function name is the hook
value from the adapter configuration. Define only the hooks you need.

Hook execution order:
    before scripts -> [replace script OR SingleFile engine] -> after scripts

--------------------------------------------------------------------------------
Config keys available in every hook
--------------------------------------------------------------------------------

  General:
    headers            dict[str,str]  HTTP request headers
    timeout            int|None       Timeout in seconds
    proxy              str|None       HTTP proxy URL
    request_url        str|None       Resolved request URL
    rewrite_url        str|None       Resolved rewrite URL
    auth               dict           Merged auth config
    cookie             dict           Cookie configuration
    user_cookie        str|None       Best available cookie string

  Snapshot-specific:
    keep_elements      list[str]      CSS selectors to keep
    remove_elements    list[str]      CSS selectors to remove
    process_lazy_images bool|list[str]
    remove_classes     dict           CSS classes to remove
    set_styles         dict           Inline styles to set
    singlefile_args    dict           SingleFile CLI args
    toggles            dict           User-toggleable controls
    scripts            list[dict]     Script hooks configuration

File handling:
    replace scripts: write HTML to output_path
    after scripts:   read and modify output_path in-place
    Framework creates and cleans up temp files automatically.
"""


def before(url: str, config: dict) -> str | None:
    """
    Hook: before

    Executes before SingleFile. Return HTML to feed to SingleFile instead of
    re-fetching the URL; return None to let SingleFile fetch the URL normally.

    Example - expand collapsed content in a browser:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(url, wait_until="networkidle")
            for btn in page.query_selector_all(".read-more-btn"):
                try:
                    btn.click()
                    page.wait_for_timeout(300)
                except Exception:
                    pass
            html = page.content()
            browser.close()
        return html
    """
    return None


def replace(url: str, config: dict, output_path: str) -> None:
    """
    Hook: replace

    Completely replaces the SingleFile engine. Write a complete,
    self-contained HTML file to `output_path`.

    Example - capture with Playwright:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(url, wait_until="networkidle")
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1000)
            html = page.content()
            browser.close()

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)

    Example - custom work then delegate to SingleFile:
        from site_adapters.services.engine import create_snapshot
        # ... do custom preprocessing ...
        create_snapshot(url, output_path, config)
    """
    pass


def after(output_path: str, config: dict) -> None:
    """
    Hook: after

    Executes after the snapshot HTML file is written. Modify the file
    at `output_path` in-place.

    Example - inject dark mode CSS:
        with open(output_path, "r+", encoding="utf-8") as f:
            html = f.read()
            html = html.replace("</head>",
                "<style>body{background:#111;color:#eee}</style></head>")
            f.seek(0)
            f.write(html)
            f.truncate()

    Example - rewrite CDN image URLs:
        import re
        with open(output_path, "r+", encoding="utf-8") as f:
            html = f.read()
            html = re.sub(r"https://cdn\\.example\\.com/",
                         "https://example.com/", html)
            f.seek(0)
            f.write(html)
            f.truncate()
    """
    pass
