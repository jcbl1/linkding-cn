"""
浏览器引擎提供者 — 构建期确定唯一引擎，运行期零探测

构建期由 Dockerfile 根据 LD_BROWSER_ENGINE + LD_BROWSER_CLOAKBROWSER_LICENSE_TYPE
下载并固化二进制到镜像内。运行期只读配置启动对应引擎。

Public API:
    get_browser_config()  → dict with engine, binary_path, etc.
    launch_browser()      → Playwright Browser instance
"""

import logging
import os
import shutil

from django.conf import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

def get_browser_config() -> dict:
    """返回当前浏览器引擎配置（供 JS 脚本等外部调用方参考）。"""
    engine = getattr(settings, 'LD_BROWSER_ENGINE', 'cloakbrowser')
    return {
        'engine': engine,
        'license_type': getattr(settings, 'LD_BROWSER_CLOAKBROWSER_LICENSE_TYPE', 'free'),
        'license_key': getattr(settings, 'LD_BROWSER_CLOAKBROWSER_LICENSE_KEY', ''),
        'chromium_path': getattr(settings, 'LD_BROWSER_CHROMIUM_PATH', ''),
    }


# ---------------------------------------------------------------------------
# Chromium 路径发现
# ---------------------------------------------------------------------------

def _find_chromium_path() -> str:
    """查找系统 chromium 可执行路径。"""
    cfg_path = getattr(settings, 'LD_BROWSER_CHROMIUM_PATH', '')
    if cfg_path:
        return cfg_path

    env_path = os.environ.get('CHROMIUM_PATH', '')
    if env_path:
        return env_path

    for path in [
        '/usr/bin/chromium',
        '/usr/bin/chromium-browser',
        '/usr/bin/google-chrome',
        '/usr/bin/google-chrome-stable',
    ]:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    for binary in ['chromium', 'chromium-browser', 'google-chrome']:
        found = shutil.which(binary)
        if found:
            return found

    raise FileNotFoundError(
        'Chromium not found. Install chromium or set LD_BROWSER_CHROMIUM_PATH.'
    )


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------

def launch_browser(headless: bool = True, **kwargs):
    """启动浏览器实例（CloakBrowser 或 Playwright+Chromium）。"""
    engine = getattr(settings, 'LD_BROWSER_ENGINE', 'cloakbrowser')

    if engine == 'cloakbrowser':
        return _launch_cloakbrowser(headless=headless, **kwargs)
    elif engine == 'chromium':
        return _launch_chromium(headless=headless, **kwargs)
    else:
        raise ValueError(f'Unknown LD_BROWSER_ENGINE: {engine!r}')


def _launch_cloakbrowser(headless: bool = True, **kwargs):
    from cloakbrowser import launch
    license_key = getattr(settings, 'LD_BROWSER_CLOAKBROWSER_LICENSE_KEY', '')
    launch_kwargs = dict(headless=headless)
    if license_key:
        launch_kwargs['license_key'] = license_key
    launch_kwargs.update(kwargs)
    return launch(**launch_kwargs)


def _launch_chromium(headless: bool = True, **kwargs):
    from playwright.sync_api import sync_playwright
    exec_path = _find_chromium_path()
    pw = sync_playwright().start()
    launch_args = ['--no-sandbox', '--disable-blink-features=AutomationControlled']
    extra_args = kwargs.pop('args', [])
    launch_args.extend(extra_args)
    browser = pw.chromium.launch(
        headless=headless,
        executable_path=exec_path,
        args=launch_args,
        **kwargs,
    )
    browser.__playwright__ = pw
    return browser
