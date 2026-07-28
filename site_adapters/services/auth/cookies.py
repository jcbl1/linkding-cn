"""
Cookie 生命周期管理（管理员共享 cookie）

存储：per-domain 文件 cookies/*.json（Playwright 标准格式）
元数据：cookies/cookies.json（updated_at / source）
冷却期：内存 dict（不持久化）
验证：auth.cookie.verify（声明式 invalid_patterns / valid_selector）
刷新：auth.cookie.refresh + 冷却期
"""

import json
import logging
import os
import subprocess
import tempfile
import time

from bookmarks.utils import atomic_write, is_safe_domain_key
from site_adapters.services.execution_log import log_execution

logger = logging.getLogger(__name__)

COOLDOWN_SECONDS = 300  # 5 分钟冷却期
MAX_COOLDOWN_ENTRIES = 1000

# 内存冷却期（不持久化）
_cooldowns: dict[str, float] = {}

# ---------------------------------------------------------------------------
# 声明式 cookie 默认值
# ---------------------------------------------------------------------------
COOKIE_DEFAULTS = {
    "type": "anon",
    "verify": {
        "check": ["title", "body"],
        "invalid_patterns": [],
    },
    "refresh": {
        "timeout": 30000,
    },
    "refresh_interval": 14400,
}


def merge_cookie(base: dict, override: dict) -> dict:
    """Merge two cookie config dicts.  override values replace base;
    sub-objects (verify / refresh) are replaced as a whole, not deep-merged."""
    if not base:
        return dict(override) if override else {}
    if not override:
        return dict(base)
    result = dict(base)
    for key, value in override.items():
        if value is not None:
            result[key] = value
    return result


def derive_cookie_file(domain_key: str) -> str:
    """从 domain key 自动推导 cookie 文件路径。"""
    from site_adapters.services.base import _get_base_dir
    return os.path.join(_get_base_dir(), 'cookies', f'{domain_key}.json')


def _get_cookies_dir() -> str:
    from site_adapters.services.base import _get_base_dir
    return os.path.join(_get_base_dir(), 'cookies')


# ---------------------------------------------------------------------------
# 文件操作
# ---------------------------------------------------------------------------

def _match_cookie_file(domain_key: str, cookies_dir: str) -> str | None:
    if not os.path.isdir(cookies_dir):
        return None
    best_wildcard = None
    best_wildcard_depth = -1
    for fname in os.listdir(cookies_dir):
        if not fname.endswith('.json') or fname == 'cookies.json':
            continue
        file_key = fname[:-5]
        if file_key == domain_key:
            return os.path.join(cookies_dir, fname)
        if file_key.startswith('*.'):
            suffix = file_key[1:]
            if domain_key.endswith(suffix):
                depth = file_key.count('.')
                if depth > best_wildcard_depth:
                    best_wildcard = os.path.join(cookies_dir, fname)
                    best_wildcard_depth = depth
    return best_wildcard


def _load_cookie_data(path: str) -> list | dict | None:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _cookie_data_to_string(data) -> str | None:
    """Convert Playwright cookie list to header string."""
    if isinstance(data, list):
        pairs = []
        for item in data:
            if isinstance(item, dict) and item.get("name") and "value" in item:
                pairs.append(f"{item['name']}={item['value']}")
        return "; ".join(pairs) if pairs else None
    if isinstance(data, str):
        return data
    return None


def cookie_string_to_playwright_list(cookie_str: str, domain_key: str) -> list[dict]:
    """Convert a 'name=value; name2=value2' cookie string to Playwright format list.

    Shared utility to avoid duplicating this conversion in cookies, credentials, and browser_fallback.
    """
    cookies_list = []
    for pair in cookie_str.split(';'):
        pair = pair.strip()
        if '=' in pair:
            name, value = pair.split('=', 1)
            cookies_list.append({
                "name": name.strip(),
                "value": value.strip(),
                "domain": domain_key.removeprefix('*.'),
                "path": "/",
            })
    return cookies_list


def load_cookie_file(path: str) -> str | None:
    return _cookie_data_to_string(_load_cookie_data(path))


def _save_cookie_data(path: str, data):
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# 元数据管理（cookies/cookies.json）
# ---------------------------------------------------------------------------

def _get_meta_path() -> str:
    return os.path.join(_get_cookies_dir(), 'cookies.json')


def _load_meta() -> dict:
    path = _get_meta_path()
    data = _load_cookie_data(path)
    return data if isinstance(data, dict) else {}


def _save_meta(meta: dict):
    _save_cookie_data(_get_meta_path(), meta)


def _update_meta(domain_key: str, **fields):
    meta = _load_meta()
    entry = meta.get(domain_key, {})
    entry.update(fields)
    meta[domain_key] = entry
    _save_meta(meta)


def get_cookie_meta(domain_key: str) -> dict:
    return _load_meta().get(domain_key, {})


def list_all_cookies_meta() -> dict:
    return _load_meta()


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------

def get_cookie_for_domain(domain_key: str) -> str | None:
    cookies_dir = _get_cookies_dir()
    path = _match_cookie_file(domain_key, cookies_dir)
    if not path:
        return None
    data = _load_cookie_data(path)
    if not data:
        return None
    return _cookie_data_to_string(data)


def get_cookie_file_for_domain(domain_key: str) -> str:
    cookies_dir = _get_cookies_dir()
    return _match_cookie_file(domain_key, cookies_dir) or os.path.join(cookies_dir, f'{domain_key}.json')


def has_cookie_for_domain(domain_key: str) -> bool:
    return get_cookie_for_domain(domain_key) is not None


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------

def save_cookie_for_domain(domain_key: str, cookie_str: str, source: str = "paste"):
    if not is_safe_domain_key(domain_key):
        raise ValueError("invalid domain key")
    cookies_dir = _get_cookies_dir()
    path = os.path.join(cookies_dir, f'{domain_key}.json')

    # 写入 Playwright 格式（使用共享转换函数）
    cookies_list = cookie_string_to_playwright_list(cookie_str, domain_key)
    _save_cookie_data(path, cookies_list)

    # 更新元数据
    _update_meta(domain_key,
                 updated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 source=source)


# ---------------------------------------------------------------------------
# 冷却期（内存）
# ---------------------------------------------------------------------------

def _is_in_cooldown(domain_key: str) -> bool:
    return time.monotonic() < _cooldowns.get(domain_key, 0)


def _evict_stale_cooldowns():
    """Remove expired cooldown entries to prevent unbounded memory growth."""
    now = time.monotonic()
    expired = [k for k, v in _cooldowns.items() if v <= now]
    for k in expired:
        _cooldowns.pop(k, None)


def _set_cooldown(domain_key: str):
    # Periodically evict stale entries to prevent unbounded growth
    if len(_cooldowns) >= MAX_COOLDOWN_ENTRIES:
        _evict_stale_cooldowns()
    _cooldowns[domain_key] = time.monotonic() + COOLDOWN_SECONDS


def _clear_cooldown(domain_key: str):
    _cooldowns.pop(domain_key, None)


# ---------------------------------------------------------------------------
# 验证（声明式）
# ---------------------------------------------------------------------------

def verify_cookie_declarative(verify_config: dict, context: dict) -> dict:
    """
    声明式 cookie 验证。
    verify_config: {check, invalid_patterns, valid_selector}
    context: {url, title, body_preview, html_path}
    返回: {valid: bool, reason: str}
    """
    check = verify_config.get("check", ["title", "body"])
    invalid = verify_config.get("invalid_patterns", [])
    valid_selector = verify_config.get("valid_selector", "")

    # 1. 正向确认：CSS 选择器存在 → 有效
    # Note: valid_selector requires html_path in context (currently unused by callers)
    if valid_selector and context.get("html_path"):
        try:
            from bs4 import BeautifulSoup
            with open(context["html_path"], encoding="utf-8") as f:
                soup = BeautifulSoup(f.read(), "html.parser")
            if soup.select_one(valid_selector):
                return {"valid": True, "reason": "selector matched"}
        except Exception:
            pass

    # 2. 没有配置 invalid_patterns → 跳过检测，认为有效
    if not invalid:
        return {"valid": True, "reason": "no patterns configured"}

    invalid_lower = [p.lower() for p in invalid]

    # 3. 检查 title
    if "title" in check and context.get("title"):
        title_lower = context["title"].lower()
        matched = next((p for p in invalid_lower if p in title_lower), None)
        if matched:
            return {"valid": False, "reason": f'title matches "{matched}"'}

    # 4. 检查 body
    if "body" in check:
        text = context.get("body_preview", "")
        if not text and context.get("html_path"):
            try:
                with open(context["html_path"], encoding="utf-8") as f:
                    text = f.read(5000)
            except Exception:
                pass
        text_lower = text.lower()
        matched = next((p for p in invalid_lower if p in text_lower), None)
        if matched:
            return {"valid": False, "reason": f'body matches "{matched}"'}

    return {"valid": True, "reason": "ok"}


# ---------------------------------------------------------------------------
# 刷新
# ---------------------------------------------------------------------------

def refresh_cookie_declarative(refresh_config: dict, url: str,
                                cookie_file: str, domain_key: str) -> bool:
    """
    声明式 cookie 刷新。使用内置 refresh_cookies.js。
    refresh_config: {url, wait_cookie, timeout}
    内置冷却期机制：失败后 5 分钟内不再尝试。
    返回是否成功。
    """
    if not cookie_file:
        return False

    script_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'engine', 'scripts', 'refresh_cookies.js',
    )
    if not os.path.exists(script_path):
        logger.error("Built-in refresh script not found: %s", script_path)
        return False

    if _is_in_cooldown(domain_key):
        logger.info("Cookie refresh in cooldown, skipping: %s", domain_key)
        return False

    from django.conf import settings as django_settings
    chromium_path = getattr(django_settings, 'LD_BROWSER_CHROMIUM_PATH', '') or os.getenv('CHROMIUM_PATH', '')
    refresh_url = refresh_config.get('url') or url
    wait_cookie = refresh_config.get('wait_cookie', '')
    timeout = refresh_config.get('timeout', 30000)
    license_key = getattr(django_settings, 'LD_BROWSER_CLOAKBROWSER_LICENSE_KEY', '')

    try:
        start = time.monotonic()
        input_data = {
            'url': refresh_url,
            'cookie_file': cookie_file,
            'outputPath': cookie_file,
            'wait_cookie': wait_cookie,
            'waitCookie': wait_cookie,
            'chromium_path': chromium_path,
            'timeout': timeout,
            'licenseKey': license_key,
        }
        cmd = ['node', script_path]
        # subprocess 超时 = 脚本超时（秒）+ 30s 缓冲（进程启动/关闭开销）
        subprocess_timeout = max(timeout / 1000 + 30, 60)
        result = subprocess.run(
            cmd,
            input=json.dumps(input_data),
            capture_output=True, text=True, timeout=subprocess_timeout,
            env={**os.environ, 'LD_BROWSER_ENGINE': getattr(django_settings, 'LD_BROWSER_ENGINE', 'cloakbrowser'),
                 'CLOAKBROWSER_LICENSE_KEY': license_key},
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        log_execution(
            url=url,
            domain_key=domain_key,
            step='cookie_refresh',
            cmd=cmd,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=duration_ms,
            config_snapshot={'cookie_file': cookie_file, 'wait_cookie': wait_cookie},
        )

        if result.returncode == 0:
            _clear_cooldown(domain_key)
            logger.info("Cookie refresh succeeded: %s", domain_key)
            return True
        else:
            _set_cooldown(domain_key)
            logger.error("Cookie refresh failed: %s: %s", domain_key, result.stderr[:200])
            return False
    except Exception as e:
        _set_cooldown(domain_key)
        logger.error("Cookie refresh error: %s: %s", domain_key, e)
        log_execution(
            url=url,
            domain_key=domain_key,
            step='cookie_refresh',
            cmd=['node', script_path],
            returncode=1,
            stderr=str(e),
        )
        return False


# ---------------------------------------------------------------------------
# 验证 + 刷新流程（声明式）
# ---------------------------------------------------------------------------

def verify_and_refresh(cookie_config: dict, url: str, domain_key: str,
                       verify_context: dict) -> str | None:
    """
    完整的 cookie 验证 + 刷新流程。
    cookie_config: 完整的 cookie 配置块（已合并 + 已解析路径）
    返回 cookie 字符串（可能为 None）。
    """
    cookie_file = cookie_config.get('file', '')
    cookie_str = load_cookie_file(cookie_file) if cookie_file else get_cookie_for_domain(domain_key)

    # 没有 cookie 且有 refresh 配置 → 尝试刷新
    if not cookie_str and cookie_config.get('refresh'):
        if refresh_cookie_declarative(cookie_config['refresh'], url, cookie_file, domain_key):
            return load_cookie_file(cookie_file)

    verify_cfg = cookie_config.get('verify', {})
    invalid_patterns = verify_cfg.get('invalid_patterns', [])

    # 没有配置 invalid_patterns → 不验证，直接返回
    if not invalid_patterns:
        return cookie_str

    # 验证
    verify_context.setdefault('domain_key', domain_key)
    result = verify_cookie_declarative(verify_cfg, verify_context)
    if result.get('valid'):
        return cookie_str

    logger.info("Cookie invalid: %s: %s", domain_key, result.get("reason"))

    # 刷新
    if cookie_config.get('refresh'):
        if refresh_cookie_declarative(cookie_config['refresh'], url, cookie_file, domain_key):
            return load_cookie_file(cookie_file)

    return cookie_str


# ---------------------------------------------------------------------------
# 临时文件（供 SingleFile 使用）
# ---------------------------------------------------------------------------

def generate_temp_cookies_file(domain_key: str, cookie_str: str = None) -> str | None:
    """Generate a temporary Playwright cookies file.

    Caller must delete the returned path after use.
    """
    if cookie_str is None:
        cookie_str = get_cookie_for_domain(domain_key)
    if not cookie_str:
        return None

    cookies_list = cookie_string_to_playwright_list(cookie_str, domain_key)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as tmp:
        json.dump(cookies_list, tmp)
        return tmp.name
