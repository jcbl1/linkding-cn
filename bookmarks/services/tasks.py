"""
异步任务模块

集中管理所有 Huey 异步任务，按功能分为以下几大类：
  1. 通用任务工具（优先级、队列管理）
  2. Web Archive（Wayback Machine）快照
  3. Favicon 加载与刷新
  4. 预览图加载
  5. 元数据补全与刷新
  6. HTML 快照生成（含域级冷却调度器）
  7. 文章提取（阅读模式，defuddle 解析）
"""

import functools
import gzip
import logging
import os
import random
import time
from collections.abc import Callable
from datetime import timedelta

import waybackpy
from django.conf import settings
from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone
from huey import crontab
from huey.contrib.djhuey import HUEY as huey
from huey.exceptions import TaskLockedException
from waybackpy.exceptions import TooManyRequestsError, WaybackError

from bookmarks.models import Bookmark, BookmarkAsset, UserProfile
from bookmarks.services import assets, favicon_loader, preview_image_loader
from bookmarks.services.website_loader import load_website_metadata
from bookmarks.utils import get_matching_domain_roots, get_registrable_domain, parse_domain_roots

logger = logging.getLogger(__name__)
HTML_SNAPSHOT_DISPATCHER_LOCK = huey.lock_task("html-snapshot-dispatcher-lock")


# ---------------------------------------------------------------------------
# 通用任务工具
# ---------------------------------------------------------------------------

# 自定义 Huey 任务装饰器，实现指数退避重试策略
# 参考: https://huey.readthedocs.io/en/latest/guide.html#tips-and-tricks
# 退避序列: 60 → 240 → 960 → 3840 → 15360 秒

def task(retries=5, retry_delay=15, retry_backoff=4):
    def deco(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            task = kwargs.pop("task", None)
            try:
                return fn(*args, **kwargs)
            except TaskLockedException as exc:
                # Task locks are currently only used as workaround to enforce
                # running specific types of tasks (e.g. singlefile snapshots)
                # sequentially. In that case don't reduce the number of retries.
                if task is not None:
                    task.retries = retries
                raise exc
            except Exception as exc:
                if task is not None:
                    task.retry_delay *= retry_backoff
                raise exc

        return huey.task(retries=retries, retry_delay=retry_delay, context=True)(inner)

    return deco


# ---------------------------------------------------------------------------
# Web Archive（Wayback Machine）快照
# ---------------------------------------------------------------------------


def is_web_archive_integration_active(user: User) -> bool:
    background_tasks_enabled = not settings.LD_DISABLE_BACKGROUND_TASKS
    web_archive_integration_enabled = (
        user.profile.web_archive_integration
        == UserProfile.WEB_ARCHIVE_INTEGRATION_ENABLED
    )

    return background_tasks_enabled and web_archive_integration_enabled


def create_web_archive_snapshot(user: User, bookmark: Bookmark, force_update: bool):
    if is_web_archive_integration_active(user):
        _create_web_archive_snapshot_task(bookmark.id, force_update)


def _create_wayback_snapshot(bookmark: Bookmark):
    logger.info(f"Create new snapshot for bookmark. url={bookmark.url}...")
    archive = waybackpy.WaybackMachineSaveAPI(
        bookmark.url, settings.LD_DEFAULT_USER_AGENT, max_tries=1
    )
    archive.save()
    bookmark.web_archive_snapshot_url = archive.archive_url
    bookmark.save(update_fields=["web_archive_snapshot_url"])
    logger.info(f"Successfully created new snapshot for bookmark:. url={bookmark.url}")


@task()
def _create_web_archive_snapshot_task(bookmark_id: int, force_update: bool):
    try:
        bookmark = Bookmark.objects.get(id=bookmark_id)
    except Bookmark.DoesNotExist:
        return

    # Skip if snapshot exists and update is not explicitly requested
    if bookmark.web_archive_snapshot_url and not force_update:
        return

    # Create new snapshot
    try:
        _create_wayback_snapshot(bookmark)
        return
    except TooManyRequestsError:
        logger.error(
            f"Failed to create snapshot due to rate limiting. url={bookmark.url}"
        )
    except WaybackError as error:
        logger.error(
            f"Failed to create snapshot. url={bookmark.url}",
            exc_info=error,
        )


# ---------------------------------------------------------------------------
# Favicon 加载与刷新（域名级）
# ---------------------------------------------------------------------------


def is_favicon_feature_active(user: User) -> bool:
    background_tasks_enabled = not settings.LD_DISABLE_BACKGROUND_TASKS
    return background_tasks_enabled and user.profile.enable_favicons


def _resolve_domain(url: str, domain_config=None) -> str:
    """从 URL 提取 hostname 并应用自定义域名归一化。"""
    from bookmarks.utils import extract_hostname, resolve_favicon_domain
    hostname = extract_hostname(url)
    if not hostname:
        return ""
    return resolve_favicon_domain(hostname, config=domain_config)


def ensure_favicon(user: User, url: str):
    """确保指定 URL 的域名有 favicon。

    策略：
    - 磁盘有文件 → 同步 DB 记录，过期则后台静默刷新
    - 磁盘无文件但 DB 有缓存 → 按状态处理（pending 等待/failed 到期重试/missing 不重试）
    - 无任何缓存 → 入队获取任务

    stale-while-revalidate：旧缓存在新缓存下载成功前保留，用户始终能看到图标。
    """
    if not is_favicon_feature_active(user):
        return

    domain_config = parse_domain_roots(user.profile.custom_domain_root)
    domain = _resolve_domain(url, domain_config)
    if not domain:
        return

    from bookmarks.models import FaviconCache

    # 1. 先查 DB（轻量，避免不必要的 os.listdir）
    cache = FaviconCache.objects.filter(domain=domain).first()

    if cache and cache.status == FaviconCache.STATUS_SUCCESS and cache.favicon_file:
        # DB 有记录 → 验证磁盘文件（isfile 比 os.listdir 快得多）
        if favicon_loader._get_favicon_path(cache.favicon_file).is_file():
            if cache.fetched_at:
                stale_threshold = timezone.now() - timedelta(days=1)
                if cache.fetched_at < stale_threshold:
                    _enqueue_favicon_task(user.id, domain)
            return
        # 磁盘文件丢失 → 继续到步骤 2 重新获取

    # 2. 磁盘扫描（仅在 DB 无有效记录时执行，支持旧命名迁移和损坏文件清理）
    cached_file = favicon_loader._find_cached_favicon_file(domain)
    if cached_file:
        if cache:
            cache.favicon_file = cached_file
            cache.status = FaviconCache.STATUS_SUCCESS
            if not cache.fetched_at:
                cache.fetched_at = timezone.now()
            cache.save(update_fields=["favicon_file", "status", "fetched_at"])
        else:
            FaviconCache.objects.create(
                domain=domain,
                favicon_file=cached_file,
                status=FaviconCache.STATUS_SUCCESS,
                fetched_at=timezone.now(),
            )
        return

    # 3. 无磁盘文件 → 按 DB 状态处理
    if not cache:
        FaviconCache.objects.create(domain=domain, status=FaviconCache.STATUS_PENDING)
        _enqueue_favicon_task(user.id, domain)
        return

    if cache.status == FaviconCache.STATUS_PENDING:
        return

    if cache.status == FaviconCache.STATUS_FAILED:
        if cache.next_retry_at and cache.next_retry_at <= timezone.now():
            _enqueue_favicon_task(user.id, domain)
        return

    if cache.status == FaviconCache.STATUS_MISSING:
        return

    # STATUS_SUCCESS 但文件丢失（已在步骤 1 处理，此处兜底）
    _enqueue_favicon_task(user.id, domain)


def refresh_favicon_for_url(user: User, url: str):
    """强制刷新指定 URL 的域名 favicon（替代原来的 refresh_favicon(bookmark)）。"""
    if not is_favicon_feature_active(user):
        return
    domain_config = parse_domain_roots(user.profile.custom_domain_root)
    domain = _resolve_domain(url, domain_config)
    if domain:
        _enqueue_favicon_task(user.id, domain)


def load_favicon(user: User, bookmark: Bookmark, domain_config=None):
    """兼容旧接口：书签创建/更新时调用。"""
    ensure_favicon(user, bookmark.url)


def refresh_favicon(user: User, bookmark: Bookmark):
    """兼容旧接口：强制刷新书签的 favicon。"""
    refresh_favicon_for_url(user, bookmark.url)


def _enqueue_favicon_task(user_id: int, domain: str):
    """带去重的入队：同一域名同时只有一个任务在执行。"""
    from django.core.cache import cache as django_cache
    lock_key = f"favicon_task_lock:{domain}"
    if django_cache.add(lock_key, "1", timeout=60):
        _fetch_domain_favicon_task(user_id, domain)


@task(retries=3)
def _fetch_domain_favicon_task(user_id: int, domain: str):
    """per-domain 的 favicon 获取任务。

    成功后更新 FaviconCache。
    失败时更新重试计数和下次重试时间（指数退避）。
    """
    from django.core.cache import cache as django_cache

    from bookmarks.models import FaviconCache

    cache, _ = FaviconCache.objects.get_or_create(
        domain=domain,
        defaults={"status": FaviconCache.STATUS_PENDING},
    )

    logger.info(f"Fetching favicon for domain={domain}")
    favicon_file = favicon_loader.fetch_and_save_favicon(domain, scheme="https")

    if not favicon_file:
        # 尝试 http fallback
        favicon_file = favicon_loader.fetch_and_save_favicon(domain, scheme="http")

    RETRY_DELAYS = FaviconCache.RETRY_DELAYS
    MAX_RETRIES = len(RETRY_DELAYS)

    if favicon_file:
        cache.favicon_file = favicon_file
        cache.status = FaviconCache.STATUS_SUCCESS
        cache.fetched_at = timezone.now()
        cache.retry_count = 0
        cache.next_retry_at = None
        cache.save()
    else:
        cache.retry_count += 1
        if cache.retry_count >= MAX_RETRIES:
            cache.status = FaviconCache.STATUS_MISSING
            cache.favicon_file = ""
            cache.next_retry_at = None
            logger.info(f"Favicon not found for domain={domain} after {MAX_RETRIES} retries, marking as missing")
        else:
            cache.status = FaviconCache.STATUS_FAILED
            delay_seconds = RETRY_DELAYS[cache.retry_count - 1]
            cache.next_retry_at = timezone.now() + timedelta(seconds=delay_seconds)
            logger.info(f"Favicon fetch failed for domain={domain}, retry #{cache.retry_count} in {delay_seconds}s")
        cache.save()

    # 释放锁
    django_cache.delete(f"favicon_task_lock:{domain}")


def schedule_bookmarks_without_favicons(user: User):
    """为用户所有缺少 favicon 的书签入队获取任务（去重到域名级）。"""
    if not is_favicon_feature_active(user):
        return
    _batch_load_favicons_task(user.id)


@task()
def _batch_load_favicons_task(user_id: int):
    from bookmarks.models import FaviconCache

    user = User.objects.get(id=user_id)
    domain_config = parse_domain_roots(user.profile.custom_domain_root)

    # 收集所有已成功的域名
    success_domains = set(
        FaviconCache.objects.filter(
            status=FaviconCache.STATUS_SUCCESS
        ).values_list("domain", flat=True)
    )

    # 先收集所有唯一域名（避免逐条调用 _resolve_domain + ensure_favicon）
    raw_urls = Bookmark.objects.filter(
        owner=user, is_deleted=False
    ).values_list("url", flat=True).iterator()
    domains_to_fetch = set()
    for url in raw_urls:
        domain = _resolve_domain(url, domain_config)
        if domain and domain not in success_domains and domain not in domains_to_fetch:
            domains_to_fetch.add(domain)

    # 为缺少 favicon 的域名入队
    for domain in domains_to_fetch:
        _enqueue_favicon_task(user.id, domain)

    logger.info(f"Queued favicon tasks for {len(domains_to_fetch)} unique domains")


def schedule_refresh_favicons(user: User):
    """手动触发：刷新该用户所有域名的 favicon。"""
    if not is_favicon_feature_active(user) or not settings.LD_ENABLE_REFRESH_FAVICONS:
        return
    _batch_refresh_favicons_task(user.id)


@task()
def _batch_refresh_favicons_task(user_id: int):
    """刷新该用户书签涉及的所有域名的 favicon。"""
    user = User.objects.get(id=user_id)
    domain_config = parse_domain_roots(user.profile.custom_domain_root)

    domains_seen = set()
    for bm in Bookmark.objects.filter(owner=user, is_deleted=False).values("url").iterator():
        domain = _resolve_domain(bm["url"], domain_config)
        if domain and domain not in domains_seen:
            domains_seen.add(domain)
            _enqueue_favicon_task(user.id, domain)

    logger.info(f"Refreshed favicons for {len(domains_seen)} unique domains")


def rename_favicon_for_domain_config(user, old_config_str: str, new_config_str: str):
    """自定义域名规则变更后，无需操作。

    FaviconCache 是全局的，Bookmark.favicon_file 已移除。
    规则变更只是改变了查询 key，渲染时自动使用新规则查表。
    """


# ---------------------------------------------------------------------------
# 预览图加载
# ---------------------------------------------------------------------------


def is_preview_feature_active(user: User) -> bool:
    return (
        user.profile.enable_preview_images and not settings.LD_DISABLE_BACKGROUND_TASKS
    )


def load_preview_image(user: User, bookmark: Bookmark):
    if is_preview_feature_active(user):
        _load_preview_image_task(bookmark.id)


@task()
def delete_preview_image_temp_file(filepath: str):
    logger.debug(
        f"Followed temporary preview image file will be deleted after a while: {filepath}"
    )
    if os.path.exists(filepath):
        os.remove(filepath)
        logger.info(f"Deleted temporary preview image file: {filepath}")


@task()
def _load_preview_image_task(bookmark_id: int):
    try:
        bookmark = Bookmark.objects.get(id=bookmark_id)
    except Bookmark.DoesNotExist:
        return

    logger.info(f"Load preview image for bookmark. url={bookmark.url}")

    new_preview_image_file = preview_image_loader.load_preview_image(
        bookmark.url, bookmark
    )

    if new_preview_image_file != bookmark.preview_image_file:
        bookmark.preview_image_file = new_preview_image_file or ""
        bookmark.save(update_fields=["preview_image_file"])
        logger.info(
            f"Successfully updated preview image for bookmark. url={bookmark.url} preview_image_file={new_preview_image_file}"
        )


def schedule_bookmarks_without_previews(user: User):
    if is_preview_feature_active(user):
        _batch_load_preview_images_task(user.id)


@task()
def _batch_load_preview_images_task(user_id: int):
    user = User.objects.get(id=user_id)
    bookmarks = Bookmark.objects.filter(
        Q(preview_image_file__exact=""),
        owner=user,
    )

    # TODO: Implement bulk task creation
    for bookmark in bookmarks:
        try:
            _load_preview_image_task(bookmark.id)
        except Exception as exc:
            logging.exception(exc)


# ---------------------------------------------------------------------------
# 元数据补全与刷新
# ---------------------------------------------------------------------------


def refresh_metadata(bookmark: Bookmark):
    if not settings.LD_DISABLE_BACKGROUND_TASKS:
        _refresh_metadata_task(bookmark.id)


def schedule_metadata_enrichment(
    bookmark: Bookmark,
    overwrite: bool = False,
    ignore_cache: bool = True,
):
    if not settings.LD_DISABLE_BACKGROUND_TASKS:
        _enrich_metadata_task(
            bookmark.id,
            overwrite=overwrite,
            ignore_cache=ignore_cache,
        )


@task(retries=3)
def _enrich_metadata_task(
    bookmark_id: int,
    overwrite: bool = False,
    ignore_cache: bool = True,
):
    try:
        bookmark = Bookmark.objects.get(id=bookmark_id)
    except Bookmark.DoesNotExist:
        return

    logger.info(f"Enrich metadata for bookmark. url={bookmark.url}")

    metadata = load_website_metadata(bookmark.url, ignore_cache=ignore_cache)
    update_fields = []

    if (
        (overwrite or not bookmark.title)
        and metadata.title is not None
        and metadata.title != bookmark.title
    ):
        bookmark.title = metadata.title
        update_fields.append("title")

    if (
        (overwrite or not bookmark.description)
        and metadata.description is not None
        and metadata.description != bookmark.description
    ):
        bookmark.description = metadata.description
        update_fields.append("description")

    if (
        (overwrite or not bookmark.preview_image_remote_url)
        and metadata.preview_image
        and metadata.preview_image != bookmark.preview_image_remote_url
    ):
        bookmark.preview_image_remote_url = metadata.preview_image
        update_fields.append("preview_image_remote_url")

    if update_fields:
        bookmark.date_modified = timezone.now()
        update_fields.append("date_modified")
        bookmark.save(update_fields=update_fields)
        logger.info(f"Successfully enriched metadata for bookmark. url={bookmark.url}")


@task()
def _refresh_metadata_task(bookmark_id: int):
    try:
        bookmark = Bookmark.objects.get(id=bookmark_id)
    except Bookmark.DoesNotExist:
        return

    logger.info(f"Refresh metadata for bookmark. url={bookmark.url}")

    metadata = load_website_metadata(bookmark.url, ignore_cache=True)
    update_fields = []

    if metadata.title is not None:
        bookmark.title = metadata.title
        update_fields.append("title")
    if metadata.description is not None:
        bookmark.description = metadata.description
        update_fields.append("description")
    if metadata.preview_image:
        bookmark.preview_image_remote_url = metadata.preview_image
        update_fields.append("preview_image_remote_url")
    if metadata.url and metadata.url != bookmark.url:
        bookmark.url = metadata.url
        update_fields.append("url")
    bookmark.date_modified = timezone.now()

    bookmark.save(update_fields=update_fields)
    logger.info(f"Successfully refreshed metadata for bookmark. url={bookmark.url}")

    # 若url变动，则按需更新html快照
    if bookmark.owner.profile.enable_automatic_html_snapshots:
        pending_assets = BookmarkAsset.objects.filter(
            bookmark=bookmark, status=BookmarkAsset.STATUS_PENDING
        )
        if pending_assets.exists():  # 若有下载中的快照，则移除
            pending_assets.delete()

        create_html_snapshot(bookmark)


# ---------------------------------------------------------------------------
# HTML 快照生成（SingleFile 归档）
#
# 采用"调度器 + 冷却窗口"模式：
#   - 每次需要生成快照时，创建 STATUS_PENDING 资产并启动调度器
#   - 调度器按域名冷却间隔串行调度，避免对同一域名频繁抓取
#   - 每分钟定时兜底，确保中断后未完成的任务能被重新拾起
# ---------------------------------------------------------------------------


def is_html_snapshot_feature_active() -> bool:
    return settings.LD_ENABLE_SNAPSHOTS and not settings.LD_DISABLE_BACKGROUND_TASKS


def _trigger_html_snapshot_dispatcher():
    _html_snapshot_dispatcher_task()


def _get_html_snapshot_cooldown_seconds(
    randint_func: Callable[[int, int], int] | None = None,
) -> int:
    min_seconds = settings.LD_SNAPSHOT_DOMAIN_COOLDOWN_MIN_SEC
    max_seconds = settings.LD_SNAPSHOT_DOMAIN_COOLDOWN_MAX_SEC
    if max_seconds < min_seconds:
        min_seconds, max_seconds = max_seconds, min_seconds

    randint = randint_func or random.randint
    return randint(min_seconds, max_seconds)


def _get_html_snapshot_dispatcher_tick_seconds() -> int:
    return max(settings.LD_SNAPSHOT_DISPATCHER_TICK_SEC, 1)


def _select_next_html_snapshot_asset(now, next_eligible_at: dict[str, object]):
    """
    从待处理快照队列中选出下一个可执行的资产。

    优先选 date_created 最新的（LIFO），但跳过仍处于冷却期的域名。
    返回 (asset, next_wake_at)：
      - asset 不为 None  → 立即执行
      - asset 为 None     → 所有 pending 均在冷却中，next_wake_at 为最早可唤醒时间
    """
    # 所有 pending 资产（包括有重试时间的）
    all_pending = BookmarkAsset.objects.filter(
        asset_type=BookmarkAsset.TYPE_SNAPSHOT,
        status=BookmarkAsset.STATUS_PENDING,
    ).select_related("bookmark").order_by("-date_created", "-id")

    # 可立即执行的（无重试时间或重试时间已过）
    executable = all_pending.filter(
        Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now)
    )

    next_wake_at = None

    # 先检查可执行的资产
    for asset in executable:
        domain = get_registrable_domain(asset.bookmark.url)
        eligible_at = next_eligible_at.get(domain)
        if eligible_at is None or eligible_at <= now:
            return asset, None
        if next_wake_at is None or eligible_at < next_wake_at:
            next_wake_at = eligible_at

    # 再检查有未来重试时间的资产，更新 next_wake_at
    waiting = all_pending.filter(
        next_retry_at__gt=now
    )
    for asset in waiting:
        if next_wake_at is None or asset.next_retry_at < next_wake_at:
            next_wake_at = asset.next_retry_at

    return None, next_wake_at


def _get_html_snapshot_dispatcher_sleep_seconds(now, next_wake_at) -> float:
    remaining_seconds = max((next_wake_at - now).total_seconds(), 0)
    if remaining_seconds == 0:
        return 0
    return min(remaining_seconds, _get_html_snapshot_dispatcher_tick_seconds())


def _run_html_snapshot_dispatcher_loop(
    now_func: Callable[[], object] | None = None,
    sleep_func: Callable[[float], None] | None = None,
    cooldown_func: Callable[[], int] | None = None,
):
    """
    调度器主循环：持续消费待处理快照，直到队列清空。

    工作逻辑：
      1. 从 pending 队列中选出下一个可执行资产（跳过冷却中的域名）
      2. 选中 → 提交任务，记录该域名的下次可用时间
      3. 无可执行资产 → 计算最早唤醒时间，sleep 等待后重试
      4. 队列完全为空 → 退出循环
    """
    now_func = now_func or timezone.now
    sleep_func = sleep_func or time.sleep
    cooldown_func = cooldown_func or _get_html_snapshot_cooldown_seconds
    next_eligible_at: dict[str, object] = {}

    while True:
        now = now_func()
        asset, next_wake_at = _select_next_html_snapshot_asset(now, next_eligible_at)
        if asset is None:
            if next_wake_at is None:
                return
            sleep_seconds = _get_html_snapshot_dispatcher_sleep_seconds(
                now, next_wake_at
            )
            if sleep_seconds > 0:
                sleep_func(sleep_seconds)
            continue

        domain = get_registrable_domain(asset.bookmark.url)
        _create_html_snapshot_task(asset.id)
        next_eligible_at[domain] = now_func() + timedelta(seconds=cooldown_func())


@task(retries=0, retry_delay=0)
def _html_snapshot_dispatcher_task():
    try:
        with HTML_SNAPSHOT_DISPATCHER_LOCK:
            _run_html_snapshot_dispatcher_loop()
    except TaskLockedException:
        logger.debug("HTML snapshot dispatcher already running.")


def create_html_snapshot(bookmark: Bookmark):
    if not is_html_snapshot_feature_active():
        return

    asset = assets.create_snapshot_asset(bookmark)
    asset.save()
    _trigger_html_snapshot_dispatcher()


def create_html_snapshots(bookmark_list: list[Bookmark]):
    if not is_html_snapshot_feature_active():
        return

    assets_to_create = []
    for bookmark in bookmark_list:
        asset = assets.create_snapshot_asset(bookmark)
        assets_to_create.append(asset)

    if not assets_to_create:
        return

    BookmarkAsset.objects.bulk_create(assets_to_create)
    _trigger_html_snapshot_dispatcher()


# SingleFile does not support running multiple snapshot captures in parallel.
# Keep a periodic fallback that can re-trigger the dispatcher if pending work was
# missed due to an interrupted worker or process restart.
@huey.periodic_task(crontab(minute="*"))
def _schedule_html_snapshots_task():
    if BookmarkAsset.objects.filter(
        asset_type=BookmarkAsset.TYPE_SNAPSHOT,
        status=BookmarkAsset.STATUS_PENDING,
    ).exists():
        _trigger_html_snapshot_dispatcher()


def _create_html_snapshot_task(asset_id: int):
    try:
        asset = BookmarkAsset.objects.get(id=asset_id)
    except BookmarkAsset.DoesNotExist:
        return

    logger.info(f"Create HTML snapshot for bookmark. url={asset.bookmark.url}")

    try:
        assets.create_snapshot(asset)

        logger.info(
            f"Successfully created HTML snapshot for bookmark. url={asset.bookmark.url}"
        )
    except Exception as error:
        # 刷新以获取 assets.create_snapshot 设置的最新状态
        asset.refresh_from_db()

        retry_delays = settings.LD_SNAPSHOT_RETRY_DELAYS
        max_retries = len(retry_delays)

        # 重试逻辑：使用配置的延迟数组
        if asset.retry_count < max_retries:
            delay_seconds = retry_delays[asset.retry_count]
            asset.retry_count += 1
            asset.next_retry_at = timezone.now() + timedelta(seconds=delay_seconds)
            asset.status = BookmarkAsset.STATUS_PENDING  # 覆盖 STATUS_FAILURE
            asset.save()
            logger.warning(
                f"Snapshot failed, will retry #{asset.retry_count} at {asset.next_retry_at}. "
                f"url={asset.bookmark.url}"
            )
        else:
            # 已达最大重试次数，保持 STATUS_FAILURE（由 assets.create_snapshot 设置）
            logger.error(
                f"Snapshot failed after {asset.retry_count} retries. "
                f"url={asset.bookmark.url}",
                exc_info=error,
            )


def create_missing_html_snapshots(user: User) -> int:
    if not is_html_snapshot_feature_active():
        return 0

    bookmarks_without_snapshots = Bookmark.objects.filter(owner=user).exclude(
        bookmarkasset__asset_type=BookmarkAsset.TYPE_SNAPSHOT,
        bookmarkasset__status__in=[
            BookmarkAsset.STATUS_PENDING,
            BookmarkAsset.STATUS_COMPLETE,
        ],
    )
    bookmarks_without_snapshots |= Bookmark.objects.filter(owner=user).exclude(
        bookmarkasset__asset_type=BookmarkAsset.TYPE_SNAPSHOT
    )

    create_html_snapshots(list(bookmarks_without_snapshots))

    return bookmarks_without_snapshots.count()


# ---------------------------------------------------------------------------
# 文章提取（阅读模式，defuddle 解析）
#
# 解析优先级：
#   1. 已有 HTML 快照 → 直接用 defuddle 解析 HTML
#   2. 域名配置了自定义 snapshot_processor → 先生成快照再解析
#   3. 都没有 → 让 defuddle 直接抓取 URL；失败则回退到生成快照再解析
# ---------------------------------------------------------------------------


def create_article(bookmark: Bookmark) -> BookmarkAsset:
    """创建 pending 状态的文章资产，并提交 defuddle 解析任务。"""
    from bookmarks.services.articles import create_article_asset_pending

    asset = create_article_asset_pending(bookmark)
    _create_article_task(asset.id)
    return asset


def create_html_articles(bookmark_list: list[Bookmark]):
    """批量创建 pending 状态的文章资产，并逐个提交 defuddle 解析任务。"""
    from bookmarks.services.articles import create_article_asset_pending

    for bookmark in bookmark_list:
        asset = create_article_asset_pending(bookmark)
        _create_article_task(asset.id)


def _load_snapshot_asset_html(snapshot: BookmarkAsset | None) -> str | None:
    """从快照资产文件中读取 HTML 内容，无法读取时返回 None。"""
    if (
        not snapshot
        or snapshot.status != BookmarkAsset.STATUS_COMPLETE
        or snapshot.content_type != BookmarkAsset.CONTENT_TYPE_HTML
        or not snapshot.file
    ):
        return None

    filepath = os.path.join(settings.LD_ASSET_FOLDER, snapshot.file)
    if not os.path.exists(filepath):
        return None

    try:
        if snapshot.gzip:
            with gzip.open(filepath, "rb") as f:
                return f.read().decode("utf-8")
        else:
            with open(filepath, encoding="utf-8") as f:
                return f.read()
    except Exception:
        logger.warning(
            f"Failed to read snapshot for bookmark. url={snapshot.bookmark.url}",
            exc_info=True,
        )
        return None


def _load_snapshot_html(bookmark: Bookmark) -> str | None:
    """读取书签最新快照的 HTML 内容，无可用快照时返回 None。"""
    return _load_snapshot_asset_html(bookmark.latest_snapshot)


def _has_custom_snapshot_processor(url: str) -> bool:
    """检查该域名是否配置了自定义快照处理器。"""
    from bookmarks.utils import search_config_for_domain

    settings_path = settings.LD_CUSTOM_SNAPSHOT_PROCESSOR_SETTINGS
    if not settings_path or not os.path.exists(settings_path):
        return False

    config = search_config_for_domain(url, settings_path)
    return config is not None


def _create_snapshot_for_article(
    bookmark: Bookmark,
) -> tuple[BookmarkAsset | None, str | None]:
    """为文章解析生成快照，返回 (快照资产, HTML内容)；失败时内容为 None。"""
    asset = assets.create_snapshot_asset(bookmark)
    asset.save()

    try:
        assets.create_snapshot(asset)
        asset.refresh_from_db()
        if asset.status == BookmarkAsset.STATUS_COMPLETE:
            return asset, _load_snapshot_asset_html(asset)
    except Exception:
        logger.warning(
            f"Failed to create snapshot for article. url={bookmark.url}",
            exc_info=True,
        )

    return asset, None


@task(retries=2)
def _create_article_task(asset_id: int):
    """Huey 任务：抓取页面 → defuddle 解析 → 保存文章内容。"""
    from bookmarks.services.articles import remove_article, save_article_content

    try:
        asset = BookmarkAsset.objects.get(id=asset_id)
    except BookmarkAsset.DoesNotExist:
        return

    # LIFO dedup: if a newer pending article exists for the same bookmark, skip
    newer_pending = BookmarkAsset.objects.filter(
        bookmark=asset.bookmark,
        asset_type=BookmarkAsset.TYPE_ARTICLE,
        status=BookmarkAsset.STATUS_PENDING,
        date_created__gt=asset.date_created,
    ).exists()
    if newer_pending:
        logger.info(
            f"Skipping stale article task (newer pending exists). url={asset.bookmark.url}"
        )
        remove_article(asset)
        return

    bookmark = asset.bookmark
    logger.info(f"Create article for bookmark. url={bookmark.url}")

    fallback_snapshot = None
    try:
        from bookmarks.services import reader_processor

        # 1. Try existing snapshot
        raw_html = _load_snapshot_html(bookmark)
        if raw_html:
            logger.info(f"Using existing snapshot. url={bookmark.url}")
            result = reader_processor.parse_html(raw_html, url=bookmark.url)
        elif _has_custom_snapshot_processor(bookmark.url):
            # 2. Custom snapshot_processor → create snapshot first, then parse
            logger.info(f"Creating snapshot via custom processor. url={bookmark.url}")
            _snapshot, raw_html = _create_snapshot_for_article(bookmark)
            if not raw_html:
                raise Exception("Failed to create snapshot via custom processor")
            result = reader_processor.parse_html(raw_html, url=bookmark.url)
        else:
            # 3. No snapshot, no custom processor → let defuddle fetch URL directly.
            # If that fails, retry once from a freshly generated snapshot.
            logger.info(f"Parsing URL directly with defuddle. url={bookmark.url}")
            try:
                result = reader_processor.parse_url(bookmark.url)
            except Exception as direct_error:
                logger.info(
                    f"Direct article parsing failed; retrying via generated snapshot. url={bookmark.url}",
                    exc_info=True,
                )
                fallback_snapshot, raw_html = _create_snapshot_for_article(bookmark)
                if not raw_html:
                    raise Exception(
                        "Failed to create fallback snapshot for article"
                    ) from direct_error
                result = reader_processor.parse_html(raw_html, url=bookmark.url)

        # 生成标准 HTML 文档：元数据放 head，正文放 body
        from django.utils.html import escape
        content = result["content"]
        head_parts = []
        if result.get("title"):
            head_parts.append(f'<meta name="title" content="{escape(result["title"])}">')
        if result.get("wordCount"):
            head_parts.append(f'<meta name="word-count" content="{result["wordCount"]}">')
        head = "".join(head_parts)
        title_tag = f"<title>{escape(result['title'])}</title>" if result.get("title") else ""
        content = f"<!DOCTYPE html><html><head>{title_tag}{head}</head><body>{content}</body></html>"

        # Save parsed content
        save_article_content(asset, content, title=result["title"])

        logger.info(f"Successfully created article for bookmark. url={bookmark.url}")
    except Exception as error:
        if fallback_snapshot:
            try:
                assets.remove_asset(fallback_snapshot)
            except Exception:
                logger.warning(
                    f"Failed to clean up generated snapshot after article failure. url={bookmark.url}",
                    exc_info=True,
                )
        try:
            remove_article(asset)
        except Exception:
            logger.warning(
                f"Failed to clean up article asset after processing failure. url={bookmark.url}",
                exc_info=True,
            )
        logger.error(
            f"Failed to create article for bookmark. url={bookmark.url}",
            exc_info=error,
        )
