"""
Loader — 域名文件加载 + 合并 + 分源缓存

目录结构：
  data/site_adapters/
  ├── global.jsonc          # 全局默认 + _subscriptions
  └── domains/              # 每个域名一个文件
      ├── *.zhihu.com.jsonc
      └── xhslink.com.jsonc

合并优先级：
  本地 global.jsonc 的 * 默认值（最高）
    > 本地 domains/
      > 订阅源 domains/ > 订阅源 global.jsonc
"""

import copy
import fnmatch
import json
import logging
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from site_adapters.services.config import (
    _resolve_all_paths,
    deep_merge,
    load_jsonc_file,
)
from site_adapters.services.subscriptions import (
    _read_subscription_file,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 分源缓存
# ---------------------------------------------------------------------------

class SourceCache:
    """按源缓存域名配置，通过 mtime 检测变化。

    Thread-safe: all reads and writes to shared state are protected by a lock.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._sources: dict[str, tuple[tuple, dict]] = {}
        self._merged: dict | None = None
        self._sub_order: list[str] = []
        self._last_check: float = 0  # monotonic timestamp of last signature check

    def _load_domains_dir(self, dir_path: str) -> dict:
        """扫描 domains/ 目录，返回 {domain_key: config}。"""
        abs_dir = os.path.abspath(dir_path)
        if not os.path.isdir(abs_dir):
            return {}
        domains = {}
        for fname in os.listdir(abs_dir):
            if not (fname.endswith('.jsonc') or fname.endswith('.json')):
                continue
            fpath = os.path.join(abs_dir, fname)
            try:
                data = load_jsonc_file(fpath)
                if fname.endswith('.jsonc'):
                    domain_key = fname[:-6]
                elif fname.endswith('.json'):
                    domain_key = fname[:-5]
                # 解析相对路径
                file_dir = str(Path(fpath).resolve().parent)
                data = _resolve_all_paths(data, file_dir)
                domains[domain_key] = data
            except (json.JSONDecodeError, OSError) as e:
                logger.error("Failed to parse domain file: %s: %s", fpath, e)
        return domains

    def _path_signature(self, path: str) -> tuple:
        if os.path.isfile(path):
            try:
                st = os.stat(path)
                return (path, st.st_mtime_ns, st.st_size)
            except OSError:
                return (path, 0, 0)
        if not os.path.isdir(path):
            return (path, 0)
        # Full walk
        sig = []
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for name in sorted(files):
                if name.startswith('.'):
                    continue
                fpath = os.path.join(root, name)
                try:
                    st = os.stat(fpath)
                    sig.append((os.path.relpath(fpath, path), st.st_mtime_ns, st.st_size))
                except OSError:
                    pass
        return tuple(sig)

    def _subscription_dir_name(self, sub: dict) -> str:
        from site_adapters.services.subscriptions import _sub_name
        return _sub_name(sub.get('url', ''), sub.get('name', ''))


    # ponytail: 5s throttle on signature checks; upgrade to per-source invalidation if needed
    _CHECK_INTERVAL = 5.0  # seconds

    def load(self, base_dir: str) -> dict:
        """加载并合并所有源，返回完整配置。"""
        now = time.monotonic()
        # Fast path: return cached result if within check interval
        with self._lock:
            if self._merged is not None and (now - self._last_check) < self._CHECK_INTERVAL:
                return self._merged
            self._last_check = now
        changed = False

        # 1. 本地 global.jsonc
        global_path = os.path.join(base_dir, 'global.jsonc')
        global_sig = self._path_signature(global_path)
        if self._sources.get('__global__', ((), {}))[0] != global_sig:
            try:
                global_config = load_jsonc_file(global_path) if os.path.exists(global_path) else {}
            except (json.JSONDecodeError, OSError):
                global_config = {}
            self._sources['__global__'] = (global_sig, global_config)
            changed = True
        global_config = self._sources.get('__global__', ((), {}))[1]

        # 2. 本地 domains/
        local_domains_dir = os.path.join(base_dir, 'domains')
        local_sig = self._path_signature(local_domains_dir)
        if self._sources.get('__local__', ((), {}))[0] != local_sig:
            self._sources['__local__'] = (local_sig, self._load_domains_dir(local_domains_dir))
            changed = True

        # 3. 订阅源：按 global.jsonc 的 _subscriptions 顺序加载
        subs_dir = os.path.join(base_dir, 'subscriptions')
        sub_order = []
        if os.path.isdir(subs_dir):
            for sub in global_config.get('_subscriptions', []):
                if not isinstance(sub, dict):
                    continue
                if sub.get('enabled') is False:
                    continue
                name = self._subscription_dir_name(sub)
                sub_file_path = os.path.join(subs_dir, name, 'subscription.jsonc')

                if not os.path.exists(sub_file_path):
                    continue

                sub_sig = self._path_signature(sub_file_path)
                cache_key = f'sub:{name}'
                sub_order.append(cache_key)
                if self._sources.get(cache_key, (0,))[0] != sub_sig:
                    sub_data = _read_subscription_file(sub_file_path)
                    if sub_data and isinstance(sub_data.get('domains'), dict):
                        sub_global = sub_data.get('*', {})
                        sub_domains = dict(sub_data['domains'])
                        # 解析脚本相对路径（相对于订阅文件所在目录）
                        sub_dir = str(Path(sub_file_path).resolve().parent)
                        sub_domains = {
                            k: _resolve_all_paths(v, sub_dir) if isinstance(v, dict) else v
                            for k, v in sub_domains.items()
                        }
                        # Apply exclude filter
                        exclude = sub.get('exclude', [])
                        if exclude:
                            sub_domains = {
                                k: v for k, v in sub_domains.items()
                                if not any(fnmatch.fnmatch(k, pat) for pat in exclude)
                            }
                        self._sources[cache_key] = (sub_sig, {
                            'global': sub_global if isinstance(sub_global, dict) else {},
                            'domains': sub_domains,
                        })
                        changed = True
        old_sub_keys = [key for key in self._sources if key.startswith('sub:')]
        for key in old_sub_keys:
            if key not in sub_order:
                self._sources.pop(key, None)
                changed = True
        if getattr(self, '_sub_order', []) != sub_order:
            self._sub_order = sub_order
            changed = True

        if changed or self._merged is None:
            with self._lock:
                self._merged = self._merge_all()

        with self._lock:
            return self._merged

    def _merge_all(self) -> dict:
        """按优先级合并所有源。"""
        global_config = self._sources.get('__global__', ((), {}))[1]
        local_domains = self._sources.get('__local__', ((), {}))[1]

        # 合并：从最低优先级开始
        merged_domains = {}

        # 订阅源（从后往前，使靠前的源覆盖靠后的）
        for key in reversed(getattr(self, '_sub_order', [])):
            sub_data = self._sources.get(key, (0, {}))[1]
            sub_global = sub_data.get('global', {})
            for domain_key, domain_config in sub_data.get('domains', {}).items():
                if sub_global:
                    merged_domains[domain_key] = deep_merge(sub_global, domain_config)
                else:
                    merged_domains[domain_key] = domain_config

        # 本地域名（最高优先级，覆盖订阅源）
        for domain_key, domain_config in local_domains.items():
            merged_domains[domain_key] = domain_config

        return {
            '*': global_config.get('*', {}),
            '_subscriptions': global_config.get('_subscriptions', []),
            **merged_domains,
        }

    def invalidate(self):
        with self._lock:
            self._sources.clear()
            self._merged = None
            self._sub_order = []


# 全局缓存实例
_cache = SourceCache()


# ---------------------------------------------------------------------------
# 域名匹配
# ---------------------------------------------------------------------------

def _get_domain(url: str) -> str:
    """Extract hostname (without port) from URL for domain matching."""
    return urlparse(url).hostname or ""


def match_domain(url: str, domain_map: dict) -> tuple[str | None, dict | None]:
    domain = _get_domain(url)
    if not domain:
        return None, None
    # 精确匹配
    if domain in domain_map:
        config = _resolve_alias(domain_map[domain], domain_map)
        if config is not None:
            return domain, config
    # 通配符匹配（最长前缀优先：层级更深的通配符更具体）
    wildcard_keys = sorted(
        [k for k in domain_map if k.startswith('*.')],
        key=lambda k: k.count('.'),
        reverse=True,
    )
    for key in wildcard_keys:
        if domain.endswith(key[1:]):
            config = _resolve_alias(domain_map[key], domain_map)
            if config is not None:
                return key, config
    return None, None


def _resolve_alias(config, domain_map: dict, visited: set | None = None, _depth: int = 0) -> dict | None:
    _MAX_ALIAS_DEPTH = 10
    if not isinstance(config, dict):
        return config
    if config.get('type') != 'alias':
        return config
    target = config.get('target')
    if not target:
        return None
    if visited is None:
        visited = set()
    if target in visited:
        logger.warning("Domain alias cycle detected: %s", target)
        return None
    if _depth >= _MAX_ALIAS_DEPTH:
        logger.warning("Domain alias chain too deep (max %d): %s", _MAX_ALIAS_DEPTH, target)
        return None
    visited.add(target)
    target_config = domain_map.get(target)
    if target_config is None:
        logger.warning("Domain alias target not found: %s", target)
        return None
    return _resolve_alias(target_config, domain_map, visited, _depth + 1)


# ---------------------------------------------------------------------------
# 核心：加载域名配置
# ---------------------------------------------------------------------------

def load_domain_config(url: str, base_dir: str) -> dict | None:
    """
    加载 URL 对应的域名配置。

    返回：
    {
        'auth': {...},
        'default': {...},
        'metadata': {...},
        'snapshot': {...},
        'reader': {...},
        '_domain_key': '...',
        '_raw': {...},
    }
    """
    all_config = _cache.load(base_dir)
    defaults = all_config.get('*', {})

    domain_key, domain_config = match_domain(url, all_config)
    if domain_config is None:
        return None

    # Check if domain is disabled
    disabled = all_config.get('*', {}).get('_disabled_domains', [])
    if domain_key in disabled:
        return None

    # 设计文档要求本地 global.jsonc 的 "*" 最高优先级。
    merged = deep_merge(domain_config, defaults) if defaults else copy.deepcopy(domain_config)

    result = copy.deepcopy(merged)
    result['_domain_key'] = domain_key
    result['_raw'] = copy.deepcopy(domain_config)
    return result


# ---------------------------------------------------------------------------
# 展示配置
# ---------------------------------------------------------------------------

def show_config(url: str, base_dir: str) -> dict:
    all_config = _cache.load(base_dir)
    defaults = all_config.get('*', {})
    domain_key, domain_config = match_domain(url, all_config)
    if domain_config is None:
        return {'error': f'无匹配域名配置: {url}', 'domain': _get_domain(url)}
    merged = deep_merge(domain_config, defaults) if defaults else copy.deepcopy(domain_config)
    return {
        'url': url,
        'domain': _get_domain(url),
        'domain_key': domain_key,
        'defaults': defaults,
        'raw_config': domain_config,
        'merged': merged,
    }
