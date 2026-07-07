from django.conf import settings
from django.db.backends.signals import connection_created
from django.dispatch import receiver


@receiver(connection_created)
def extend_sqlite(connection=None, **kwargs):
    # Load ICU extension into Sqlite connection to support case-insensitive
    # comparisons with unicode characters
    if connection.vendor == "sqlite" and settings.USE_SQLITE_ICU_EXTENSION:
        connection.connection.enable_load_extension(True)
        connection.connection.load_extension(
            settings.SQLITE_ICU_EXTENSION_PATH.rstrip(".so")
        )

        with connection.cursor() as cursor:
            # Load an ICU collation for case-insensitive ordering.
            # The first param can be a specific locale, it seems that not
            # providing one will use a default collation from the ICU project
            # that works reasonably for multiple languages
            cursor.execute("SELECT icu_load_collation('', 'ICU');")


# ---- tag co-occurrence cache maintenance ----
#
# The cached (bookmark_id, tag_id) pairs are kept warm by incrementally
# updating them on m2m changes and evicting them on hard deletes.
#
# * m2m_changed (tags added/removed/cleared on a bookmark) → incremental
# * Bookmark deleted → evict (pairs are cascade-deleted in DB)
# * Tag deleted → evict (pairs are cascade-deleted in DB)
#
# Bookmark field-level edits (title, url …) do NOT affect the M2M pairs
# and intentionally have no signal connected.

from django.db.models.signals import m2m_changed, post_delete
from django.dispatch import receiver  # noqa: F811

from bookmarks.models import Bookmark, Tag
from bookmarks.views.contexts import (
    _CACHE_KEY_PREFIX,
    _CACHE_TIMEOUT,
    invalidate_tag_cooccurrence_cache,
)
from django.core.cache import cache


def _get_cached_pairs_or_none(user_id):
    """Return the cached pairs list, or None if not cached."""
    return cache.get(f"{_CACHE_KEY_PREFIX}:{user_id}")


def _set_cached_pairs(user_id, pairs):
    cache.set(f"{_CACHE_KEY_PREFIX}:{user_id}", pairs, _CACHE_TIMEOUT)


# -- incremental update on tag add / remove / clear ------------------

@receiver(m2m_changed, sender=Bookmark.tags.through)
def _update_cache_on_m2m_change(sender, instance, action, pk_set, **kwargs):
    """Incrementally update cached pairs when tags change on a bookmark."""
    user_id = instance.owner_id
    cached = _get_cached_pairs_or_none(user_id)
    if cached is None:
        return  # Cache not populated yet — nothing to update.

    if action == "post_add":
        # New tags added: append the new (bookmark, tag) pairs.
        new_pairs = [(instance.id, tid) for tid in pk_set]
        cached.extend(new_pairs)
        _set_cached_pairs(user_id, cached)

    elif action == "post_remove":
        # Tags removed: filter out the matching pairs.
        remove = {(instance.id, tid) for tid in pk_set}
        cached[:] = [p for p in cached if p not in remove]
        _set_cached_pairs(user_id, cached)

    elif action == "post_clear":
        # All tags cleared from the bookmark.
        bid = instance.id
        cached[:] = [p for p in cached if p[0] != bid]
        _set_cached_pairs(user_id, cached)


# -- evict on hard delete -------------------------------------------

@receiver(post_delete, sender=Bookmark)
def _evict_on_bookmark_delete(sender, instance, **kwargs):
    invalidate_tag_cooccurrence_cache(instance.owner_id)


@receiver(post_delete, sender=Tag)
def _evict_on_tag_delete(sender, instance, **kwargs):
    invalidate_tag_cooccurrence_cache(instance.owner_id)
