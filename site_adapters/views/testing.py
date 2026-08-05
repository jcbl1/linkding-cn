"""
Test panel: config/metadata/snapshot/reader/cookie/pipeline tests + validation.
"""
import json
import logging
import os

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from bookmarks.services.website_loader import (
    load_website_metadata_for_test,
)
from bookmarks.utils import is_safe_domain_key
from site_adapters.services.auth.cookies import (
    load_cookie_file,
    save_cookie_for_domain,
    verify_and_refresh,
)
from site_adapters.services.config.loader import _cache, match_domain, show_config
from site_adapters.services.config.resolver import (
    get_metadata_config,
    get_reader_config,
    get_snapshot_config,
)
from site_adapters.services.config.validator import validate_config
from site_adapters.services.execution_log import collect_executions
from site_adapters.views.helpers import (
    TEST_ASSETS_DIR,
    _get_base_dir,
    _sanitize_url_for_filename,
    site_adapters_required,
)

logger = logging.getLogger(__name__)

def _timestamp() -> str:
    """返回当前时间戳字符串，遵循 TIME_ZONE 设置。"""
    from zoneinfo import ZoneInfo
    tz_name = getattr(settings, 'TIME_ZONE', 'UTC')
    tz = ZoneInfo(tz_name)
    return timezone.now().astimezone(tz).strftime('%Y%m%d%H%M%S')

@site_adapters_required
@require_POST
def action(request):
    """处理测试请求。"""
    act = request.POST.get('action', '')
    if act == 'test':
        try:
            return _handle_test(request)
        except Exception as exc:
            logger.exception("Site adapter test failed")
            return JsonResponse({
                'type': request.POST.get('test_type', 'test'),
                'error': str(exc),
            })
    elif act == 'validate':
        return _handle_validate(request)
    elif act == 'clean_test_files':
        return _handle_clean_test_files()
    return JsonResponse({'error': 'unknown action'}, status=400)


def _handle_validate(request) -> JsonResponse:
    base_dir = _get_base_dir()
    filename = request.POST.get('filename', '')
    if filename:
        try:
            _resolve_domain_path(filename)
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=400)
    issues = validate_config(base_dir, domain_filename=filename)
    return JsonResponse({'issues': issues})


# ---------------------------------------------------------------------------
# Test/verification (metadata, snapshot, reader, cookie, pipeline)
# ---------------------------------------------------------------------------

def _test_response(data, entries=None, **kwargs):
    """包装测试响应，附加 collected execution entries."""
    if entries:
        data['executions'] = entries
    return JsonResponse(data, **kwargs)


def _handle_test(request) -> JsonResponse:
    url = request.POST.get('url', '').strip()
    test_type = request.POST.get('test_type', 'config')

    if not url:
        return JsonResponse({'error': 'URL required'}, status=400)

    base_dir = _get_base_dir()
    username = request.POST.get('test_username', '').strip()

    handlers = {
        'config': _test_config,
        'metadata': _test_metadata,
        'snapshot': _test_snapshot,
        'reader': _test_reader,
        'cookie': _test_cookie,
        'pipeline': _test_pipeline,
    }
    handler = handlers.get(test_type)
    if not handler:
        return _test_response({'error': f'Unknown test type: {test_type}'}, status=400)

    with collect_executions() as entries:
        return handler(url, base_dir, username, entries)


def _test_config(url, base_dir, username, entries):
    result = show_config(url, base_dir)
    return _test_response({'type': 'config', 'result': result}, entries=entries)


def _make_default_metadata_config():
    """为无匹配域名的场景构建内置引擎默认参数。"""
    return {
        '_engine': 'built-in',
        'timeout': 10,
        'chunk_size': 50 * 1024,
        'proxy': None,
        'headers': {},
        'script': None,
        'select_title': None,
        'select_description': None,
        'select_image': None,
        'request_url': None,
        'rewrite_url': None,
    }


def _make_default_snapshot_config():
    """为无匹配域名的场景构建内置快照引擎默认参数。"""
    from django.conf import settings as django_settings
    return {
        '_engine': 'built-in (SingleFile)',
        'script': None,
        'process_lazy_images': None,
        'remove_classes': None,
        'set_styles': None,
        'singlefile_args': {},
        'singlefile_path': getattr(django_settings, 'LD_SINGLEFILE_PATH', 'single-file'),
        'singlefile_timeout': getattr(django_settings, 'LD_SINGLEFILE_TIMEOUT_SEC', 60),
        'user_agent': getattr(django_settings, 'LD_DEFAULT_USER_AGENT', ''),
        'headers': {},
        'proxy': None,
        'request_url': None,
    }


def _make_default_reader_config():
    """为无匹配域名的场景构建内置阅读器引擎默认参数。"""
    return {
        '_engine': 'built-in (defuddle)',
        'script': None,
        'defuddle_args': {},
    }



def _extract_match_info(config):
    """从 config 中提取匹配信息。"""
    if not config:
        return {
            'matched': False,
            'domain_key': None,
            'adapter': None,
        }
    return {
        'matched': True,
        'domain_key': config.get('_domain_key'),
        'adapter': config.get('_adapter'),
    }


def _test_metadata(url, base_dir, username, entries):
    config = get_metadata_config(url, username=username)
    no_match = False
    if not config:
        no_match = True
    metadata, sources, config = load_website_metadata_for_test(url, username=username)
    match_info = _extract_match_info(config)
    result = {
        'type': 'metadata',
        'no_match': no_match,
        'matched': match_info['matched'],
        'domain_key': match_info['domain_key'],
        'adapter': match_info['adapter'],
        'config': config,
        'original_url': url,
        'request_url': config.get('_request_url', url) if config else url,
        'result': metadata.to_dict(),
        'sources': sources,
    }
    if no_match:
        result['default_config'] = _make_default_metadata_config()
    return _test_response(result, entries=entries)


def _test_snapshot(url, base_dir, username, entries):
    config = get_snapshot_config(url, username=username)
    no_match = False
    if not config:
        no_match = True
    from bookmarks.services.snapshot_processor import create_snapshot
    os.makedirs(TEST_ASSETS_DIR, exist_ok=True)
    filename = 'snapshot_' + _timestamp() + '_' + _sanitize_url_for_filename(url) + '.html'
    out_path = os.path.join(TEST_ASSETS_DIR, filename)
    create_snapshot(url, out_path, username=username)
    match_info = _extract_match_info(config)
    result = {
        'type': 'snapshot',
        'no_match': no_match,
        'matched': match_info['matched'],
        'domain_key': match_info['domain_key'],
        'adapter': match_info['adapter'],
        'config': config,
        'original_url': url,
        'request_url': config.get('_request_url', url) if config else url,
        'result': {
            'file': filename,
            'size': os.path.getsize(out_path),
            'view_url': f'/admin/site-adapters/view-snapshot?file={filename}',
        },
    }
    if no_match:
        result['default_config'] = _make_default_snapshot_config()
    return _test_response(result, entries=entries)


def _test_reader(url, base_dir, username, entries):
    config = get_reader_config(url, username=username)
    no_match = False
    if not config:
        no_match = True
    from bookmarks.services import reader_processor
    from bookmarks.services.snapshot_processor import create_snapshot
    os.makedirs(TEST_ASSETS_DIR, exist_ok=True)
    snap_filename = 'snapshot_' + _timestamp() + '_' + _sanitize_url_for_filename(url) + '.html'
    snap_path = os.path.join(TEST_ASSETS_DIR, snap_filename)
    create_snapshot(url, snap_path, username=username)
    with open(snap_path, encoding='utf-8') as f:
        html = f.read()
    result = reader_processor.parse_html(html, url=url, username=username)
    reader_html = result.get('content', '')
    reader_filename = 'article_' + _timestamp() + '_' + _sanitize_url_for_filename(url) + '.html'
    reader_path = os.path.join(TEST_ASSETS_DIR, reader_filename)
    with open(reader_path, 'w', encoding='utf-8') as f:
        f.write(reader_html)
    match_info = _extract_match_info(config)
    result_data = {
        'type': 'reader',
        'no_match': no_match,
        'matched': match_info['matched'],
        'domain_key': match_info['domain_key'],
        'adapter': match_info['adapter'],
        'config': config,
        'original_url': url,
        'request_url': config.get('_request_url', url) if config else url,
        'result': {
            'title': result.get('title', ''),
            'word_count': result.get('wordCount', 0),
            'html_size': os.path.getsize(reader_path),
            'view_url': f'/admin/site-adapters/view-snapshot?file={reader_filename}',
            'snapshot_size': os.path.getsize(snap_path),
            'snapshot_view_url': f'/admin/site-adapters/view-snapshot?file={snap_filename}',
        },
        'defuddle_args': config.get('defuddle_args') if config else None,
    }
    if no_match:
        result_data['default_config'] = _make_default_reader_config()
    return _test_response(result_data, entries=entries)


def _test_cookie(url, base_dir, username, entries):
    from site_adapters.services.auth.cookies import get_cookie_for_domain
    from site_adapters.services.auth.credentials import get_user_cookie
    all_config = {}
    try:
        all_config = _cache.load(base_dir)
    except (json.JSONDecodeError, OSError):
        pass
    domain_key, _ = match_domain(url, all_config)
    if not domain_key:
        return _test_response({'type': 'cookie', 'error': '无匹配域名'}, entries=entries)

    # 1) Check global cookie (shared across users, from cookies/ directory)
    global_cookie = get_cookie_for_domain(domain_key)
    has_global_cookie = bool(global_cookie)

    # 2) Check user credential cookie (per-user, highest priority)
    user_cookie_str = None
    if username:
        user_cookie_str, _ = get_user_cookie(username, domain_key)
    has_user_cookie = bool(user_cookie_str)

    # Combined: user credential takes precedence
    cookie = user_cookie_str if user_cookie_str else global_cookie
    has_cookie = has_global_cookie or has_user_cookie
    cookie_preview = cookie[:50] + '...' if cookie and len(cookie) > 50 else (cookie or '')

    # Get config for refresh info
    metadata_config = get_metadata_config(url, username=username) or {}
    snapshot_config = get_snapshot_config(url, username=username) or {}
    config = metadata_config
    if snapshot_config.get('cookie', {}).get('file') and not metadata_config.get('cookie', {}).get('file'):
        config = snapshot_config
    cookie_config = config.get('cookie', {})

    refreshed = False
    if cookie_config and cookie:
        before = cookie
        after = verify_and_refresh(cookie_config, url, domain_key, {'url': url, 'status': 0, 'title': '', 'body_preview': ''})
        refreshed = bool(after and after != before)
        if refreshed:
            # Re-read after refresh
            global_cookie = get_cookie_for_domain(domain_key)
            if username:
                user_cookie_str, _ = get_user_cookie(username, domain_key)
            cookie = user_cookie_str if user_cookie_str else global_cookie
            has_cookie = bool(cookie)
            if has_cookie:
                cookie_preview = cookie[:50] + '...' if cookie and len(cookie) > 50 else cookie

    return _test_response({
        'type': 'cookie',
        'domain_key': domain_key,
        'has_cookie': has_cookie,
        'has_global_cookie': has_global_cookie,
        'has_user_cookie': has_user_cookie,
        'cookie_preview': cookie_preview,
        'refreshed': refreshed,
    }, entries=entries)


def _test_pipeline(url, base_dir, username, entries):
    from bookmarks.services import reader_processor
    from bookmarks.services.snapshot_processor import create_snapshot
    config_result = show_config(url, base_dir)
    meta_config = get_metadata_config(url, username=username)
    snap_config = get_snapshot_config(url, username=username)
    reader_config = get_reader_config(url, username=username)
    metadata, sources, _ = load_website_metadata_for_test(url, username=username)
    os.makedirs(TEST_ASSETS_DIR, exist_ok=True)
    snap_filename = 'snapshot_' + _timestamp() + '_' + _sanitize_url_for_filename(url) + '.html'
    snap_path = os.path.join(TEST_ASSETS_DIR, snap_filename)
    create_snapshot(url, snap_path, username=username)
    with open(snap_path, encoding='utf-8') as f:
        html = f.read()
    article = reader_processor.parse_html(html, url=url, username=username)
    reader_html = article.get('content', '')
    reader_filename = 'article_' + _timestamp() + '_' + _sanitize_url_for_filename(url) + '.html'
    reader_path = os.path.join(TEST_ASSETS_DIR, reader_filename)
    with open(reader_path, 'w', encoding='utf-8') as f:
        f.write(reader_html)
    metadata_no_match = not meta_config
    snapshot_no_match = not snap_config
    reader_no_match = not reader_config
    md_match = _extract_match_info(meta_config)
    snap_match = _extract_match_info(snap_config)
    rd_match = _extract_match_info(reader_config)
    result = {
        'type': 'pipeline',
        'config': config_result,
        'metadata': {
            'config': meta_config,
            'matched': md_match['matched'],
            'domain_key': md_match['domain_key'],
            'adapter': md_match['adapter'],
            'no_match': metadata_no_match,
            'original_url': url,
            'request_url': meta_config.get('_request_url', url) if meta_config else url,
            'result': metadata.to_dict(),
            'sources': sources,
        },
        'snapshot': {
            'config': snap_config,
            'matched': snap_match['matched'],
            'domain_key': snap_match['domain_key'],
            'adapter': snap_match['adapter'],
            'no_match': snapshot_no_match,
            'original_url': url,
            'request_url': snap_config.get('_request_url', url) if snap_config else url,
            'result': {
                'file': snap_filename,
                'size': os.path.getsize(snap_path),
                'view_url': f'/admin/site-adapters/view-snapshot?file={snap_filename}',
            },
        },
        'reader': {
            'config': reader_config,
            'matched': rd_match['matched'],
            'domain_key': rd_match['domain_key'],
            'adapter': rd_match['adapter'],
            'no_match': reader_no_match,
            'original_url': url,
            'request_url': reader_config.get('_request_url', url) if reader_config else url,
            'result': {
                'title': article.get('title', ''),
                'word_count': article.get('wordCount', 0),
                'html_size': os.path.getsize(reader_path),
                'view_url': f'/admin/site-adapters/view-snapshot?file={reader_filename}',
                'snapshot_size': os.path.getsize(snap_path),
                'snapshot_view_url': f'/admin/site-adapters/view-snapshot?file={snap_filename}',
            },
            'defuddle_args': reader_config.get('defuddle_args') if reader_config else None,
        },
    }
    if metadata_no_match:
        result['metadata']['default_config'] = _make_default_metadata_config()
    if snapshot_no_match:
        result['snapshot']['default_config'] = _make_default_snapshot_config()
    if reader_no_match:
        result['reader']['default_config'] = _make_default_reader_config()
    return _test_response(result, entries=entries)


def _handle_clean_test_files() -> JsonResponse:
    test_dir = TEST_ASSETS_DIR
    if not os.path.isdir(test_dir):
        return JsonResponse({'success': True, 'deleted': 0})
    count = 0
    for f in os.listdir(test_dir):
        fpath = os.path.join(test_dir, f)
        if os.path.isfile(fpath):
            os.remove(fpath)
            count += 1
    return JsonResponse({'success': True, 'deleted': count})


# ---------------------------------------------------------------------------
# Cookie 操作
# ---------------------------------------------------------------------------

@site_adapters_required
@require_POST
def save_cookie(request):
    """保存 cookie。"""
    domain_key = request.POST.get('domain_key', '')
    cookie_str = request.POST.get('cookie', '')
    if not domain_key:
        return JsonResponse({'error': 'domain_key required'}, status=400)
    if not is_safe_domain_key(domain_key):
        return JsonResponse({'error': 'invalid domain key'}, status=400)
    save_cookie_for_domain(domain_key, cookie_str, source='paste')
    return JsonResponse({'success': True})


