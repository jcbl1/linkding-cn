import { gettext } from "./utils/i18n.js";

function initAdapters() {
  var csrfToken = window.__ld_csrf_token || '';
  var urls = window.__ld_urls || {};
  var credMode = window.__ld_cred_mode || 'user';  // 'user' | 'shared'

  var container = document.querySelector('[ld-site-adapters]');
  if (!container) return;
  if (container.dataset.adaptersReady) return;
  container.dataset.adaptersReady = '1';

  var allDomains = window.__ld_auth_domains || [];
  var togglesUrl = window.__ld_snapshot_toggles_url || '/settings/adapters/snapshot_toggles';
  var modalOpen = false;
  // Full credentials data (unfiltered) for client-side search in shared mode
  var allCredentials = window.__ld_credentials_data || [];

  // ===================================================================
  //  Toast helper
  // ===================================================================
  function toast(msg, tone) {
    if (typeof window.showToast === 'function') {
      window.showToast(msg, { tone: tone || 'info' });
    }
  }

  // ===================================================================
  //  API helpers for shared mode
  // ===================================================================
  function apiPost(url, data) {
    var fd = new FormData();
    for (var k in data) {
      if (!data.hasOwnProperty(k)) continue;
      fd.append(k, data[k]);
    }
    return fetch(url, {
      method: 'POST',
      headers: { 'X-CSRFToken': csrfToken },
      body: fd
    }).then(function(r) { return r.json(); });
  }

  function apiGet(url) {
    return fetch(url, { headers: { 'X-CSRFToken': csrfToken } })
      .then(function(r) { return r.json(); });
  }

  // ===================================================================
  //  Cookie format validation
  // ===================================================================
  function isValidCookieFormat(val) {
    return val && /[^;=]+=[^;=]+/.test(val);
  }

  // ===================================================================
  //  On page load: check sessionStorage for save-success signal
  // ===================================================================
  (function() {
    try {
      if (sessionStorage.getItem('ld_cred_saved') === '1') {
        sessionStorage.removeItem('ld_cred_saved');
        toast(gettext('Saved successfully'), 'success');
      }
    } catch(e) {}
  })();

  // Cleanup stale event handlers
  container.querySelectorAll('.wa-toggle-domain-bar').forEach(function(b) { b.onclick = null; });

  // ===================================================================
  //  Expand / collapse (toggles only)
  // ===================================================================
  container.addEventListener('click', function(e) {
    var bar = e.target.closest('.wa-toggle-domain-bar');
    if (!bar) return;
    var row = bar.closest('.wa-toggle-domain-row');
    row.classList.toggle('expanded');
  });

  // ===================================================================
  //  Toggle switch (user settings page only)
  // ===================================================================
  container.addEventListener('change', function(e) {
    var cb = e.target.closest('.wa-toggle-pref');
    if (!cb) return;

    var domain = cb.dataset.domain;
    var toggleId = cb.dataset.toggle;
    var newValue = cb.checked;
    var defaultVal = cb.dataset.default === 'true';
    var wasModified = (!newValue) !== defaultVal;
    var isModified = newValue !== defaultVal;

    function adjustMod(delta) {
      var row = cb.closest('.wa-toggle-domain-row');
      var modSpan = row && row.querySelector('.wa-toggle-col-modified');
      if (!modSpan) return;
      var cur = parseInt(modSpan.textContent, 10) || 0;
      modSpan.textContent = Math.max(0, cur + delta);
    }
    if (!wasModified && isModified) adjustMod(1);
    else if (wasModified && !isModified) adjustMod(-1);

    var fd = new FormData();
    fd.append('domain', domain);
    fd.append('toggle_id', toggleId);
    fd.append('enabled', String(newValue));

    fetch(togglesUrl, {
      method: 'POST',
      headers: {'X-CSRFToken': csrfToken},
      body: fd,
      signal: AbortSignal.timeout(10000)
    }).then(function(r) { return r.json(); }).then(function(data) {
      if (data.error) throw new Error(data.error);
    }).catch(function() {
      console.warn('Toggle save failed, reverting');
      cb.checked = !newValue;
      adjustMod(newValue ? -1 : 1);
    });
  });

  // ===================================================================
  //  Toggle filter form (user settings page only)
  // ===================================================================
  var filterForm = container.querySelector('#toggle-filter-form');
  if (filterForm) {
    filterForm.addEventListener('change', function(e) {
      if (e.target.name === 'modified_only') filterForm.requestSubmit();
    });
  }

  // ===================================================================
  //  Search form — shared mode: client-side filter
  // ===================================================================
 var searchForm = container.querySelector('.wa-cred-search-form');
 if (searchForm && credMode === 'shared') {
   searchForm.setAttribute('data-turbo', 'false');
   searchForm.addEventListener('submit', function(e) {
      e.preventDefault();
      var q = (searchForm.querySelector('input[name="q"]') || {}).value || '';
      filterAndRender(q.toLowerCase());
    });
  }

  function filterAndRender(q) {
    var filtered = q
      ? allCredentials.filter(function(c) { return c.domain.toLowerCase().indexOf(q) >= 0; })
      : allCredentials;
    renderCredentialRows(filtered, q);
  }

  // ===================================================================
  //  Add credential button
  // ===================================================================
  container.addEventListener('click', function(e) {
    if (!e.target.closest('#btn-add-credential')) return;
    e.preventDefault();
    showModal('Add credentials', null, null);
  });

  // ===================================================================
  //  Edit credential button
  // ===================================================================
  container.addEventListener('click', function(e) {
    var btn = e.target.closest('.js-edit-cred');
    if (!btn) return;
    e.preventDefault();
    showModal('Edit: ' + btn.dataset.domain, btn.dataset.domain, btn.dataset.type);
  });

  // ===================================================================
  //  Delete credential button — ld-confirm-popup
  // ===================================================================
  container.addEventListener('click', function(e) {
    var btn = e.target.closest('.js-delete-cred');
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();

    var popup = document.createElement('ld-confirm-popup');
    popup._button = btn;
    popup._onConfirm = function() {
      var domain = btn.dataset.domain;
      var type = btn.dataset.type;

      if (credMode === 'shared') {
        apiPost(urls.sharedCredDelete, {
          domain: domain,
          type: type,
          header_name: btn.dataset.headerName || ''
        }).then(function(r) {
          if (r.success) {
            toast(gettext('Deleted'), 'success');
            reloadCredentialList();
          } else {
            toast(gettext('Error: ') + (r.error || 'unknown'), 'error');
          }
        }).catch(function() {
          toast(gettext('Delete failed'), 'error');
        });
      } else {
        var f = document.createElement('form');
        f.method = 'POST';
        f.action = window.location.pathname + window.location.search;
        f.style.display = 'none';
        addHidden(f, 'csrfmiddlewaretoken', csrfToken);
        addHidden(f, 'action', 'delete_credential');
        addHidden(f, 'domain', domain);
        addHidden(f, 'type', type);
        if (btn.dataset.headerName) addHidden(f, 'header_name', btn.dataset.headerName);
        document.body.appendChild(f);
        f.requestSubmit();
      }
    };
    btn.setAttribute('ld-confirm-question', gettext('Delete this credential?'));
    btn.setAttribute('ld-confirm-danger', '');
    document.body.appendChild(popup);
  });

  // ===================================================================
  //  Reload credential list (shared mode)
  // ===================================================================
  function reloadCredentialList() {
    if (!urls.sharedCredList) return;
    apiGet(urls.sharedCredList).then(function(data) {
      var creds = data.credentials || [];
      window.__ld_credentials_data = creds;
      allCredentials = creds;
      allDomains = data.domains || [];
      // Re-apply current search filter
      var q = (searchForm ? (searchForm.querySelector('input[name="q"]') || {}).value || '' : '').toLowerCase();
      filterAndRender(q);
    }).catch(function() {
      toast(gettext('Failed to reload credentials'), 'error');
    });
  }

  function renderCredentialRows(credentials, searchQuery) {
    var body = container.querySelector('.wa-cred-table-body');
    if (!body) return;

    if (!credentials || !credentials.length) {
      var msg = searchQuery
        ? gettext('No matching results.')
        : gettext('No credentials added yet.');
      body.innerHTML = '<div class="wa-cred-empty">' + msg + '</div>';
      return;
    }

    var html = '';
    credentials.forEach(function(c) {
      html += '<div class="wa-cred-row">'
        + '<span class="wa-col-domain wa-cred-domain-cell">' + escapeHtml(c.domain) + '</span>'
        + '<span class="wa-col-type wa-cred-type-cell">'
        + '<span class="wa-badge">' + (c.type === 'cookie' ? 'Cookie' : c.type === 'oauth2' ? 'OAuth2' : c.type === 'token' ? 'OAuth2' : 'Header') + '</span>'
        + (c.type === 'header' && c.header_names ? '<span class="wa-cred-header-names">(' + escapeHtml(c.header_names.slice(0, 3).join(', ')) + (c.header_names.length > 3 ? '...' : '') + ')</span>' : '')
        + '</span>'
        + '<span class="wa-col-updated wa-cred-updated-cell">' + escapeHtml((c.updated_at || '').slice(0, 10))
        + (c.status !== 'ok' ? ' <span class="wa-badge wa-badge-warn">key changed</span>' : '') + '</span>'
        + '<span class="wa-col-actions">'
        + '<button type="button" class="btn btn-sm js-edit-cred" data-domain="' + escapeHtml(c.domain) + '" data-type="' + escapeHtml(c.type) + '">' + gettext('Edit') + '</button>'
        + '<button type="button" class="btn btn-sm btn-error js-delete-cred" data-domain="' + escapeHtml(c.domain) + '" data-type="' + escapeHtml(c.type) + '"'
        + (c.type === 'header' && c.header_names && c.header_names[0] ? ' data-header-name="' + escapeHtml(c.header_names[0]) + '"' : '')
        + '>' + gettext('Delete') + '</button>'
        + '</span></div>';
    });
    body.innerHTML = html;
  }

  // ===================================================================
  //  Submit credential form helper
  // ===================================================================
  function submitCredentialForm(targetDomain, credType, extras) {
    if (credMode === 'shared') {
      var data = { domain: targetDomain, type: credType };
      if (extras) {
        if (extras.value) data.value = extras.value;
        if (extras.header_name) data.header_name = extras.header_name;
      }
      apiPost(urls.sharedCredSave, data).then(function(r) {
        if (r.success) {
          toast(gettext('Saved successfully'), 'success');
          closeCurrentModal();
          reloadCredentialList();
        } else {
          toast(gettext('Error: ') + (r.error || 'unknown'), 'error');
        }
      }).catch(function() {
        toast(gettext('Save failed'), 'error');
      });
    } else {
      try { sessionStorage.setItem('ld_cred_saved', '1'); } catch(e) {}
      var f = document.createElement('form');
      f.method = 'POST';
      f.action = window.location.pathname + window.location.search;
      f.style.display = 'none';
      addHidden(f, 'csrfmiddlewaretoken', csrfToken);
      addHidden(f, 'action', 'save_credential');
      addHidden(f, 'domain', targetDomain);
      addHidden(f, 'type', credType);
      if (extras) {
        Object.keys(extras).forEach(function(k) { addHidden(f, k, extras[k]); });
      }
      document.body.appendChild(f);
      f.requestSubmit();
    }
  }

  function addHidden(form, name, value) {
    var inp = document.createElement('input');
    inp.type = 'hidden';
    inp.name = name;
    inp.value = value;
    form.appendChild(inp);
  }

  // ===================================================================
  //  Portal dropdown
  // ===================================================================
  function createPortalDropdown() {
    var dd = document.createElement('div');
    dd.className = 'wa-url-dropdown wa-url-dropdown-portal';
    dd.style.display = 'none';
    document.body.appendChild(dd);
    return dd;
  }

  function positionPortal(dropdown, anchorEl) {
    var rect = anchorEl.getBoundingClientRect();
    var vh = window.innerHeight;
    var maxH = Math.min(320, vh - rect.bottom - 12);
    var below = vh - rect.bottom - 8;
    var above = rect.top - 8;

    dropdown.style.maxHeight = maxH + 'px';
    dropdown.style.width = rect.width + 'px';
    dropdown.style.left = rect.left + 'px';

    if (below >= 200 || below >= above) {
      dropdown.style.top = rect.bottom + 4 + 'px';
      dropdown.style.bottom = 'auto';
    } else {
      dropdown.style.bottom = (vh - rect.top + 4) + 'px';
      dropdown.style.top = 'auto';
    }
    dropdown.style.display = '';
  }

  // ===================================================================
  //  Type grayed state (visual only)
  // ===================================================================
  function setTypeGrayed(modal, type, grayed) {
    var label = modal.querySelector('.wa-type-option[data-cred-type="' + type + '"]');
    if (!label) return;
    label.classList.toggle('wa-type-grayed', grayed);
  }

  // ===================================================================
  //  Show/hide credential field panels
  // ===================================================================
  function showFieldPanel(modal, fieldType) {
    ['cookie', 'header', 'oauth2'].forEach(function(t) {
      var panel = modal.querySelector('[data-cred-field="' + t + '"]');
      if (!panel) return;
      panel.hidden = t !== fieldType;
    });
  }

  // ===================================================================
  //  Update type radios for a domain
  // ===================================================================
  function updateTypesForDomain(modal, domainKey) {
    var inf = allDomains.find(function(a) { return a.d === domainKey || a.domain === domainKey; }) || {};
    var autoType = null;
    if (inf.c) autoType = 'cookie';
    else if (inf.h && inf.h.length) autoType = 'header';
    else if (inf.t) autoType = 'oauth2';

    var localNeeded = (inf.c ? ['cookie'] : []).concat(
      (inf.h && inf.h.length ? ['header'] : []),
      (inf.t ? ['oauth2'] : [])
    );
    var hasReq = localNeeded.length > 0;

    ['cookie', 'header', 'oauth2'].forEach(function(t) {
      setTypeGrayed(modal, t, hasReq && localNeeded.indexOf(t) < 0);
    });

    if (autoType) {
      var radio = modal.querySelector('input[name="dlg-type"][value="' + autoType + '"]');
      if (radio) {
        radio.checked = true;
        radio.dispatchEvent(new Event('change', {bubbles: true}));
      }
    }
    return inf;
  }

  // ===================================================================
  //  Modal
  // ===================================================================
  var currentModal = null;
  var portalDropdown = null;
  var portalScrollHandler = null;

  function closeCurrentModal() {
    if (currentModal) {
      currentModal.remove();
      currentModal = null;
    }
    if (portalDropdown) {
      portalDropdown.remove();
      portalDropdown = null;
    }
    if (portalScrollHandler) {
      window.removeEventListener('scroll', portalScrollHandler, true);
      window.removeEventListener('resize', portalScrollHandler);
      portalScrollHandler = null;
    }
    modalOpen = false;
  }

  function showModal(title, domain, type) {
    var host = container.querySelector('.modals');
    if (!host || modalOpen) return;
    modalOpen = true;

    var tmpl = document.getElementById('credential-modal-template');
    if (!tmpl) return;

    host.innerHTML = '';
    var modal = tmpl.content.firstElementChild.cloneNode(true);
    host.appendChild(modal);
    currentModal = modal;

    var existingCred = null;
    if (domain && window.__ld_credentials_data) {
      existingCred = window.__ld_credentials_data.find(function(c) {
        return c.domain === domain && c.type === type;
      }) || null;
    }

    var titleEl = modal.querySelector('.wa-cred-modal-title');
    if (titleEl) titleEl.textContent = title;

    var info = domain ? (allDomains.find(function(d) { return d.d === domain || d.domain === domain; }) || {}) : {};
    var selectedType = type || 'cookie';
    if (domain && !type) {
      if (info.c) selectedType = 'cookie';
      else if (info.h && info.h.length) selectedType = 'header';
      else if (info.t) selectedType = 'oauth2';
    }

    var neededTypes = domain ? (
      (info.c ? ['cookie'] : []).concat(
        (info.h && info.h.length ? ['header'] : []),
        (info.t ? ['oauth2'] : [])
      )
    ) : ['cookie', 'header', 'oauth2'];
    var hasReq = neededTypes.length > 0 && domain;

    ['cookie', 'header', 'oauth2'].forEach(function(t) {
      var radio = modal.querySelector('input[name="dlg-type"][value="' + t + '"]');
      if (!radio) return;
      if (t === selectedType) radio.checked = true;
      setTypeGrayed(modal, t, hasReq && neededTypes.indexOf(t) < 0);
    });

    showFieldPanel(modal, selectedType);

    var domainGroup = modal.querySelector('[data-cred-domain-group]');
    var domainHidden = modal.querySelector('[data-cred-domain-hidden]');
    if (domain) {
      if (domainGroup) domainGroup.hidden = true;
      if (domainHidden) { domainHidden.hidden = false; domainHidden.value = domain; }
    } else {
      if (domainGroup) domainGroup.hidden = false;
      if (domainHidden) domainHidden.hidden = true;
    }

    if (existingCred) {
      var cookieEl = modal.querySelector('#dlg-cookie-value');
      if (cookieEl && existingCred.cookie) cookieEl.value = existingCred.cookie;
      var tokenEl = modal.querySelector('#dlg-oauth2-value');
      if (tokenEl && existingCred.oauth2) tokenEl.value = existingCred.oauth2;
    }

    var headerRows = modal.querySelector('#dlg-header-rows');
    if (headerRows) {
      var declared = info.h || info.needs_headers || [];
      var ev = (existingCred && existingCred.header_values) || {};
      declared.forEach(function(name) {
        addHeaderRow(headerRows, name, ev[name] || '', true);
      });
    }

    var addHdrBtn = modal.querySelector('#btn-add-header-row');
    if (addHdrBtn) {
      addHdrBtn.addEventListener('click', function() {
        addHeaderRow(headerRows, '', '', false);
      });
    }

    modal.querySelectorAll('input[name="dlg-type"]').forEach(function(r) {
      r.addEventListener('change', function() {
        showFieldPanel(modal, this.value);
      });
    });

    // Portal dropdown for domain autocomplete
    if (!domain) {
      var dInput = modal.querySelector('#dlg-domain');
      portalDropdown = createPortalDropdown();

      function renderDropdown() {
        var q = dInput.value.toLowerCase();
        var filtered = allDomains.filter(function(d) {
          var key = d.d || d.domain || '';
          return key.toLowerCase().indexOf(q) >= 0;
        });
        portalDropdown.innerHTML = '';
        if (!filtered.length) { portalDropdown.style.display = 'none'; return; }
        filtered.forEach(function(d) {
          var item = document.createElement('div');
          item.className = 'wa-url-dropdown-item';
          var labels = [];
          if (d.c || d.needs_cookie) { var cookieLabel = (d.ct === 'login') ? 'Cookie' : 'Cookie (auto)'; labels.push(cookieLabel); }
          if ((d.h && d.h.length) || (d.needs_headers && d.needs_headers.length)) labels.push('Header');
          if (d.t || d.needs_oauth2) labels.push('Token');
          item.innerHTML = '<span>' + escapeHtml(d.d || d.domain) + (labels.length ? ' <span class="text-gray" style="font-size:12px">(' + escapeHtml(labels.join(' + ')) + ')</span>' : '') + '</span>';
          item.addEventListener('mousedown', function(ev) {
            ev.preventDefault();
            dInput.value = d.d || d.domain;
            portalDropdown.style.display = 'none';
            var inf = updateTypesForDomain(modal, d.d || d.domain);
            buildHeaderRows(modal, inf, existingCred);
          });
          portalDropdown.appendChild(item);
        });
        positionPortal(portalDropdown, dInput);
      }

      if (dInput) {
        dInput.addEventListener('input', renderDropdown);
        dInput.addEventListener('focus', renderDropdown);
        dInput.addEventListener('blur', function() {
          setTimeout(function() { portalDropdown.style.display = 'none'; }, 200);
        });
      }

      portalScrollHandler = function() { renderDropdown(); };
      window.addEventListener('scroll', portalScrollHandler, true);
      window.addEventListener('resize', portalScrollHandler);
    }

    // Close
    modal.querySelector('.modal-overlay').addEventListener('click', closeCurrentModal);
    var clr = modal.querySelector('.btn-clear');
    if (clr) clr.addEventListener('click', closeCurrentModal);
    var cancel = modal.querySelector('#dlg-cancel');
    if (cancel) cancel.addEventListener('click', closeCurrentModal);

    // Save
    modal.querySelector('#dlg-save').addEventListener('click', function() {
      var d = domain || (modal.querySelector('#dlg-domain') ? modal.querySelector('#dlg-domain').value.trim() : '');
      if (!d) { toast(gettext('Please enter a domain'), 'error'); return; }
      var saveBtn = this;
      saveBtn.disabled = true;

      var tr = modal.querySelector('input[name="dlg-type"]:checked');
      var ct = tr ? tr.value : 'cookie';

      if (ct === 'cookie') {
        var val = modal.querySelector('#dlg-cookie-value').value.trim();
        if (!val) { toast(gettext('Please enter cookie value'), 'error'); saveBtn.disabled = false; return; }
        if (!isValidCookieFormat(val)) {
          toast(gettext('Invalid cookie format. Expected name=value pairs separated by semicolons.'), 'error');
          saveBtn.disabled = false;
          return;
        }
        submitCredentialForm(d, ct, {value: val});
      } else if (ct === 'token') {
        var tv = modal.querySelector('#dlg-oauth2-value').value.trim();
        if (!tv) { toast(gettext('Please enter refresh token'), 'error'); saveBtn.disabled = false; return; }
        submitCredentialForm(d, ct, {value: tv});
      } else {
        var rows = modal.querySelectorAll('#dlg-header-rows > .wa-header-row');
        var sub = 0;
        rows.forEach(function(row) {
          var inputs = row.querySelectorAll('input[type="text"]');
          if (inputs.length < 2) return;
          var hn, hv;
          var ns = row.querySelector('.wa-header-row-name');
          if (ns) { hn = ns.textContent.trim(); hv = inputs[0].value.trim(); }
          else { hn = inputs[0].value.trim(); hv = inputs[1].value.trim(); }
          if (!hn || !hv) return;
          sub++;
          submitCredentialForm(d, ct, {value: hv, header_name: hn});
        });
        if (!sub) toast(gettext('Please enter at least one header value'), 'error');
      }
      saveBtn.disabled = false;
    });
  }

  // ===================================================================
  //  Header rows
  // ===================================================================
  function escapeHtml(s) {
    return s ? String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;') : '';
  }

  function buildHeaderRows(modal, needs, existingCred) {
    var cnt = modal.querySelector('#dlg-header-rows');
    if (!cnt) return;
    cnt.innerHTML = '';
    var declared = needs.h || needs.needs_headers || [];
    var ev = (existingCred && existingCred.header_values) || {};
    declared.forEach(function(name) {
      addHeaderRow(cnt, name, ev[name] || '', true);
    });
  }

  function addHeaderRow(cnt, name, value, isDeclared) {
    var row = document.createElement('div');
    row.className = 'wa-header-row';

    var nameEl;
    if (isDeclared) {
      nameEl = document.createElement('span');
      nameEl.className = 'wa-header-row-name';
      nameEl.textContent = name;
    } else {
      nameEl = document.createElement('input');
      nameEl.type = 'text';
      nameEl.className = 'form-input wa-header-row-name-input';
      nameEl.placeholder = 'Header-Name';
      nameEl.value = name;
    }

    var valEl = document.createElement('input');
    valEl.type = 'text';
    valEl.className = 'form-input wa-header-row-value';
    valEl.placeholder = 'value';
    valEl.value = value;

    row.appendChild(nameEl);
    row.appendChild(valEl);

    if (!isDeclared) {
      var rmv = document.createElement('button');
      rmv.type = 'button';
      rmv.className = 'wa-header-row-delete';
      rmv.setAttribute('aria-label', 'Delete');
      rmv.innerHTML = '<svg width="16" height="16" aria-hidden="true"><use href="#ld-icon-delete"></use></svg>';
      rmv.addEventListener('click', function() { row.remove(); });
      row.appendChild(rmv);
    }
    cnt.appendChild(row);
  }
}

initAdapters();

if (!window.__ld_adapters_turbo_bound) {
  window.__ld_adapters_turbo_bound = true;
  document.addEventListener('turbo:load', function() { initAdapters(); });
}
