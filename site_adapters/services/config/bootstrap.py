"""
Runtime defaults adapter bootstrap helpers.

These functions are intentionally view-independent so the config loader and
domain services can self-heal a missing defaults adapter without importing
Django view modules.
"""

import json
import logging
import os
import shutil

from bookmarks.utils import atomic_write
from site_adapters.services.base import _get_adapters_dir, _get_base_dir
from site_adapters.services.config import load_jsonc_file, parse_jsonc
from site_adapters.services.config.jsonc import update_key as _replace_top_level_jsonc_value

logger = logging.getLogger(__name__)


def is_defaults_adapter(adapter) -> bool:
    """Return True only for the canonical built-in defaults adapter."""
    return (
        isinstance(adapter, dict)
        and adapter.get('id') == 'defaults'
        and adapter.get('name') == 'defaults'
    )


def _get_defaults_source_path() -> str:
    """Return the path to the bundled defaults adapter template."""
    return os.path.join(
        os.path.dirname(__file__),
        'adapters',
        'defaults',
        'adapters.jsonc',
    )


def _defaults_adapter_entry() -> dict:
    return {
        'id': 'defaults',
        'name': 'defaults',
        'source': './defaults/adapters.jsonc',
        'update_interval': 86400,
        'enabled': True,
    }


def _default_official_subscription_entry() -> dict:
    """Official standard adapter subscription bundled with new installs."""
    return {
        'id': 'woohoodai',
        'name': 'official-standard',
        'source': 'https://raw.githubusercontent.com/WooHooDai/linkding-cn-adapters/refs/heads/main/src/standard/adapters.jsonc',
        'update_interval': 86400,
        'enabled': True,
    }


def _write_initial_config(adapters_dir: str, config_path: str):
    """Write config.jsonc on first deployment.

    This is only called when config.jsonc does not exist yet, so it is safe
    to seed both the defaults adapter and the official subscription here.
    Once the file exists we never touch the _adapters list again; this is what
    prevents the official subscription from reappearing after a user deletes it.
    """
    default_config = {
        '_adapters': [
            _default_official_subscription_entry(),
            _defaults_adapter_entry(),
        ]
    }
    os.makedirs(adapters_dir, exist_ok=True)
    atomic_write(
        config_path,
        json.dumps(default_config, indent=2, ensure_ascii=False),
    )


def _ensure_defaults_config_entry(adapters_dir: str, config_path: str):
    """Create config.jsonc or append the defaults entry when it is missing."""
    if os.path.exists(config_path):
        try:
            with open(config_path, encoding='utf-8') as f:
                text = f.read()
            data = parse_jsonc(text) if text.strip() else {}
            if not isinstance(data, dict):
                raise ValueError('config.jsonc must be an object')

            adapters = data.get('_adapters', [])
            if not isinstance(adapters, list):
                adapters = []

            has_defaults = any(
                is_defaults_adapter(item) for item in adapters
            )
            if has_defaults:
                return

            adapters.append(_defaults_adapter_entry())
            new_text = _replace_top_level_jsonc_value(text, '_adapters', adapters)
            atomic_write(config_path, new_text)
            logger.info('Appended missing defaults entry to %s', config_path)
        except Exception:
            logger.exception('Failed to repair defaults entry in %s', config_path)
        return

    _write_initial_config(adapters_dir, config_path)


def ensure_defaults_adapter(
    adapters_dir: str,
    source_path: str | None = None,
    sync: bool = True,
) -> str:
    """Ensure the runtime defaults adapter exists and is synced.

    - Creates config.jsonc and appends a defaults entry if missing.
    - Copies the bundled template when the runtime file is missing.
    - On subsequent calls, syncs ``_meta`` and ``_builtin`` from the template
      while preserving user-editable sections.
    """
    source_path = source_path or _get_defaults_source_path()
    config_path = os.path.join(adapters_dir, 'config.jsonc')
    defaults_dir = os.path.join(adapters_dir, 'defaults')
    defaults_file = os.path.join(defaults_dir, 'adapters.jsonc')

    _ensure_defaults_config_entry(adapters_dir, config_path)

    # First deployment, or recovery after the runtime file was deleted.
    if not os.path.exists(defaults_file):
        if not os.path.exists(source_path):
            logger.error('Defaults source template not found: %s', source_path)
            return defaults_file
        os.makedirs(defaults_dir, exist_ok=True)
        shutil.copy2(source_path, defaults_file)
        logger.info('Defaults adapter initialized from source template')
        return defaults_file

    if not os.path.exists(source_path):
        logger.error('Defaults source template not found, skipping sync: %s', source_path)
        return defaults_file

    # Startup/initialization should refresh the bundled builtin. Runtime
    # self-healing calls only need to make sure the file exists.
    if not sync:
        return defaults_file

    try:
        source_data = load_jsonc_file(source_path)
    except Exception as exc:
        logger.error('Failed to read source template: %s: %s', source_path, exc)
        return defaults_file

    try:
        with open(defaults_file, encoding='utf-8') as f:
            text = f.read()
    except OSError as exc:
        logger.error('Failed to read runtime defaults file: %s: %s', defaults_file, exc)
        return defaults_file

    original_text = text

    source_meta = source_data.get('_meta', {})
    if isinstance(source_meta, dict) and source_meta:
        text = _replace_top_level_jsonc_value(text, '_meta', source_meta)

    source_builtin = source_data.get('_builtin', {})
    if isinstance(source_builtin, dict) and source_builtin:
        text = _replace_top_level_jsonc_value(text, '_builtin', source_builtin)

    try:
        current = parse_jsonc(text)
    except Exception:
        current = {}
    if isinstance(current, dict) and '_builtin_overrides' not in current:
        text = _replace_top_level_jsonc_value(text, '_builtin_overrides', {})

    if text != original_text:
        try:
            atomic_write(defaults_file, text)
        except OSError as exc:
            logger.error('Failed to write runtime defaults file: %s: %s', defaults_file, exc)

    return defaults_file


def ensure_base_dirs() -> str:
    """Create the runtime site adapter directories and the defaults adapter."""
    base_dir = _get_base_dir()
    adapters_dir = _get_adapters_dir()
    os.makedirs(adapters_dir, exist_ok=True)
    for name in ('logs', 'test_assets'):
        os.makedirs(os.path.join(base_dir, name), exist_ok=True)

    # On a brand-new install, seed config.jsonc with defaults + the official
    # subscription. Existing config.jsonc is left untouched so user-managed
    # subscriptions, ordering and disabled domains are preserved, and a deleted
    # official subscription does not get re-added.
    config_path = os.path.join(adapters_dir, 'config.jsonc')
    if not os.path.exists(config_path):
        _write_initial_config(adapters_dir, config_path)

    ensure_defaults_adapter(adapters_dir)
    return adapters_dir
