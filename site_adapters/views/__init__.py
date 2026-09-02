"""
Site Adapters management views.

Split into submodules by responsibility:
  - helpers.py       Shared utilities, decorators, global config
  - page.py          Main page rendering
  - adapters.py      User settings adapters page
  - credentials.py   Credential endpoints
  - snapshot_toggles.py Snapshot toggle endpoint
  - domains.py       Domain CRUD + rename
  - testing.py       Test panel + validation + save_cookie
  - subscriptions.py Subscription management
  - snapshot.py      Snapshot preview
"""

# Re-export all public view functions for URL routing compatibility.
from site_adapters.views.adapters import adapters_page
from site_adapters.views.credentials import (
    match_domain_config,
    shared_credential_delete,
    shared_credential_list,
    shared_credential_save,
    user_credentials,
)
from site_adapters.views.domains import (
    domain_create,
    domain_delete,
    domain_read,
    domain_rename,
    domain_save,
)
from site_adapters.views.page import site_adapters_page
from site_adapters.views.preview import preview_image_proxy
from site_adapters.views.snapshot import view_reader, view_snapshot
from site_adapters.views.snapshot_toggles import snapshot_toggles
from site_adapters.views.subscriptions import (
    all_domains,
    local_domain_toggle,
    subscription_domain_read,
    subscription_domain_toggle,
    subscription_manage,
)
from site_adapters.views.testing import (
    action,
    save_cookie,
)

__all__ = [
    'site_adapters_page',
    'domain_create', 'domain_delete', 'domain_read', 'domain_rename', 'domain_save',
    'action', 'save_cookie',
    'all_domains', 'local_domain_toggle',
    'subscription_domain_read', 'subscription_domain_toggle', 'subscription_manage',
    'adapters_page', 'user_credentials', 'snapshot_toggles',
    'shared_credential_list', 'shared_credential_save', 'shared_credential_delete',
    'view_snapshot',
    'view_reader',
    'preview_image_proxy',
]
