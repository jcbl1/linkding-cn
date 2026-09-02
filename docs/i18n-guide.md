# i18n Guide

This project uses Django's native gettext for internationalization. The source language is English; translations live in `locale/<lang>/LC_MESSAGES/`.

---

## Add a New Language (4 Steps)

Using French `fr` as an example.

### 1. Register the language

Add it to `LANGUAGES` in `bookmarks/settings/base.py`:

```python
LANGUAGES = [
    ("en", "English"),
    ("zh-hans", "Simplified Chinese"),
    ("fr", "French"),  # new
]
```

### 2. Generate .po files

```bash
# Python / template strings
DJANGO_SETTINGS_MODULE=bookmarks.settings.base \
.venv/bin/python manage.py makemessages -l fr \
  --ignore ".venv/*" --ignore "node_modules/*" --ignore "docs/*" \
  --ignore "chromium-profile/*" --ignore "bookmarks/static/*" --ignore "site_adapters/static/*"

# JavaScript strings
DJANGO_SETTINGS_MODULE=bookmarks.settings.base \
.venv/bin/python manage.py makemessages -d djangojs -l fr \
  --ignore ".venv/*" --ignore "node_modules/*" --ignore "docs/*" \
  --ignore "chromium-profile/*" --ignore "bookmarks/static/*" --ignore "site_adapters/static/*"
```

### 3. Translate

Fill in the `msgstr` for each entry in `locale/fr/LC_MESSAGES/django.po` and `djangojs.po`.

> 💡 You can copy `locale/zh_Hans` as a starting point, replace the `msgstr` values and PO header fields (`Language`, `Language-Team`), then run Step 4.

### 4. Compile and verify

```bash
DJANGO_SETTINGS_MODULE=bookmarks.settings.base .venv/bin/python manage.py compilemessages
npm run build
```

Verify: the login page can switch to the new language, the setting persists after saving, and JS dialog text also switches.

---

## Development: Writing Translatable Strings

All user-facing strings must be written in English and marked as translatable:

### Templates

```django
{% load i18n %}

{% translate "Save" %}
{% blocktranslate trimmed with count=item_count %}
  {{ count }} item selected
{% endblocktranslate %}
```

### Python

```python
from django.utils.translation import gettext_lazy, pgettext_lazy

# Use _lazy for strings defined at import time (field names, choices, etc.)
status = models.CharField(choices=[
    ("unread", gettext_lazy("Unread")),
])

# Use pgettext when the same English word has different meanings
("read", pgettext_lazy("bookmark_status", "Read")),   # → Lu (read state)
("read", pgettext_lazy("bookmark_action", "Read")),   # → Lire (read action)
```

### JavaScript

```js
import { gettext } from "./i18n";
button.textContent = gettext("Confirm");
```

The function name must remain `gettext` so that `makemessages -d djangojs` can extract it.

---

## pgettext Context Convention

Only use context for **ambiguous words** (same English, different translation). Don't add context to unambiguous words.

| Context | Purpose | Examples |
|---|---|---|
| `bookmark_status` | Bookmark **state** | Read→Lu, Unread→Non lu |
| `bookmark_action` | Bookmark **action** | Read→Lire |

Template usage:

```django
{% translate "Read" context "bookmark_status" %}   {# Lu #}
{% translate "Read" context "bookmark_action" %}   {# Lire #}
{% translate "Bookmarks" %}                          {# unambiguous, no context #}
```

---

## Routine Maintenance

After modifying user-facing strings:

```bash
# 1. Re-extract messages
makemessages -l zh_Hans ...   # same flags as above
makemessages -d djangojs -l zh_Hans ...

# 2. Translate any new msgstr

# 3. Compile
compilemessages

# 4. Rebuild frontend assets
npm run build
```