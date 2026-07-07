import json
import logging
import os
import subprocess
import tempfile

logger = logging.getLogger(__name__)


class DefuddleError(Exception):
    pass


def _normalize_result(data: dict) -> dict:
    """统一 defuddle 输出格式。"""
    return {
        "title": data.get("title", ""),
        "content": data.get("content", ""),
        "description": data.get("description", ""),
        "author": data.get("author", ""),
        "site": data.get("site", ""),
        "wordCount": data.get("wordCount", 0),
    }


def _inject_base_tag(html_content: str, url: str) -> str:
    """注入 <base> 标签以便 defuddle 解析相对链接。"""
    if url and "<base " not in html_content:
        base_tag = f'<base href="{url}">'
        if "<head>" in html_content:
            html_content = html_content.replace("<head>", f"<head>{base_tag}", 1)
        elif "<head " in html_content:
            html_content = html_content.replace("<head", f"{base_tag}<head", 1)
        else:
            html_content = f"<head>{base_tag}</head>{html_content}"
    return html_content


def _run_defuddle(input_data: dict, options: dict = None, timeout: int = 60) -> dict:
    """通过 vendor/defuddle_parse.js wrapper 脚本调用 defuddle。

    所有 subprocess 调用、错误处理和输出解析均在此函数内完成，
    调用方只需处理 DefuddleError。
    """
    script_path = os.path.join(os.path.dirname(__file__), "vendor", "defuddle_parse.js")
    if not os.path.exists(script_path):
        raise DefuddleError(f"defuddle wrapper script not found at {script_path}")

    if options:
        input_data["options"] = options

    env = os.environ.copy()
    env["LANG"] = "en_US.UTF-8"

    try:
        result = subprocess.run(
            ["node", script_path],
            input=json.dumps(input_data).encode("utf-8"),
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as e:
        raise DefuddleError(f"defuddle timed out after {timeout}s") from e

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise DefuddleError(f"defuddle wrapper exited with code {result.returncode}: {stderr}")

    output = result.stdout.decode("utf-8").strip()
    if not output:
        raise DefuddleError("defuddle wrapper produced no output")

    try:
        return _normalize_result(json.loads(output))
    except json.JSONDecodeError as e:
        raise DefuddleError(f"Failed to parse defuddle output: {e}") from e


def parse_html(html_content: str, url: str = "") -> dict:
    """
    Parse raw HTML with defuddle and return clean article content.

    Returns dict with keys: title, content, description, author, site, wordCount
    Raises DefuddleError on failure.
    """
    html_content = _inject_base_tag(html_content, url)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(html_content)
        tmp_path = tmp.name

    try:
        return _run_defuddle({"htmlPath": tmp_path, "url": url}, timeout=30)
    finally:
        os.unlink(tmp_path)


def parse_url(url: str) -> dict:
    """
    Parse a URL directly with defuddle (defuddle handles fetching).
    Returns dict with keys: title, content, description, author, site, wordCount
    Raises DefuddleError on failure.
    """
    return _run_defuddle({"url": url})
