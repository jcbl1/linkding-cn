"""
订阅机制

从 URL 下载规则包，缓存到 subscriptions/<name>/。
支持 _includes 递归展开。

增强：
- 条件请求（ETag / Last-Modified）避免重复下载
- 内容哈希（sha256）记录，供后续比对
- script 路径白名单防止目录遍历
"""

import hashlib
import ipaddress
import json
import logging
import os
import re
import time
from urllib.parse import urljoin, urlparse

import requests

from bookmarks.utils import atomic_write
from site_adapters.services.config import deep_merge, parse_jsonc

logger = logging.getLogger(__name__)

# 内存缓存：避免每分钟读磁盘检查 last_fetch
# key = (url, name), value = (last_fetch_ts, interval_sec)
_last_fetch_cache: dict[tuple[str, str], tuple[float, float]] = {}


def _get_subscriptions_dir() -> str:
    from site_adapters.services.base import _get_base_dir
    return os.path.join(_get_base_dir(), 'subscriptions')


def _get_meta_path() -> str:
    return os.path.join(_get_subscriptions_dir(), '_meta.json')


def _load_meta() -> dict:
    path = _get_meta_path()
    if os.path.exists(path):
        try:
            with open(path, encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_meta(meta: dict):
    atomic_write(_get_meta_path(), json.dumps(meta, indent=2, ensure_ascii=False))


def _url_to_name(url: str) -> str:
    """URL → 目录名。"""
    return hashlib.md5(url.encode()).hexdigest()[:12]


def _safe_name(name: str) -> str:
    """Validate subscription name: no path traversal, no leading dots, no whitespace-only."""
    if not name or not name.strip():
        return ''
    if '/' in name or '\\' in name or '..' in name:
        return ''
    if name.startswith('.'):
        return ''
    # Only allow alphanumeric, hyphens, underscores, dots (not leading)
    if not re.match(r'^[A-Za-z0-9][A-Za-z0-9._-]*$', name):
        return ''
    return name


def _sub_name(url: str, name: str = '') -> str:
    return _safe_name(name) or _url_to_name(url)


def _is_safe_entry_name(name: str) -> bool:
    """Validate file entry name: no path traversal, no leading dots."""
    if not name or '/' in name or '\\' in name or '..' in name:
        return False
    if name.startswith('.'):
        return False
    return True


def _resolve_script_ref(script_ref: str, base_url: str) -> tuple[str | None, str | None]:
    """Resolve a script reference to (download_url, local_filename).
    Returns (None, None) if the ref is not downloadable."""
    if script_ref.startswith('https://'):
        return script_ref, os.path.basename(urlparse(script_ref).path)
    if script_ref.startswith('http://'):
        logger.warning('Insecure script ref rejected (http://): %s', script_ref)
        return None, None
    if script_ref.startswith('./') or script_ref.startswith('../'):
        return urljoin(base_url, script_ref), os.path.basename(script_ref)
    return None, None


def _validate_https_url(url: str, resolve_dns: bool = False):
    """Validate URL is HTTPS and not targeting private/loopback IPs (SSRF protection).

    Always checks direct IP literals. When *resolve_dns* is True, also resolves
    domain names and verifies that no resolved address is private/loopback.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("URL must be HTTPS with a hostname: %s" % url)
    hostname = parsed.hostname

    def _check_ip(addr_str: str):
        """Raise ValueError if addr_str is a private/loopback/link-local IP."""
        try:
            addr = ipaddress.ip_address(addr_str)
        except ValueError:
            return  # not a recognized IP encoding
        if addr.is_private or addr.is_loopback or addr.is_link_local:
            raise ValueError("URL cannot target private/loopback: %s" % addr_str)

    # Check if hostname is a direct IP literal (covers 192.168.x.x, [::1], etc.)
    _check_ip(hostname)

    # Also check encoded IP forms that ipaddress accepts (0x7f000001, 2130706433, etc.)
    if hostname and hostname[0].isdigit():
        _check_ip(hostname)

    # Optionally resolve DNS and check results
    if resolve_dns:
        import socket
        try:
            infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
            for family, _, _, _, sockaddr in infos:
                resolved = ipaddress.ip_address(sockaddr[0])
                if resolved.is_private or resolved.is_loopback or resolved.is_link_local:
                    raise ValueError(
                        "URL resolves to private/loopback address: %s -> %s" % (hostname, resolved)
                    )
        except socket.gaierror as exc:
            raise ValueError("Cannot resolve hostname: %s: %s" % (hostname, exc)) from exc

    return parsed


def validate_subscription_url(url: str):
    """Public SSRF check for subscription URLs."""
    return _validate_https_url(url)


def _validate_download_url(url: str):
    """SSRF check for script download URLs."""
    return _validate_https_url(url)

# ---------------------------------------------------------------------------
# 下载（支持条件请求 + 哈希记录）
# ---------------------------------------------------------------------------

def _download_jsonc(url: str, etag: str = '', last_modified: str = '') -> tuple[dict | None, dict]:
    # SECURITY: callers must validate url via validate_subscription_url() before calling
    """下载并解析 JSONC 订阅。

    Returns:
        (data, response_meta): data 为 None 表示 304 未变化。
        response_meta 包含 etag/last_modified/content_hash。
    """
    headers = {}
    if etag:
        headers['If-None-Match'] = etag
    if last_modified:
        headers['If-Modified-Since'] = last_modified

    resp = requests.get(url, timeout=30, headers=headers)
    if resp.status_code == 304:
        logger.info("Subscription not modified (304): %s", url)
        return None, {}

    resp.raise_for_status()
    # Limit response size to 10MB to prevent memory issues
    _MAX_SUBSCRIPTION_SIZE = 10 * 1024 * 1024
    # Pre-check Content-Length header for early rejection
    content_length = resp.headers.get('Content-Length')
    if content_length:
        try:
            if int(content_length) > _MAX_SUBSCRIPTION_SIZE:
                raise ValueError("Subscription too large (%s bytes, max %d)" % (content_length, _MAX_SUBSCRIPTION_SIZE))
        except (ValueError, TypeError):
            pass  # ignore unparseable Content-Length
    content = resp.text
    if len(content) > _MAX_SUBSCRIPTION_SIZE:
        raise ValueError("Subscription too large (%d bytes, max %d)" % (len(content), _MAX_SUBSCRIPTION_SIZE))
    content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()

    data = parse_jsonc(content)
    if not isinstance(data, dict):
        raise ValueError("订阅顶层必须是对象")

    # 内容哈希记录（供 _meta.json 存储，便于后续人工比对或审计）

    response_meta = {'content_hash': content_hash}
    if 'ETag' in resp.headers:
        response_meta['etag'] = resp.headers['ETag']
    if 'Last-Modified' in resp.headers:
        response_meta['last_modified'] = resp.headers['Last-Modified']

    return data, response_meta


def _download_version_json(url: str) -> tuple[int | None, str | None]:
    """下载 checkUpdateUrl，返回 (version, updateUrl)。

    checkUpdateUrl 返回格式: {"id": ..., "version": ...}
    可选包含 "updateUrl" 字段。
    """
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = parse_jsonc(resp.text)
        if not isinstance(data, dict):
            return None, None
        version = data.get('version')
        update_url = data.get('updateUrl')
        if isinstance(version, (int, float)):
            return int(version), update_url
        return None, None
    except Exception as e:
        logger.warning("checkUpdateUrl failed: %s: %s", url, e)
        return None, None


def _domain_map(data: dict) -> dict:
    if isinstance(data.get('domains'), dict):
        return data['domains']
    return {
        key: value for key, value in data.items()
        if key not in ('*', 'domains') and not key.startswith('_')
    }


def _normalize_domain_config(value):
    if isinstance(value, str):
        return {"type": "alias", "target": value}
    return value


def _materialize_domains(data: dict) -> dict:
    defaults = data.get('*', {})
    domains = {}
    for domain_key, config in _domain_map(data).items():
        config = _normalize_domain_config(config)
        if isinstance(config, dict) and config.get('type') != 'alias' and defaults:
            config = deep_merge(defaults, config)
        domains[domain_key] = config
    return domains


_MAX_INCLUDES_DEPTH = 10

def _resolve_includes(url: str, data: dict, seen: set[str], _depth: int = 0) -> dict:
    if url in seen:
        raise ValueError("Subscription _includes cycle: %s" % url)
    if _depth >= _MAX_INCLUDES_DEPTH:
        raise ValueError("Subscription _includes too deep (max %d): %s" % (_MAX_INCLUDES_DEPTH, url))
    seen.add(url)

    includes = data.get('_includes', [])
    if isinstance(includes, str):
        includes = [includes]

    merged_domains = {}

    # 靠前 include 优先，因此先落低优先级、后落高优先级。
    for include_url in reversed(includes or []):
        include_url = urljoin(url, include_url)
        try:
            validate_subscription_url(include_url)
        except ValueError as exc:
            logger.warning('Skipping unsafe include URL: %s: %s', include_url, exc)
            continue
        include_data, _ = _download_jsonc(include_url)
        if include_data is None:
            continue
        include_data = _resolve_includes(include_url, include_data, seen, _depth + 1)
        merged_domains.update(_materialize_domains(include_data))

    merged_domains.update(_domain_map(data))

    result = dict(data)
    result['domains'] = merged_domains
    result.pop('_includes', None)
    seen.remove(url)
    return result




def _collect_script_refs(data: dict) -> set[str]:
    """扫描域名配置，收集所有 script 引用（相对路径或 URL）。"""
    refs = set()
    domains = _domain_map(data)
    for domain_config in domains.values():
        if not isinstance(domain_config, dict):
            continue
        for section in ('metadata', 'snapshot'):
            section_data = domain_config.get(section)
            if not isinstance(section_data, dict):
                continue
            script = section_data.get('script')
            if isinstance(script, str) and script:
                refs.add(script)
    return refs


def _write_subscription_file(file_path: str, url: str, data: dict, response_meta: dict = None):
    """将订阅写入 {name}/subscription.jsonc 格式。

    scripts 处理：扫描域名配置中的 script 引用（相对路径或 URL），
    下载并缓存到 {name}/scripts/ 子目录。
    """
    sub_dir = os.path.dirname(file_path)
    os.makedirs(sub_dir, exist_ok=True)

    # 确保 _meta 字段存在
    meta = data.get('_meta', {})
    if not isinstance(meta, dict):
        meta = {}
    meta['last_fetch'] = time.time()
    meta['url'] = url
    if response_meta:
        meta.setdefault('etag', response_meta.get('etag', ''))
        meta.setdefault('last_modified', response_meta.get('last_modified', ''))
        meta.setdefault('content_hash', response_meta.get('content_hash', ''))
    data['_meta'] = meta

    # 处理 scripts：扫描域名配置中的引用，下载到 {name}/scripts/
    script_refs = _collect_script_refs(data)
    if script_refs:
        scripts_dir = os.path.join(sub_dir, 'scripts')
        os.makedirs(scripts_dir, exist_ok=True)
        for script_ref in script_refs:
            download_url, local_name = _resolve_script_ref(script_ref, url)
            if not download_url:
                continue
            if not local_name or not _is_safe_entry_name(local_name):
                continue
            # 下载脚本（带 SSRF 防护）
            script_path = os.path.join(scripts_dir, local_name)
            try:
                _validate_download_url(download_url)
                resp = requests.get(download_url, timeout=15)
                resp.raise_for_status()
                new_content = resp.text
            except Exception as e:
                logger.warning("Failed to download script %s: %s", download_url, e)
                continue
            # 按内容哈希判断是否需要写入
            new_hash = hashlib.sha256(new_content.encode('utf-8')).hexdigest()
            old_hash = ''
            if os.path.exists(script_path):
                try:
                    with open(script_path, encoding='utf-8') as f:
                        old_hash = hashlib.sha256(f.read().encode('utf-8')).hexdigest()
                except OSError:
                    pass
            if new_hash != old_hash:
                atomic_write(script_path, new_content)
                logger.info("Script updated: %s", local_name)

        # 清理不再引用的旧脚本
        existing_scripts = set(os.listdir(scripts_dir))
        referenced_names = set()
        for ref in script_refs:
            _, ref_name = _resolve_script_ref(ref, url)
            if ref_name:
                referenced_names.add(ref_name)
        for old_script in existing_scripts - referenced_names:
            if old_script.startswith('.'):
                continue
            try:
                os.remove(os.path.join(scripts_dir, old_script))
                logger.info("Removed unused script: %s", old_script)
            except OSError:
                pass
        # 移除顶层 scripts 字段（如果有遗留）
        data.pop('scripts', None)

    # 原子写入订阅文件
    content_str = json.dumps(data, indent=2, ensure_ascii=False)
    atomic_write(file_path, content_str)


def _read_subscription_file(file_path: str) -> dict | None:
    """读取单文件格式的订阅。"""
    if not os.path.exists(file_path):
        return None
    try:
        with open(file_path, encoding='utf-8') as f:
            return parse_jsonc(f.read())
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to read subscription file: %s: %s", file_path, e)
        return None


def list_cached_domains_from_file(file_path: str) -> list[str]:
    """从单文件格式中列出域名。"""
    data = _read_subscription_file(file_path)
    if not data or not isinstance(data.get('domains'), dict):
        return []
    return sorted(data['domains'].keys())


def _get_subscription_dir(name: str) -> str:
    """获取订阅源缓存目录路径。"""
    return os.path.join(_get_subscriptions_dir(), name)


def _get_subscription_cache_path(name: str) -> str:
    """获取订阅源单文件路径。"""
    return os.path.join(_get_subscription_dir(name), 'subscription.jsonc')


# ---------------------------------------------------------------------------
# Script 路径白名单
# ---------------------------------------------------------------------------

def is_allowed_script_path(script_path: str, base_dir: str) -> bool:
    """检查脚本路径是否在站点适配根目录内。"""
    abs_path = os.path.abspath(script_path)
    abs_base = os.path.abspath(base_dir)
    try:
        return os.path.commonpath([abs_path, abs_base]) == abs_base
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# 单个订阅下载
# ---------------------------------------------------------------------------

def fetch_subscription(url: str, name: str = '', force: bool = False) -> str | None:
    """
    下载订阅源并缓存为单文件：subscriptions/<name>/subscription.jsonc

    Returns:
        缓存文件路径（即使下载失败，若旧缓存仍存在也返回该路径）；
        仅当下载失败且无旧缓存时返回 None。
    """
    sub_name = _sub_name(url, name)
    file_path = _get_subscription_cache_path(sub_name)
    meta = _load_meta()

    try:
        validate_subscription_url(url)
    except ValueError as exc:
        logger.error(str(exc))
        return None

    # 一次性读取文件 _meta（避免 3 次重复 I/O）
    file_meta = _get_file_meta(file_path)

    # 检查是否需要更新
    if not force:
        last_fetch = file_meta.get('last_fetch')
        interval = meta.get(url, {}).get('update_interval', 86400)
        if last_fetch and time.time() - last_fetch < interval:
            return file_path

    try:
        logger.info("Fetching subscription: %s", url)

        # 条件请求信息（来自同一个 file_meta）
        etag = file_meta.get('etag', '')
        last_modified = file_meta.get('last_modified', '')

        # checkUpdateUrl 轻量版本检测（gkd 机制）
        check_url = meta.get(url, {}).get('check_update_url')
        if check_url and not force:
            try:
                validate_subscription_url(check_url)
            except ValueError as exc:
                logger.warning('checkUpdateUrl failed validation: %s: %s', check_url, exc)
                check_url = None
        if check_url and not force:
            remote_version, remote_update_url = _download_version_json(check_url)
            if remote_version is not None:
                v = file_meta.get('version')
                local_version = int(v) if isinstance(v, (int, float)) else None
                if local_version is not None and remote_version <= local_version:
                    logger.info("Subscription version unchanged: %s (v%d)", url, remote_version)
                    _update_file_last_fetch(file_path)
                    return file_path
                if remote_update_url:
                    try:
                        validate_subscription_url(remote_update_url)
                        url = remote_update_url
                    except ValueError as exc:
                        logger.warning('updateUrl failed validation: %s: %s', remote_update_url, exc)

        data, response_meta = _download_jsonc(url, etag=etag, last_modified=last_modified)

        if data is None:
            # 304 Not Modified
            _update_file_last_fetch(file_path)
            logger.info("Subscription unchanged: %s", url)
            return file_path

        # Resolve _includes if present
        if '_includes' in data:
            data = _resolve_includes(url, data, set())

        _write_subscription_file(file_path, url, data, response_meta)

        # 更新全局 meta
        meta.setdefault(url, {})
        meta[url]['last_fetch'] = time.time()
        meta[url]['name'] = sub_name
        sub_meta_inner = data.get('_meta', {})
        if isinstance(sub_meta_inner, dict) and sub_meta_inner.get('version'):
            meta[url]['version'] = sub_meta_inner['version']
        if check_url:
            meta[url]['check_update_url'] = check_url
        _save_meta(meta)

        # Update in-memory cache
        cache_key = (url, name)
        interval = meta.get(url, {}).get('update_interval', 86400)
        _last_fetch_cache[cache_key] = (time.time(), interval)

        logger.info("Subscription updated: %s", url)
        return file_path
    except Exception as e:
        logger.error("Subscription fetch failed: %s: %s", url, e)
        return file_path if os.path.exists(file_path) else None


def _get_file_meta(file_path: str) -> dict:
    """读取订阅文件的 _meta 字段（单次读取）。"""
    data = _read_subscription_file(file_path)
    if data and isinstance(data.get('_meta'), dict):
        return data['_meta']
    return {}


def _get_file_last_fetch(file_path: str) -> float | None:
    """获取单文件订阅的上次拉取时间。"""
    return _get_file_meta(file_path).get('last_fetch')



def _update_file_last_fetch(file_path: str):
    """更新单文件订阅的 last_fetch。"""
    data = _read_subscription_file(file_path)
    if data:
        meta_inner = data.get('_meta', {})
        if isinstance(meta_inner, dict):
            meta_inner['last_fetch'] = time.time()
            data['_meta'] = meta_inner
            atomic_write(file_path, json.dumps(data, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# 批量更新
# ---------------------------------------------------------------------------

def _needs_fetch(sub: dict) -> bool:
    """检查单个订阅是否需要拉取（基于 last_fetch + interval）。

    优先查内存缓存，命中且未过期则直接返回 False（零 I/O）。
    缓存未命中时才读磁盘，并回填缓存。
    """
    url = sub.get('url', '')
    if not url:
        return False
    if sub.get('enabled') is False:
        return False

    now = time.time()
    name = sub.get('name', '')
    interval = sub.get('update_interval', 86400)
    cache_key = (url, name)

    # 内存快速路径：缓存命中且仍在有效期内
    cached = _last_fetch_cache.get(cache_key)
    if cached is not None:
        cached_fetch, cached_interval = cached
        if cached_interval == interval and now - cached_fetch < interval:
            return False

    # 缓存未命中或已过期 → 读磁盘
    sub_file = _get_subscription_cache_path(_sub_name(url, name))
    if not os.path.exists(sub_file):
        return True  # 从未拉取过
    try:
        last_fetch = _get_file_last_fetch(sub_file)
        if last_fetch is None:
            return True
        _last_fetch_cache[cache_key] = (last_fetch, interval)
        return now - last_fetch >= interval
    except (json.JSONDecodeError, OSError):
        return True


def fetch_all_subscriptions(subscriptions: list[dict]) -> list[str]:
    """
    下载所有订阅，返回目录路径列表。
    subscriptions 格式: [{"url": "...", "name": "...", "update_interval": 86400}]
    """
    # 快速检查：是否有任何订阅需要拉取
    if not any(_needs_fetch(sub) for sub in subscriptions if isinstance(sub, dict)):
        return []

    paths = []
    meta = _load_meta()

    # 收集所有 update_interval，仅在有变化时写入
    changed = False
    for sub in subscriptions:
        url = (sub.get('url') or '') if isinstance(sub, dict) else ''
        if url:
            interval = sub.get('update_interval', 86400) if isinstance(sub, dict) else 86400
            if meta.get(url, {}).get('update_interval') != interval:
                meta.setdefault(url, {})['update_interval'] = interval
                changed = True
    if changed:
        _save_meta(meta)

    for sub in subscriptions:
        if sub.get('enabled') is False:
            continue
        url = sub.get('url')
        if not url:
            continue
        name = sub.get('name', '')

        path = fetch_subscription(url, name=name)
        if path:
            paths.append(path)

    return paths
