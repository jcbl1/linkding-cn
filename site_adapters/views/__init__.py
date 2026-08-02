"""
Site Adapters management views.

Split into submodules by responsibility:
  - helpers.py       Shared utilities, decorators, global config
  - page.py          Main page rendering
  - domains.py       Domain CRUD + rename
  - testing.py       Test panel + validation + save_cookie
  - resources.py     File browser CRUD
  - subscriptions.py Subscription management
  - credentials.py   User credential management
  - snapshot.py      Snapshot preview
"""

# Re-export all public view functions for URL routing compatibility.
from site_adapters.views.page import site_adapters_page
from site_adapters.views.domains import (
    domain_create,
    domain_delete,
    domain_read,
    domain_rename,
    domain_save,
)
from site_adapters.views.testing import (
    action,
    save_cookie,
)
from site_adapters.views.resources import (
    resource_manage,
    resource_save,
    resources,
)
from site_adapters.views.subscriptions import (
    all_domains,
    local_domain_toggle,
    subscription_domain_read,
    subscription_domain_toggle,
    subscription_manage,
)
from site_adapters.views.credentials import (
    cookies_page,
    user_cookies,
    user_toggles,
)
from site_adapters.views.snapshot import view_snapshot

__all__ = [
    'site_adapters_page',
    'domain_create', 'domain_delete', 'domain_read', 'domain_rename', 'domain_save',
    'action', 'save_cookie',
    'resource_manage', 'resource_save', 'resources',
    'all_domains', 'local_domain_toggle',
    'subscription_domain_read', 'subscription_domain_toggle', 'subscription_manage',
    'cookies_page', 'user_cookies', 'user_toggles',
    'view_snapshot',
]
