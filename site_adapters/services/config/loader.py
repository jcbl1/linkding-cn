"""
Loader — 适配器配置加载 + 合并 + 分源缓存

目录结构：
  data/site_adapters/
    adapters/
      config.jsonc            # _adapters 列表：声明所有适配器及优先级
      defaults/
        defaults.jsonc         # 最高优先级适配器
        scripts/
      fivefilters/
        adapters.jsonc
        scripts/
      ...

config.jsonc 格式：
{
  "_adapters": [
    {"name": "defaults"},
    {"name": "rsshub", "source": "https://...", "interval": 86400, "enabled": true},
    {"name": "my-local", "source": "./path/to/adapters.jsonc"}
  ]
}

- name: 适配器目录名（相对于 adapters/）
- source: 文件来源（https:// 远程 / 本地路径 / 省略 = adapters/<name>/adapters.jsonc）
- interval: 远程源更新间隔（秒），默认 86400
- enabled: 是否启用，默认 true

合并优先级：
  _adapters 数组顺序 = 优先级（第一个最高）
  → 第一个适配器的 "*" 作为全局覆盖（最高优先级）
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

from site_adapters.services.base import _get_adapters_dir
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
# 适配器文件名
# ---------------------------------------------------------------------------

_ADAPTER_FILE = 'adapters.jsonc'
_CONFIG_FILE = 'config.jsonc'


# ---------------------------------------------------------------------------
# 分源缓存
# ---------------------------------------------------------------------------

class SourceCache:
    """按源缓存域名配置，通过 mtime 检测变化。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._sources: dict[str, tuple[tuple, dict]] = {}
        self._merged: dict | None = None
        self._adapter_order: list[str] = []
        self._first_adapter_name: str | None = None
        self._last_check: float = 0

    def _path_signature(self, path: str) -> tuple:
        if os.path.isfile(path):
            try:
                st = os.stat(path)
                return (path, st.st_mtime_ns, st.st_size)
            except OSError:
                return (path, 0, 0)
        if not os.path.isdir(path):
            return (path, 0)
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

    def _resolve_adapter_path(self, entry: dict, adapters_dir: str) -> str:
        """解析适配器文件路径。

        entry: {"id": "...", "name": "...", "source": "..."}
        
        - 有 source 且是远程 URL → adapters/{id}.{name}/adapters.jsonc
        - 有 source 且是本地路径 → 直接用该路径
        - 无 source → adapters/{id}.{name}/adapters.jsonc（有id）
                        adapters/{name}/adapters.jsonc（无id）
        """
        from site_adapters.services.base import _adapter_dir
        name = entry.get('name', '') if isinstance(entry, dict) else ''
        source = entry.get('source') if isinstance(entry, dict) else None
        dir_name = _adapter_dir(entry) if isinstance(entry, dict) else name
        
        if source:
            if source.startswith('https://') or source.startswith('http://'):
                return os.path.join(adapters_dir, dir_name, _ADAPTER_FILE)
            if os.path.isabs(source):
                return source
            return os.path.normpath(os.path.join(adapters_dir, source))
        return os.path.join(adapters_dir, dir_name, _ADAPTER_FILE)

    def _load_adapter_file(self, file_path: str) -> dict:
        """加载单个适配器文件，返回 {"*": ..., "domains": {...}}。

        查找逻辑：
        1. 如果传入路径直接存在 → 使用它
        2. 如果目录存在，尝试 adapters.jsonc → <dirname>.jsonc
        """
        if os.path.exists(file_path):
            pass  # 文件存在，直接使用
        elif os.path.isdir(os.path.dirname(file_path)) or os.path.isdir(file_path):
            # 尝试同目录下的 adapters.jsonc
            dir_path = file_path if os.path.isdir(file_path) else os.path.dirname(file_path)
            candidates = [
                os.path.join(dir_path, 'adapters.jsonc'),
                os.path.join(dir_path, os.path.basename(dir_path) + '.jsonc'),
            ]
            file_path = None
            for c in candidates:
                if os.path.exists(c):
                    file_path = c
                    break
            if file_path is None:
                logger.warning("Adapter file not found in: %s", dir_path)
                return {'*': {}, 'domains': {}}
        try:
            data = load_jsonc_file(file_path)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to parse adapter file: %s: %s", file_path, e)
            return {'*': {}, 'domains': {}}
        if not isinstance(data, dict):
            logger.error("Adapter file top-level must be an object: %s", file_path)
            return {'*': {}, 'domains': {}}

        # 提取 "*" 和 domains
        glob_defaults = data.get('*', {})
        if not isinstance(glob_defaults, dict):
            glob_defaults = {}

        domains_raw = data.get('domains', {})
        if isinstance(domains_raw, dict):
            domains = dict(domains_raw)
        else:
            # 兼容：没有 "domains" 键时，所有非元数据键视为域名
            domains = {
                k: v for k, v in data.items()
                if k not in ('*', 'domains', '_meta') and not k.startswith('_')
            }

        # 解析脚本相对路径
        file_dir = str(Path(file_path).resolve().parent)
        domains = {
            k: _resolve_all_paths(v, file_dir) if isinstance(v, dict) else v
            for k, v in domains.items()
        }

        return {'*': glob_defaults, 'domains': domains}

    _CHECK_INTERVAL = 5.0

    def load(self, base_dir: str) -> dict:
        """加载并合并所有适配器，返回完整配置。"""
        now = time.monotonic()
        with self._lock:
            if self._merged is not None and (now - self._last_check) < self._CHECK_INTERVAL:
                return self._merged
            self._last_check = now

        adapters_dir = _get_adapters_dir()
        config_path = os.path.join(adapters_dir, _CONFIG_FILE)
        changed = False

        # 1. 读取 config.jsonc
        config_sig = self._path_signature(config_path)
        if self._sources.get('__config__', ((), {}))[0] != config_sig:
            try:
                config_data = load_jsonc_file(config_path) if os.path.exists(config_path) else {}
            except (json.JSONDecodeError, OSError):
                config_data = {}
            self._sources['__config__'] = (config_sig, config_data)
            changed = True
        config_data = self._sources.get('__config__', ((), {}))[1]

        adapters_list = config_data.get('_adapters', [])
        if not isinstance(adapters_list, list):
            adapters_list = []

        # 过滤出 enabled 的适配器
        enabled_adapters = []
        for item in adapters_list:
            if not isinstance(item, dict):
                continue
            if item.get('enabled') is False:
                continue
            enabled_adapters.append(item)

        # 2. 加载每个适配器
        new_order = []
        for item in enabled_adapters:
            name = item.get('name', '')
            if not name:
                continue
            file_path = self._resolve_adapter_path(item, adapters_dir)

            cache_key = f'adapter:{name}'
            new_order.append(cache_key)

            sig = self._path_signature(file_path)
            if self._sources.get(cache_key, (0,))[0] != sig:
                adapter_data = self._load_adapter_file(file_path)
                # 应用 exclude 过滤
                exclude = item.get('exclude', [])
                if exclude and adapter_data.get('domains'):
                    adapter_data['domains'] = {
                        k: v for k, v in adapter_data['domains'].items()
                        if not any(fnmatch.fnmatch(k, pat) for pat in exclude)
                    }
                self._sources[cache_key] = (sig, adapter_data)
                changed = True

        # 清理移除的适配器
        old_keys = [k for k in self._sources if k.startswith('adapter:')]
        for key in old_keys:
            if key not in new_order:
                self._sources.pop(key, None)
                changed = True

        if self._adapter_order != new_order:
            self._adapter_order = new_order
            changed = True
        self._first_adapter_name = new_order[0] if new_order else None

        if changed or self._merged is None:
            with self._lock:
                self._merged = self._merge_all()

        with self._lock:
            return self._merged

    def _merge_all(self) -> dict:
        """按优先级合并所有适配器。

        规则：
        1. _adapters 顺序 = 优先级（第一个最高）
        2. 同一适配器内：domain 配置覆盖 "*"（"*" 是内部基准）
        3. 跨适配器：靠前覆盖靠后
        4. 第一个适配器的 "*" 作为全局覆盖（查询时通过 load_domain_config 应用）
        """
        merged_domains = {}

        # 从后往前合并（靠前的最后覆盖）
        for cache_key in reversed(self._adapter_order):
            adapter_data = self._sources.get(cache_key, (0, {'*': {}, 'domains': {}}))[1]
            glob_defaults = adapter_data.get('*', {})
            domains = adapter_data.get('domains', {})

            for domain_key, domain_config in domains.items():
                if not isinstance(domain_config, dict):
                    merged_domains[domain_key] = domain_config
                    continue
                # 同一适配器内：domain 覆盖 "*"
                if glob_defaults:
                    merged_domains[domain_key] = deep_merge(
                        copy.deepcopy(glob_defaults),
                        domain_config,
                    )
                else:
                    merged_domains[domain_key] = copy.deepcopy(domain_config)

        return {
            '_adapters': [
                item for item in (
                    self._sources.get('__config__', ((), {}))[1].get('_adapters', [])
                    if isinstance(self._sources.get('__config__', ((), {}))[1].get('_adapters'), list)
                    else []
                )
            ],
            '_first_adapter': self._first_adapter_name,
            **merged_domains,
        }

    def invalidate(self):
        with self._lock:
            self._sources.clear()
            self._merged = None
            self._adapter_order = []
            self._first_adapter_name = None


_cache = SourceCache()


# ---------------------------------------------------------------------------
# 域名匹配
# ---------------------------------------------------------------------------

def _get_domain(url: str) -> str:
    return urlparse(url).hostname or ""


def match_domain(url: str, domain_map: dict) -> tuple[str | None, dict | None]:
    domain = _get_domain(url)
    if not domain:
        return None, None
    if domain in domain_map:
        config = _resolve_alias(domain_map[domain], domain_map)
        if config is not None:
            return domain, config
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

    # 获取第一个适配器的 "*" 作为全局覆盖
    first_adapter_name = all_config.get('_first_adapter')
    global_override = {}
    disabled_domains = []
    if first_adapter_name:
        first_data = _cache._sources.get(first_adapter_name, (0, {'*': {}, 'domains': {}}))[1]
        first_glob = first_data.get('*', {})
        if isinstance(first_glob, dict):
            global_override = copy.deepcopy(first_glob)
            disabled_domains = first_glob.get('_disabled_domains', [])
            if not isinstance(disabled_domains, list):
                disabled_domains = []
            # 清理内部字段
            global_override.pop('_disabled_domains', None)

    domain_key, domain_config = match_domain(url, all_config)
    if domain_config is None:
        return None

    # 检查域名是否被禁用
    if domain_key in disabled_domains:
        return None

    # 合并：全局覆盖（第一个适配器的 "*"）> 域名特定配置
    merged = deep_merge(domain_config, global_override) if global_override else copy.deepcopy(domain_config)

    result = copy.deepcopy(merged)
    result['_domain_key'] = domain_key
    result['_raw'] = copy.deepcopy(domain_config)
    return result


# ---------------------------------------------------------------------------
# 展示配置
# ---------------------------------------------------------------------------

def show_config(url: str, base_dir: str) -> dict:
    all_config = _cache.load(base_dir)

    first_adapter_name = all_config.get('_first_adapter')
    global_override = {}
    if first_adapter_name:
        first_data = _cache._sources.get(first_adapter_name, (0, {'*': {}, 'domains': {}}))[1]
        first_glob = first_data.get('*', {})
        if isinstance(first_glob, dict):
            global_override = copy.deepcopy(first_glob)
            global_override.pop('_disabled_domains', None)

    domain_key, domain_config = match_domain(url, all_config)
    if domain_config is None:
        return {'error': f'无匹配域名配置: {url}', 'domain': _get_domain(url)}

    merged = deep_merge(domain_config, global_override) if global_override else copy.deepcopy(domain_config)
    return {
        'url': url,
        'domain': _get_domain(url),
        'domain_key': domain_key,
        'defaults': global_override,
        'raw_config': domain_config,
        'merged': merged,
    }
