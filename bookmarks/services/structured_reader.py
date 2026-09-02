import json
import re
from contextlib import suppress
from html import escape, unescape

from elementpath import Selector as XPathSelector
from jsonpath_rfc9535 import find as jsonpath_find
from lxml import etree
from lxml import html as lxml_html

_TITLE_KEYS = ("title", "headline", "name")
_CONTENT_KEYS = (
    "content",
    "body",
    "text",
    "description",
    "summary",
    "selftext",
)
_XML_WRAPPER_TAGS = {
    "feed",
    "rss",
    "channel",
    "root",
    "data",
    "response",
    "result",
    "results",
}

_DEFAULT_NAMESPACE_ALIASES = {
    "http://www.w3.org/2005/Atom": "atom",
    "http://purl.org/rss/1.0/": "rss",
}


def _text_to_html(value: str) -> str:
    """Convert plain text to simple HTML paragraphs."""
    value = value.strip()
    if not value:
        return ""
    paragraphs = [
        part.strip()
        for part in re.split(r"\r?\n\s*\r?\n", value)
        if part.strip()
    ]
    if not paragraphs:
        return ""
    if len(paragraphs) == 1:
        return f"<p>{escape(paragraphs[0])}</p>"
    return "".join(f"<p>{escape(part)}</p>" for part in paragraphs)


def _is_html_fragment(value: str) -> bool:
    return bool(re.search(r"</?[a-zA-Z][^>]*>", value))


def _xml_local_name(tag) -> str:
    if isinstance(tag, str) and tag.startswith("{"):
        return tag.split("}", 1)[1]
    return str(tag or "")


def _xml_text(element) -> str:
    return "".join(element.itertext()).strip()


def _render_xml_node(element, level: int) -> str:
    tag = _xml_local_name(element.tag)
    children = list(element)
    text = (element.text or "").strip()

    if tag in _XML_WRAPPER_TAGS:
        return "".join(_render_xml_node(child, level) for child in children)

    if tag in ("entry", "item"):
        parts = ["<article>"]
        for child in children:
            child_html = _render_xml_node(child, level + 1)
            if child_html:
                parts.append(child_html)
        if text:
            parts.append(_text_to_html(text))
        parts.append("</article>")
        return "".join(parts)

    if tag == "title":
        title = _xml_text(element)
        if not title:
            return ""
        heading = min(max(level + 1, 1), 6)
        return f"<h{heading}>{escape(title)}</h{heading}>"

    if tag in ("content", "description", "summary", "body", "text"):
        content_type = element.get("type") or ""
        value = _xml_text(element)
        if content_type == "html":
            return f'<div class="{tag}">{unescape(value)}</div>'
        if _is_html_fragment(value):
            return f'<div class="{tag}">{value}</div>'
        if children:
            inner = "".join(_render_xml_node(child, level + 1) for child in children)
            if inner:
                return f'<div class="{tag}">{inner}</div>'
        return f'<div class="{tag}">{_text_to_html(value)}</div>'

    if tag == "link":
        return ""

    if tag in ("image", "thumbnail", "enclosure"):
        src = element.get("url") or element.get("href") or element.get("src")
        if src:
            return f'<p><img src="{escape(src)}" alt=""></p>'
        return ""

    if tag in ("author", "creator"):
        if level == 0:
            return ""
        inner = "".join(_render_xml_node(child, level + 1) for child in children)
        return f'<p class="author">{inner or escape(text)}</p>'

    if tag in ("updated", "published", "pubdate", "date"):
        return f'<p class="date">{escape(text)}</p>' if text else ""

    if tag in ("id", "guid", "uri", "generator", "icon", "logo"):
        return ""

    if tag == "category":
        return f'<p class="category">{escape(text)}</p>' if text else ""

    if children:
        inner = "".join(_render_xml_node(child, level + 1) for child in children)
        if inner:
            return f'<section class="xml-{tag}">{inner}</section>'

    if text:
        return _text_to_html(text)

    return ""


def _xml_title(root) -> str:
    for element in root.iter():
        if _xml_local_name(element.tag) == "title":
            title = _xml_text(element)
            if title:
                return title
    return ""


def xml_to_html(content: str) -> str:
    """Convert an XML snapshot to a semantic HTML document for defuddle."""
    if not content or not content.strip():
        return content or ""
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    root = etree.fromstring(content.encode("utf-8"), parser=parser)
    body = _render_xml_node(root, 0)
    title = _xml_title(root)
    head = f"<title>{escape(title)}</title>" if title else ""
    return f"<!DOCTYPE html><html><head>{head}</head><body>{body}</body></html>"


def _wrap_selected_html(body: str, title: str = "") -> str:
    head = f"<title>{escape(title)}</title>" if title else ""
    if body.startswith("<article") and body.rstrip().endswith("</article>"):
        article = body
    else:
        article = f"<article>{body}</article>"
    return f"<!DOCTYPE html><html><head>{head}</head><body>{article}</body></html>"


def _build_xml_namespaces(root) -> tuple[dict | None, str | None]:
    namespaces = {}
    for element in root.iter():
        for prefix, uri in (element.nsmap or {}).items():
            if prefix:
                namespaces.setdefault(prefix, uri)
    default_uri = (root.nsmap or {}).get(None)
    default_alias = _DEFAULT_NAMESPACE_ALIASES.get(default_uri or "")
    if default_alias:
        namespaces.setdefault(default_alias, default_uri)
    return namespaces or None, default_uri


def _render_xml_xpath_result(result) -> str:
    if isinstance(result, list) and result:
        result = result[0]
    if isinstance(result, etree._Element):
        return _render_xml_node(result, 0)
    if isinstance(result, bool):
        return _text_to_html("true" if result else "false")
    if isinstance(result, (int, float)):
        return _text_to_html(str(result))
    if isinstance(result, str):
        return _text_to_html(result)
    return ""


def xml_xpath_to_html(content: str, expressions: list[str]) -> str:
    """Select XML content with the first matching XPath expression."""
    if not content or not content.strip():
        return ""
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    root = etree.fromstring(content.encode("utf-8"), parser=parser)
    namespaces, default_uri = _build_xml_namespaces(root)

    for expression in expressions:
        result = None
        with suppress(etree.XPathError, ValueError):
            result = root.xpath(expression, namespaces=namespaces)
        if not result and default_uri:
            try:
                selector = XPathSelector(
                    expression,
                    namespaces=namespaces,
                    default_namespace=default_uri,
                )
                result = selector.select(root)
            except Exception:
                result = None
        body = _render_xml_xpath_result(result) if result else ""
        if body:
            return _wrap_selected_html(body, _xml_title(root))
    return ""


def _find_json_title(value, depth: int = 0) -> str:
    if depth > 3:
        return ""
    if isinstance(value, dict):
        for key in _TITLE_KEYS:
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
        for key in ("data", "post", "thread", "article", "result", "results", "item"):
            child = value.get(key)
            if isinstance(child, (dict, list)):
                title = _find_json_title(child, depth + 1)
                if title:
                    return title
    elif isinstance(value, list) and value:
        return _find_json_title(value[0], depth + 1)
    return ""


def _render_json_value(value, level: int = 0, key: str | None = None) -> str:
    key_attr = f' data-key="{escape(str(key))}"' if key is not None else ""

    if isinstance(value, dict):
        parts = []
        title = ""
        for title_key in _TITLE_KEYS:
            item = value.get(title_key)
            if isinstance(item, str) and item.strip():
                title = item.strip()
                break
        if title:
            heading = min(max(level + 1, 1), 6)
            parts.append(f"<h{heading}>{escape(title)}</h{heading}>")

        body = None
        for content_key in _CONTENT_KEYS:
            if content_key in value:
                body = value[content_key]
                break
        if body is not None:
            if isinstance(body, str):
                if _is_html_fragment(body):
                    parts.append(f'<div class="json-content">{body}</div>')
                else:
                    parts.append(_text_to_html(body))
            else:
                parts.append(_render_json_value(body, level + 1, "content"))

        for child_key, child in value.items():
            if child_key in _TITLE_KEYS or child_key in _CONTENT_KEYS:
                continue
            if not isinstance(child, (dict, list, str)):
                continue
            child_html = _render_json_value(child, level + 1, child_key)
            if child_html:
                child_key_attr = f' data-key="{escape(str(child_key))}"'
                parts.append(
                    f'<section class="json-field"{child_key_attr}>{child_html}</section>'
                )

        if not parts:
            return ""
        return "<article>" + "".join(parts) + "</article>"

    if isinstance(value, list):
        items = [_render_json_value(item, level, key) for item in value]
        items = [item for item in items if item]
        if not items:
            return ""
        return f'<section class="json-list"{key_attr}>{"".join(items)}</section>'

    if value is None or value == "":
        return ""
    return _text_to_html(str(value))


def json_to_html(content: str) -> str:
    """Convert a JSON snapshot to a semantic HTML document for defuddle."""
    if not content or not content.strip():
        return content or ""
    data = json.loads(content)
    body = _render_json_value(data)
    title = _find_json_title(data)
    head = f"<title>{escape(title)}</title>" if title else ""
    return f"<!DOCTYPE html><html><head>{head}</head><body>{body}</body></html>"


def _normalize_json_path(path: str) -> str:
    normalized = path.strip()
    if normalized.startswith("["):
        return "$" + normalized
    if not normalized.startswith("$"):
        return "$." + normalized
    return normalized


def json_path_to_html(content: str, expressions: list[str]) -> str:
    """Select JSON content with the first matching JSONPath expression."""
    if not content or not content.strip():
        return ""
    data = json.loads(content)
    for expression in expressions:
        try:
            nodes = jsonpath_find(_normalize_json_path(expression), data)
        except Exception:
            continue
        for node in nodes:
            body = _render_json_value(node.value)
            if body:
                return _wrap_selected_html(body, _find_json_title(data))
    return ""


def _render_html_xpath_result(result) -> str:
    if isinstance(result, list) and result:
        result = result[0]
    if isinstance(result, etree._Element):
        if _xml_local_name(result.tag) in ("body", "html"):
            return "".join(
                etree.tostring(child, encoding="unicode", method="html")
                for child in result
            )
        return etree.tostring(result, encoding="unicode", method="html")
    if isinstance(result, bool):
        return _text_to_html("true" if result else "false")
    if isinstance(result, (int, float)):
        return _text_to_html(str(result))
    if isinstance(result, str):
        return _text_to_html(result)
    return ""


def html_xpath_to_html(content: str, expressions: list[str]) -> str:
    """Select HTML content with the first matching XPath expression."""
    if not content or not content.strip():
        return ""
    root = lxml_html.fromstring(content)
    for expression in expressions:
        try:
            result = root.xpath(expression)
        except Exception:
            continue
        body = _render_html_xpath_result(result)
        if body:
            return _wrap_selected_html(body, _xml_title(root))
    return ""
