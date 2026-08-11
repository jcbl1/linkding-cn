"""
订阅机制

将远程订阅源目录镜像到本地：下载 adapters.jsonc 和 scripts/ 中的所有引用脚本。
- 远程 HTTPS 目录 → 下载并缓存
- 本地目录 → 直接读取（不缓存副本）
- _includes 递归展开
- 条件请求（ETag / Last-Modified）
- 运行时状态存储于 _meta.json（不污染缓存的 adapters.jsonc）
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


# ---------------------------------------------------------------------------
# _meta.json — 订阅源运行时状态
# ---------------------------------------------------------------------------

def _get_meta_path() -> str:
    return os.path.join(_get_adapters_dir(), '_meta.json')


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


def _get_meta_entry(url: str) -> dict:
    """获取指定订阅源的运行时状态条目。"""
    meta = _load_meta()
    entry = meta.get(url)
    if isinstance(entry, dict):
        return entry
    return {}


def _update_meta_entry(url: str, **fields):
    """更新指定订阅源的运行时状态。"""
    meta = _load_meta()
    entry = meta.get(url, {})
    if not isinstance(entry, dict):
        entry = {}
    entry.update(fields)
    meta[url] = entry
    _save_meta(meta)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _get_adapters_dir_path() -> str:
    return _get_adapters_dir()


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


def _is_safe_script_key(key: str) -> bool:
    """验证脚本存储键是否安全（允许子目录，拒绝 .. 和隐藏文件）。"""
    if not key or key.startswith('/') or key.endswith('/') or '//' in key:
        return False
    parts = key.split('/')
    for part in parts:
        if not part or part == '..' or part.startswith('.'):
            return False
    return True


def _resolve_script_ref(script_ref: str, base_url: str) -> tuple[str | None, str | None]:
    """解析脚本引用，返回 (下载 URL, 本地存储键)。

    订阅源视为自包含目录，所有脚本路径相对于 base_url 解析。
    不支持外部 HTTPS URL —— 发布者应将脚本放入 scripts/ 目录。
    """
    if script_ref.startswith('https://') or script_ref.startswith('http://'):
        logger.warning('External script URLs not supported in subscriptions: %s', script_ref)
        return None, None

    # 计算本地存储键和对应的下载 URL
    if script_ref.startswith('./'):
        local_key = script_ref[2:]  # 去掉 ./
        download_url = urljoin(base_url, script_ref)
    elif script_ref.startswith('../'):
        download_url = urljoin(base_url, script_ref)
        parts = urlparse(download_url).path.strip('/').split('/')
        if 'scripts' in parts:
            idx = parts.index('scripts')
            local_key = '/'.join(parts[idx + 1:])
        else:
            local_key = parts[-1] if parts else ''
    else:
        # 纯文件名或目录前缀名：远端在 scripts/ 下
        local_key = script_ref
        download_url = urljoin(base_url, 'scripts/' + script_ref)

    return download_url, local_key


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


def is_remote_source(source: str) -> bool:
    """判断 source 是否为远程 URL。"""
    if not source:
        return False
    return source.startswith('https://') or source.startswith('http://')


def _normalize_source_to_directory(source: str) -> str:
    """将 source 规范化为目录路径（去掉末尾的 adapters.jsonc 文件名）。"""
    if source.endswith('/' + _ADAPTER_FILE):
        source = source[:-len('/' + _ADAPTER_FILE)]
    elif source.endswith(_ADAPTER_FILE):
        source = source[:-len(_ADAPTER_FILE)]
    return source


def resolve_adapter_path(name: str, source: str, adapters_dir: str | None = None) -> str:
    """解析适配器文件路径。

    source 是订阅源目录（包含 adapters.jsonc）的路径，兼容旧格式（包含文件名）。
    - HTTPS URL → adapters/<id>.<name>/adapters.jsonc（本地缓存路径）
    - 本地路径 → <source>/adapters.jsonc
    """
    if adapters_dir is None:
        adapters_dir = _get_adapters_dir_path()

    source = _normalize_source_to_directory(source)

    if is_remote_source(source):
        return os.path.join(adapters_dir, name, _ADAPTER_FILE)
    # 本地目录路径：拼接 adapters.jsonc
    if os.path.isabs(source):
        return os.path.join(source, _ADAPTER_FILE)
    return os.path.normpath(os.path.join(adapters_dir, source, _ADAPTER_FILE))


def _adapter_dir(entry: dict) -> str:
    """从适配器条目计算目录名。"""
    from site_adapters.services.base import _adapter_dir as _base_adapter_dir
    return _base_adapter_dir(entry)


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


def _domain_map(data: dict) -> dict:
    if isinstance(data.get('domains'), dict):
        return data['domains']
    return {
        key: value for key, value in data.items()
        if key not in ('defaults', '_builtin', 'domains') and not key.startswith('_')
    }


def _normalize_domain_config(value):
    if isinstance(value, str):
        return {"type": "alias", "target": value}
    return value


def _materialize_domains(data: dict) -> dict:
    defaults = data.get('defaults', {})
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


# ---------------------------------------------------------------------------
# 脚本收集与缓存
# ---------------------------------------------------------------------------

def _collect_script_refs(data: dict) -> dict[str, list[str]]:
    """收集所有域的脚本引用，按域名分组。

    只扫描 scripts 数组中的 path 字段（不兼容旧的 script 标量）。
    """
    refs: dict[str, list[str]] = {}
    domains = _domain_map(data)
    for domain_key, domain_config in domains.items():
        if not isinstance(domain_config, dict):
            continue
        for section in ('metadata', 'snapshot'):
            section_data = domain_config.get(section)
            if not isinstance(section_data, dict):
                continue
            scripts = section_data.get('scripts')
            if isinstance(scripts, list):
                for entry in scripts:
                    if isinstance(entry, dict):
                        path = entry.get('path', '')
                        if isinstance(path, str) and path:
                            refs.setdefault(domain_key, []).append(path)
    return refs


def _write_adapter_file(file_path: str, url: str, data: dict):
    """将订阅镜像到本地 adapters/<adapter>/。不做任何内容改写。

    adapters.jsonc 保持远端原样（不注入 _meta，不改写脚本路径）。
    scripts/ 目录镜像远端结构。
    """
    sub_dir = os.path.dirname(file_path)
    os.makedirs(sub_dir, exist_ok=True)

    # 下载脚本到 scripts/ 目录（镜像远端结构）
    domain_refs = _collect_script_refs(data)
    if domain_refs:
        scripts_dir = os.path.join(sub_dir, 'scripts')
        # 收集所有将被引用的本地脚本路径
        referenced_paths: set[str] = set()
        for _domain_key, refs in domain_refs.items():
            for script_ref in refs:
                download_url, local_key = _resolve_script_ref(script_ref, url)
                if not download_url or not local_key:
                    continue
                if not _is_safe_script_key(local_key):
                    logger.warning('Unsafe script key: %s', local_key)
                    continue
                target_path = os.path.join(scripts_dir, local_key)
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                try:
                    _validate_download_url(download_url)
                    resp = requests.get(download_url, timeout=15)
                    resp.raise_for_status()
                    new_content = resp.text
                except Exception as e:
                    logger.warning('Failed to download script %s: %s', download_url, e)
                    continue
                new_hash = hashlib.sha256(new_content.encode('utf-8')).hexdigest()
                old_hash = ''
                if os.path.exists(target_path):
                    try:
                        with open(target_path, encoding='utf-8') as f:
                            old_hash = hashlib.sha256(f.read().encode('utf-8')).hexdigest()
                    except OSError:
                        pass
                if new_hash != old_hash:
                    atomic_write(target_path, new_content)
                    logger.info('Script updated: %s', local_key)
                referenced_paths.add(local_key)

        # 清理不再被引用的脚本
        if os.path.isdir(scripts_dir):
            for root, dirs, files in os.walk(scripts_dir, topdown=False):
                for name in files:
                    if name.startswith('.'):
                        continue
                    abs_path = os.path.join(root, name)
                    rel_path = os.path.relpath(abs_path, scripts_dir)
                    if rel_path not in referenced_paths:
                        try:
                            os.remove(abs_path)
                            logger.info('Removed unused script: %s', rel_path)
                        except OSError:
                            pass
                for name in dirs:
                    dir_path = os.path.join(root, name)
                    try:
                        if not os.listdir(dir_path):
                            os.rmdir(dir_path)
                    except OSError:
                        pass

    # 原样写入 adapters.jsonc（不注入 _meta，不改写路径）
    content_str = json.dumps(data, indent=2, ensure_ascii=False)
    atomic_write(file_path, content_str)


# ---------------------------------------------------------------------------
# 文件读取
# ---------------------------------------------------------------------------

def _read_subscription_file(file_path: str) -> dict | None:
    """读取适配器文件。支持 adapters.jsonc 和旧 subscription.jsonc。"""
    if not os.path.exists(file_path):
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


def _get_adapter_cache_path(name: str, adapter_id: str) -> str:
    from site_adapters.services.base import _adapter_dir as _base_adapter_dir
    dir_name = _base_adapter_dir({'id': adapter_id, 'name': name})
    return os.path.join(_get_adapters_dir_path(), dir_name, _ADAPTER_FILE)


def is_allowed_script_path(script_path: str, base_dir: str) -> bool:
    abs_path = os.path.realpath(os.path.abspath(script_path))
    abs_base = os.path.realpath(os.path.abspath(base_dir))
    try:
        return os.path.commonpath([abs_path, abs_base]) == abs_base
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# 单个订阅下载
# ---------------------------------------------------------------------------

def fetch_subscription(url: str, name: str = '', adapter_id: str = '', force: bool = False,
                       update_interval: int = 86400) -> str | None:
    """下载远程订阅源并镜像到本地。

    url 可以是目录路径（自动拼 adapters.jsonc），也可以是直接的 .jsonc 文件 URL。
    状态信息存储在 _meta.json 中，不写入缓存的 adapters.jsonc。

    Returns:
        缓存文件路径；失败时若旧缓存存在也返回该路径；完全失败返回 None。
    """
    # 构建下载 URL、基准 URL、_meta.json key
    meta_key = _normalize_source_to_directory(url)

    if is_remote_source(url):
        if url.endswith('.jsonc'):
            file_url = url
        else:
            file_url = meta_key.rstrip('/') + '/' + _ADAPTER_FILE
        base_url = meta_key
    else:
        base_url = meta_key
        file_url = os.path.join(meta_key, _ADAPTER_FILE)

    # 本地路径：直接返回
    if not is_remote_source(url):
        local_file = resolve_adapter_path(name, base_url) if name else file_url
        if os.path.exists(local_file):
            return local_file
        logger.error("Local adapter file not found: %s", local_file)
        return None

    sub_name = _sub_name(meta_key, name)
    file_path = _get_adapter_cache_path(sub_name, adapter_id or name)

    try:
        validate_subscription_url(file_url)
    except ValueError as exc:
        logger.error(str(exc))
        return None

    if update_interval == 0:
        return file_path

    # 从 _meta.json 读取运行时状态
    meta_entry = _get_meta_entry(meta_key)

    if not force:
        last_fetch = meta_entry.get('last_fetch')
        if last_fetch and time.time() - last_fetch < update_interval:
            return file_path

    try:
        logger.info("Fetching subscription: %s", file_url)

        etag = meta_entry.get('etag', '')
        last_modified = meta_entry.get('last_modified', '')

        data, response_meta = _download_jsonc(file_url, etag=etag, last_modified=last_modified)

        if data is None:
            _update_meta_entry(meta_key, last_fetch=time.time())
            logger.info("Subscription unchanged: %s", meta_key)
            return file_path

        if '_includes' in data:
            data = _resolve_includes(file_url, data, set())

        _write_adapter_file(file_path, meta_key, data)

        update_fields = {
            'last_fetch': time.time(),
            'content_hash': response_meta.get('content_hash', ''),
        }
        if response_meta.get('etag'):
            update_fields['etag'] = response_meta['etag']
        if response_meta.get('last_modified'):
            update_fields['last_modified'] = response_meta['last_modified']
        _update_meta_entry(meta_key, **update_fields)

        cache_key = (meta_key, name)
        _last_fetch_cache[cache_key] = (time.time(), update_interval)

        logger.info("Subscription updated: %s", meta_key)
        return file_path
    except Exception as e:
        logger.error("Subscription fetch failed: %s: %s", meta_key, e)
        _update_meta_entry(meta_key, last_fetch=time.time(), fetch_status='error')
        return file_path if os.path.exists(file_path) else None

def _needs_fetch(sub: dict) -> bool:
    source = sub.get('source', '')
    if not source:
        return False
    if sub.get('enabled') is False:
        return False
    if not is_remote_source(source):
        return False

    now = time.time()
    name = sub.get('name', '')
    interval = sub.get('update_interval', 86400)
    if interval == 0:
        return False
    cache_key = (source, name)

    cached = _last_fetch_cache.get(cache_key)
    if cached is not None:
        cached_fetch, cached_interval = cached
        if cached_interval == interval and now - cached_fetch < interval:
            return False

    adapter_id = sub.get('id', '')
    sub_file = _get_adapter_cache_path(_sub_name(source, name), adapter_id or name)
    if not os.path.exists(sub_file):
        return True

    # 从 _meta.json 获取 last_fetch（key 需规范化为目录 URL）
    meta_key = _normalize_source_to_directory(source)
    meta_entry = _get_meta_entry(meta_key)
    last_fetch = meta_entry.get('last_fetch')
    if last_fetch is None:
        return True
    _last_fetch_cache[cache_key] = (last_fetch, interval)
    return now - last_fetch >= interval


def fetch_all_subscriptions(subscriptions: list[dict]) -> list[str]:
    if not any(_needs_fetch(sub) for sub in subscriptions if isinstance(sub, dict)):
        return []

    paths = []
    for sub in subscriptions:
        if sub.get('enabled') is False:
            continue
        source = sub.get('source')
        if not source:
            continue
        name = sub.get('name', '')
        interval = sub.get('update_interval', 86400) if isinstance(sub, dict) else 86400

        path = fetch_subscription(source, name=name, adapter_id=sub.get('id', ''), update_interval=interval)
        if path:
            paths.append(path)

    return paths
