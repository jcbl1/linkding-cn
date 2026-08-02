"""
订阅机制

从 URL 下载适配器文件，缓存到 adapters/<name>/adapters.jsonc。
支持:
- 远程 HTTPS URL：下载并缓存
- 本地路径：直接读取（不缓存副本）
- _includes 递归展开
- 条件请求（ETag / Last-Modified）
- script 路径白名单
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
from site_adapters.services.base import _get_adapters_dir, _get_base_dir
from site_adapters.services.config import deep_merge, parse_jsonc

logger = logging.getLogger(__name__)

_ADAPTER_FILE = 'adapters.jsonc'
_OLD_SUB_FILE = 'subscription.jsonc'

_last_fetch_cache: dict[tuple[str, str], tuple[float, float]] = {}


def _get_adapters_dir_path() -> str:
    return _get_adapters_dir()


def _get_meta_path() -> str:
    return os.path.join(_get_adapters_dir_path(), '_meta.json')


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
    return hashlib.md5(url.encode()).hexdigest()[:12]


def _safe_name(name: str) -> str:
    if not name or not name.strip():
        return ''
    if '/' in name or '\\' in name or '..' in name:
        return ''
    if name.startswith('.'):
        return ''
    if not re.match(r'^[A-Za-z0-9][A-Za-z0-9._-]*$', name):
        return ''
    return name


def _sub_name(url: str, name: str = '') -> str:
    return _safe_name(name) or _url_to_name(url)


def _is_safe_entry_name(name: str) -> bool:
    if not name or '/' in name or '\\' in name or '..' in name:
        return False
    if name.startswith('.'):
        return False
    return True


def _resolve_script_ref(script_ref: str, base_url: str) -> tuple[str | None, str | None]:
    if script_ref.startswith('https://'):
        return script_ref, os.path.basename(urlparse(script_ref).path)
    if script_ref.startswith('http://'):
        logger.warning('Insecure script ref rejected (http://): %s', script_ref)
        return None, None
    if script_ref.startswith('./') or script_ref.startswith('../'):
        return urljoin(base_url, script_ref), os.path.basename(script_ref)
    return None, None


def _validate_https_url(url: str, resolve_dns: bool = False):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("URL must be HTTPS with a hostname: %s" % url)
    hostname = parsed.hostname

    def _check_ip(addr_str: str):
        try:
            addr = ipaddress.ip_address(addr_str)
        except ValueError:
            return
        if addr.is_private or addr.is_loopback or addr.is_link_local:
            raise ValueError("URL cannot target private/loopback: %s" % addr_str)

    _check_ip(hostname)
    if hostname and hostname[0].isdigit():
        _check_ip(hostname)

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
    return _validate_https_url(url)


def _validate_download_url(url: str):
    return _validate_https_url(url)


def is_remote_source(source: str | None) -> bool:
    """判断 source 是否为远程 URL。"""
    if not source:
        return False
    return source.startswith('https://') or source.startswith('http://')


def resolve_adapter_path(name: str, source: str | None, adapters_dir: str | None = None) -> str:
    """解析适配器文件路径。

    - source 为空 → adapters/<name>/adapters.jsonc
    - source 为 HTTPS URL → adapters/<name>/adapters.jsonc（下载目标）
    - source 为本地路径 → 直接使用该路径
    """
    if adapters_dir is None:
        adapters_dir = _get_adapters_dir_path()

    if source:
        if is_remote_source(source):
            return os.path.join(adapters_dir, name, _ADAPTER_FILE)
        # 本地路径
        if os.path.isabs(source):
            return source
        return os.path.normpath(os.path.join(adapters_dir, source))

    return os.path.join(adapters_dir, name, _ADAPTER_FILE)


# ---------------------------------------------------------------------------
# 下载
# ---------------------------------------------------------------------------

def _download_jsonc(url: str, etag: str = '', last_modified: str = '') -> tuple[dict | None, dict]:
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
    _MAX_SUBSCRIPTION_SIZE = 10 * 1024 * 1024
    content_length = resp.headers.get('Content-Length')
    if content_length:
        try:
            if int(content_length) > _MAX_SUBSCRIPTION_SIZE:
                raise ValueError("Subscription too large (%s bytes, max %d)" % (content_length, _MAX_SUBSCRIPTION_SIZE))
        except (ValueError, TypeError):
            pass
    content = resp.text
    if len(content) > _MAX_SUBSCRIPTION_SIZE:
        raise ValueError("Subscription too large (%d bytes, max %d)" % (len(content), _MAX_SUBSCRIPTION_SIZE))
    content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()

    data = parse_jsonc(content)
    if not isinstance(data, dict):
        raise ValueError("订阅顶层必须是对象")

    response_meta = {'content_hash': content_hash}
    if 'ETag' in resp.headers:
        response_meta['etag'] = resp.headers['ETag']
    if 'Last-Modified' in resp.headers:
        response_meta['last_modified'] = resp.headers['Last-Modified']

    return data, response_meta


def _download_version_json(url: str) -> tuple[int | None, str | None]:
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


def _write_adapter_file(file_path: str, url: str, data: dict, response_meta: dict = None):
    """将订阅写入 adapters/<name>/adapters.jsonc。"""
    sub_dir = os.path.dirname(file_path)
    os.makedirs(sub_dir, exist_ok=True)

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
            script_path = os.path.join(scripts_dir, local_name)
            try:
                _validate_download_url(download_url)
                resp = requests.get(download_url, timeout=15)
                resp.raise_for_status()
                new_content = resp.text
            except Exception as e:
                logger.warning("Failed to download script %s: %s", download_url, e)
                continue
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
        data.pop('scripts', None)

    content_str = json.dumps(data, indent=2, ensure_ascii=False)
    atomic_write(file_path, content_str)


def _read_subscription_file(file_path: str) -> dict | None:
    """读取适配器文件。支持 adapters.jsonc 和旧 subscription.jsonc。"""
    if not os.path.exists(file_path):
        # 回退：尝试旧文件名
        old_path = os.path.join(os.path.dirname(file_path), _OLD_SUB_FILE)
        if os.path.exists(old_path):
            file_path = old_path
        else:
            return None
    try:
        with open(file_path, encoding='utf-8') as f:
            return parse_jsonc(f.read())
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to read adapter file: %s: %s", file_path, e)
        return None


def list_cached_domains_from_file(file_path: str) -> list[str]:
    data = _read_subscription_file(file_path)
    if not data or not isinstance(data.get('domains'), dict):
        return []
    return sorted(data['domains'].keys())


def _get_adapter_dir(name: str, adapter_id: str = '') -> str:
    from site_adapters.services.base import _adapter_dir
    dir_name = _adapter_dir({'id': adapter_id, 'name': name}) if adapter_id else name
    return os.path.join(_get_adapters_dir_path(), dir_name)


def _get_adapter_cache_path(name: str, adapter_id: str = '') -> str:
    return os.path.join(_get_adapter_dir(name, adapter_id), _ADAPTER_FILE)


def is_allowed_script_path(script_path: str, base_dir: str) -> bool:
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
    下载远程订阅源并缓存为 adapters/<name>/adapters.jsonc

    如果 url 是本地路径，直接返回该路径（不下载）。

    Returns:
        缓存文件路径；失败时若旧缓存存在也返回该路径；完全失败返回 None。
    """
    # 本地路径：直接返回
    if not is_remote_source(url):
        if os.path.exists(url):
            return url
        logger.error("Local adapter file not found: %s", url)
        return None

    sub_name = _sub_name(url, name)
    # 使用 name 作为 id（下载时还不知道 _meta.id）
    file_path = _get_adapter_cache_path(sub_name, name)
    meta = _load_meta()

    try:
        validate_subscription_url(url)
    except ValueError as exc:
        logger.error(str(exc))
        return None

    file_meta = _get_file_meta(file_path)

    if not force:
        last_fetch = file_meta.get('last_fetch')
        interval = meta.get(url, {}).get('update_interval', 86400)
        if last_fetch and time.time() - last_fetch < interval:
            return file_path

    try:
        logger.info("Fetching subscription: %s", url)

        etag = file_meta.get('etag', '')
        last_modified = file_meta.get('last_modified', '')

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
            _update_file_last_fetch(file_path)
            logger.info("Subscription unchanged: %s", url)
            return file_path

        if '_includes' in data:
            data = _resolve_includes(url, data, set())

        _write_adapter_file(file_path, url, data, response_meta)

        meta.setdefault(url, {})
        meta[url]['last_fetch'] = time.time()
        meta[url]['name'] = sub_name
        sub_meta_inner = data.get('_meta', {})
        if isinstance(sub_meta_inner, dict) and sub_meta_inner.get('version'):
            meta[url]['version'] = sub_meta_inner['version']
        if check_url:
            meta[url]['check_update_url'] = check_url
        _save_meta(meta)

        cache_key = (url, name)
        interval = meta.get(url, {}).get('update_interval', 86400)
        _last_fetch_cache[cache_key] = (time.time(), interval)

        logger.info("Subscription updated: %s", url)
        return file_path
    except Exception as e:
        logger.error("Subscription fetch failed: %s: %s", url, e)
        return file_path if os.path.exists(file_path) else None


def _get_file_meta(file_path: str) -> dict:
    data = _read_subscription_file(file_path)
    if data and isinstance(data.get('_meta'), dict):
        return data['_meta']
    return {}


def _get_file_last_fetch(file_path: str) -> float | None:
    return _get_file_meta(file_path).get('last_fetch')


def _update_file_last_fetch(file_path: str):
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
    url = sub.get('url', '')
    if not url:
        return False
    if sub.get('enabled') is False:
        return False
    # 本地路径不需要拉取
    if not is_remote_source(url):
        return False

    now = time.time()
    name = sub.get('name', '')
    interval = sub.get('update_interval', 86400)
    cache_key = (url, name)

    cached = _last_fetch_cache.get(cache_key)
    if cached is not None:
        cached_fetch, cached_interval = cached
        if cached_interval == interval and now - cached_fetch < interval:
            return False

    sub_file = _get_adapter_cache_path(_sub_name(url, name), name)
    if not os.path.exists(sub_file):
        return True
    try:
        last_fetch = _get_file_last_fetch(sub_file)
        if last_fetch is None:
            return True
        _last_fetch_cache[cache_key] = (last_fetch, interval)
        return now - last_fetch >= interval
    except (json.JSONDecodeError, OSError):
        return True


def fetch_all_subscriptions(subscriptions: list[dict]) -> list[str]:
    if not any(_needs_fetch(sub) for sub in subscriptions if isinstance(sub, dict)):
        return []

    paths = []
    meta = _load_meta()

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
