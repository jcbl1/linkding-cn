import json
import logging
import os
import re
import subprocess
import tempfile
import time

from site_adapters.services.execution_log import log_execution

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


# --- CJK inline-space stripping ---
# Defuddle's standardizeElements step inserts space text nodes between
# consecutive inline elements to fix missing word-separators in Latin text.
# This is wrong for CJK text where no inter-word spaces are needed.
#
# NOTE: This is a workaround for defuddle's lack of CJK-awareness.
# If a future version of defuddle fixes this upstream, this post-processing
# step can be removed.
#
# Trade-off: authors who *intentionally* place spaces between CJK characters
# (e.g. in linguistic analysis or children's reading materials) will have
# those spaces collapsed — an acceptable trade-off given how rarely this
# occurs in practice.

def _normalize_cjk_spacing(content: str) -> str:
    """Normalize CJK text spacing in defuddle output.

    defuddle's standardizeElements step inserts space text nodes between
    consecutive inline elements to fix Latin word separation.  For CJK text
    this produces visible gaps between characters that should be contiguous.

    This function post-processes defuddle output to:
    1. Remove spurious spaces between inline elements when both adjacent text
       segments are CJK (ideographs or CJK/fullwidth punctuation).
    2. Insert a single space at CJK ↔ half-width (A-Za-z0-9) boundaries per
       the pangu.js convention (W3C CLREQ §3.1.3).
    3. Preserve existing spacing in all other cases.

    References:
    - W3C CLREQ: https://www.w3.org/TR/clreq/
    - pangu.js:   https://github.com/vinta/pangu.js
    """
    # CJK ideographs + CJK punctuation + fullwidth forms (excluding fullwidth
    # ASCII letters/digits which are treated as half-width).
    cjk_re = re.compile(
        r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"  # ideographs
        r"\u3000-\u303f"                                    # CJK punct
        r"\uff01-\uff0f\uff1a-\uff20"                     # fullwidth punct
        r"\uff3b-\uff40\uff5b-\uffef]"                    # fullwidth forms
    )
    hw_re = re.compile(r"[A-Za-z0-9]")
    inline_tag_re = re.compile(r"(</\w+>)\s*(<\w[^>]*>)")
    block_re = re.compile(
        r"<pre[^>]*>.*?</pre>"
        r"|<code[^>]*>.*?</code>"
        r"|<script[^>]*>.*?</script>"
        r"|<style[^>]*>.*?</style>",
        re.DOTALL,
    )
    sentinel = "\x00ld-cjk-fix-block-"

    def classify(c):
        if c is None:
            return None
        if cjk_re.match(c):
            return "cjk"
        if hw_re.match(c):
            return "hw"
        return "other"

    def last_text_char_before(html, pos):
        i = pos - 1
        while i >= 0:
            c = html[i]
            if c == ">":
                break
            if c == "<":
                return None
            if not c.isspace():
                return c
            i -= 1
        return None

    def first_text_char_after(html, pos):
        i = pos
        while i < len(html):
            c = html[i]
            if c == "<":
                end = html.find(">", i)
                if end == -1:
                    return None
                i = end + 1
                continue
            if not c.isspace():
                return c
            i += 1
        return None

    # 1. Protect pre/code/script/style blocks
    blocks: list[str] = []

    def save_block(m: re.Match) -> str:
        blocks.append(m.group(0))
        return f"{sentinel}{len(blocks) - 1}\x00"

    protected = block_re.sub(save_block, content)

    # 2. Adjust whitespace between inline element boundaries
    def replace(m: re.Match) -> str:
        closing, opening = m.group(1), m.group(2)
        pos = m.start()
        before = last_text_char_before(protected, pos)
        after_pos = m.start() + len(m.group(0))
        after = first_text_char_after(protected, after_pos)
        b, a = classify(before), classify(after)
        # CJK ↔ CJK: remove space (defuddle artifact)
        if b == "cjk" and a == "cjk":
            return closing + opening
        # CJK ↔ half-width: ensure exactly one space (pangu.js convention)
        if (b == "cjk" and a == "hw") or (b == "hw" and a == "cjk"):
            return closing + " " + opening
        return m.group(0)

    protected = inline_tag_re.sub(replace, protected)

    # 3. Restore protected blocks
    for i, block in enumerate(blocks):
        protected = protected.replace(f"{sentinel}{i}\x00", block)

    return protected


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
    """通过 site_adapters/services/engine/scripts/defuddle_parse.js wrapper 脚本调用 defuddle。

    所有 subprocess 调用、错误处理和输出解析均在此函数内完成，
    调用方只需处理 DefuddleError。
    """
    import site_adapters.services as _sa_services
    script_path = os.path.join(os.path.dirname(_sa_services.__file__), "engine", "scripts", "defuddle_parse.js")
    if not os.path.exists(script_path):
        raise DefuddleError(f"defuddle wrapper script not found at {script_path}")

    if options:
        input_data["options"] = options

    env = os.environ.copy()
    env["LANG"] = "en_US.UTF-8"

    url = input_data.get("url", "")
    cmd = ["node", script_path]
    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            input=json.dumps(input_data).encode("utf-8"),
            capture_output=True,
            timeout=timeout,
            env=env,
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        log_execution(
            url=url, domain_key="", step="reader",
            cmd=cmd, returncode=result.returncode,
            stdout=result.stdout.decode("utf-8", errors="replace")[:500],
            stderr=result.stderr.decode("utf-8", errors="replace")[:500],
            duration_ms=duration_ms,
        )
    except subprocess.TimeoutExpired as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        log_execution(url=url, domain_key="", step="reader", cmd=cmd,
                      returncode=-1, stderr="Timeout", duration_ms=duration_ms)
        raise DefuddleError(f"defuddle timed out after {timeout}s") from e

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise DefuddleError(f"defuddle wrapper exited with code {result.returncode}: {stderr}")

    output = result.stdout.decode("utf-8").strip()
    if not output:
        raise DefuddleError("defuddle wrapper produced no output")

    try:
        parsed = _normalize_result(json.loads(output))
    except json.JSONDecodeError as e:
        raise DefuddleError(f"Failed to parse defuddle output: {e}") from e

    # Fix spurious spaces between inline elements for CJK text
    if parsed.get("content"):
        parsed["content"] = _normalize_cjk_spacing(parsed["content"])

    return parsed


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
