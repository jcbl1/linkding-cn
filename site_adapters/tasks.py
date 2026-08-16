"""Site adapters 定时任务：订阅源自动更新。"""

import logging

from django.conf import settings
from huey import crontab
from huey.contrib.djhuey import HUEY as huey
from huey.exceptions import TaskLockedException

from bookmarks.services.tasks import (
    BACKGROUND_SERIAL_LOCK,
    PRIORITY_SUBSCRIPTION,
    acquire_non_urgent_slot,
    release_non_urgent_slot,
)

logger = logging.getLogger(__name__)


@huey.periodic_task(crontab(minute="0"), priority=PRIORITY_SUBSCRIPTION)
def _scheduled_subscription_refresh():
    """每小时检查并更新到达 interval 的远程订阅源。

    以 config.jsonc 中的 update_interval 为准：
      - update_interval = 0 → 跳过（禁用自动更新）
      - 距离上次拉取 >= update_interval 秒 → 执行更新
    """
    if settings.LD_DISABLE_BACKGROUND_TASKS:
        return

    from site_adapters.services.subscriptions import fetch_all_subscriptions
    from site_adapters.views.helpers import _get_adapters_list

    if not acquire_non_urgent_slot():
        logger.debug("Subscription auto-update skipped, no non-urgent slot")
        return
    try:
        with BACKGROUND_SERIAL_LOCK:
            try:
                adapters = _get_adapters_list()
                paths = fetch_all_subscriptions(adapters)
                if paths:
                    logger.info("Subscription auto-update: %d updated", len(paths))
            except Exception:
                logger.exception("Subscription auto-update failed")
    except TaskLockedException:
        logger.debug("Subscription auto-update skipped, background serial lock is busy")
    finally:
        release_non_urgent_slot()
