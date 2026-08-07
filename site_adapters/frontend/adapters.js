import { gettext } from "./utils/i18n.js";

function initAdapters() {
  var csrfToken = window.__ld_csrf_token || '';

  var container = document.querySelector('[ld-site-adapters]');
  if (!container) return;
  if (container.dataset.adaptersReady) return;
  container.dataset.adaptersReady = '1';

  var allDomains = window.__ld_auth_domains || [];
  var togglesUrl = window.__ld_snapshot_toggles_url || '/settings/adapters/snapshot_toggles';
  var modalOpen = false;

  // ===================================================================
  //  Toast helper
  // ===================================================================
  function toast(msg, tone) {
    if (typeof window.showToast === 'function') {
      window.showToast(msg, { tone: tone || 'info' });
    }
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

  // Cleanup stale event handlers from Turbo cache restoration
  container.querySelectorAll('.wa-toggle-domain-bar').forEach(function(b) { b.onclick = null; });

  // ===================================================================
  //  Expand / collapse
  // ===================================================================
  container.addEventListener('click', function(e) {
    var bar = e.target.closest('.wa-toggle-domain-bar');
    if (!bar) return;
    var row = bar.closest('.wa-toggle-domain-row');
    row.classList.toggle('expanded');
  });

  // ===================================================================
  //  Toggle switch
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
  //  Toggle filter form
  // ===================================================================
  var filterForm = container.querySelector('#toggle-filter-form');
  if (filterForm) {
    filterForm.addEventListener('change', function(e) {
      if (e.target.name === 'modified_only') filterForm.requestSubmit();
    });
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
      var f = document.createElement('form');
      f.method = 'POST';
      f.action = window.location.pathname + window.location.search;
      f.style.display = 'none';
      addHidden(f, 'csrfmiddlewaretoken', csrfToken);
      addHidden(f, 'action', 'delete_credential');
      addHidden(f, 'domain', btn.dataset.domain);
      addHidden(f, 'type', btn.dataset.type);
      if (btn.dataset.headerName) addHidden(f, 'header_name', btn.dataset.headerName);
      document.body.appendChild(f);
      f.requestSubmit();
    };
    btn.setAttribute('ld-confirm-question', gettext('Delete this credential?'));
    btn.setAttribute('ld-confirm-danger', '');
    document.body.appendChild(popup);
  });

  // ===================================================================
  //  Helpers
  // ===================================================================
  function submitCredentialForm(targetDomain, credType, extras) {
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

  function addHidden(form, name, value) {
    var inp = document.createElement('input');
    inp.type = 'hidden';
    inp.name = name;
    inp.value = value;
    form.appendChild(inp);
  }

  // ===================================================================
  //  Portal dropdown (avoids modal overflow clipping)
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
  //  Update type grayed state (visual only, still clickable)
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
    ['cookie', 'header', 'token'].forEach(function(t) {
      var panel = modal.querySelector('[data-cred-field="' + t + '"]');
      if (!panel) return;
      panel.hidden = t !== fieldType;
    });
  }

  // ===================================================================
  //  Set type radios state for a domain
  // ===================================================================
  function updateTypesForDomain(modal, domainKey) {
    var inf = allDomains.find(function(a) { return a.d === domainKey; }) || {};
    var autoType = null;
    if (inf.c) autoType = 'cookie';
    else if (inf.h && inf.h.length) autoType = 'header';
    else if (inf.t) autoType = 'token';

    var localNeeded = (inf.c ? ['cookie'] : []).concat(
      (inf.h && inf.h.length ? ['header'] : []),
      (inf.t ? ['token'] : [])
    );
    var hasReq = localNeeded.length > 0;

    ['cookie', 'header', 'token'].forEach(function(t) {
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
  //  Modal — built from <template>
  // ===================================================================
  function showModal(title, domain, type) {
    var host = container.querySelector('.modals');
    if (!host || modalOpen) return;
    modalOpen = true;

    var tmpl = document.getElementById('credential-modal-template');
    if (!tmpl) return;

    host.innerHTML = '';
    var modal = tmpl.content.firstElementChild.cloneNode(true);
    host.appendChild(modal);

    // Find existing credential for edit mode
    var existingCred = null;
    if (domain && window.__ld_credentials_data) {
      existingCred = window.__ld_credentials_data.find(function(c) {
        return c.domain === domain && c.type === type;
      }) || null;
    }

    // Set title
    var titleEl = modal.querySelector('.wa-cred-modal-title');
    if (titleEl) titleEl.textContent = title;

    // Determine initial type
    var info = domain ? (allDomains.find(function(d) { return d.d === domain; }) || {}) : {};
    var selectedType = type || 'cookie';
    if (domain && !type) {
      if (info.c) selectedType = 'cookie';
      else if (info.h && info.h.length) selectedType = 'header';
      else if (info.t) selectedType = 'token';
    }

    // Set type radios: check selected, gray non-needed
    var neededTypes = domain ? (
      (info.c ? ['cookie'] : []).concat(
        (info.h && info.h.length ? ['header'] : []),
        (info.t ? ['token'] : [])
      )
    ) : ['cookie', 'header', 'token'];
    var hasReq = neededTypes.length > 0 && domain;

    ['cookie', 'header', 'token'].forEach(function(t) {
      var radio = modal.querySelector('input[name="dlg-type"][value="' + t + '"]');
      if (!radio) return;
      if (t === selectedType) radio.checked = true;
      setTypeGrayed(modal, t, hasReq && neededTypes.indexOf(t) < 0);
    });

    // Show selected field panel
    showFieldPanel(modal, selectedType);

    // Domain: hide group + show hidden input in edit mode
    var domainGroup = modal.querySelector('[data-cred-domain-group]');
    var domainHidden = modal.querySelector('[data-cred-domain-hidden]');
    if (domain) {
      if (domainGroup) domainGroup.hidden = true;
      if (domainHidden) { domainHidden.hidden = false; domainHidden.value = domain; }
    } else {
      if (domainGroup) domainGroup.hidden = false;
      if (domainHidden) domainHidden.hidden = true;
    }

    // Pre-fill values from existing credential
    if (existingCred) {
      var cookieEl = modal.querySelector('#dlg-cookie-value');
      if (cookieEl && existingCred.cookie) cookieEl.value = existingCred.cookie;
      var tokenEl = modal.querySelector('#dlg-token-value');
      if (tokenEl && existingCred.token) tokenEl.value = existingCred.token;
    }

    // Build header rows
    var headerRows = modal.querySelector('#dlg-header-rows');
    if (headerRows) {
      var declared = info.h || [];
      var ev = (existingCred && existingCred.header_values) || {};
      declared.forEach(function(name) {
        addHeaderRow(headerRows, name, ev[name] || '', true);
      });
    }

    // Add header row button
    var addHdrBtn = modal.querySelector('#btn-add-header-row');
    if (addHdrBtn) {
      addHdrBtn.addEventListener('click', function() {
        addHeaderRow(headerRows, '', '', false);
      });
    }

    // Type change → show relevant field panel
    modal.querySelectorAll('input[name="dlg-type"]').forEach(function(r) {
      r.addEventListener('change', function() {
        showFieldPanel(modal, this.value);
      });
    });

    // ── Portal dropdown for domain autocomplete (add mode only) ──
    var portalDropdown = null;
    var portalScrollHandler = null;

    if (!domain) {
      var dInput = modal.querySelector('#dlg-domain');
      portalDropdown = createPortalDropdown();

      function renderDropdown() {
        var q = dInput.value.toLowerCase();
        var filtered = allDomains.filter(function(d) { return d.d.toLowerCase().indexOf(q) >= 0; });
        portalDropdown.innerHTML = '';
        if (!filtered.length) { portalDropdown.style.display = 'none'; return; }
        filtered.forEach(function(d) {
          var item = document.createElement('div');
          item.className = 'wa-url-dropdown-item';
          var labels = [];
          if (d.c) labels.push('Cookie');
          if (d.h && d.h.length) labels.push('Header');
          if (d.t) labels.push('Token');
          item.innerHTML = '<span>' + escapeHtml(d.d) + (labels.length ? ' <span class="text-gray" style="font-size:12px">(' + escapeHtml(labels.join(' + ')) + ')</span>' : '') + '</span>';
          item.addEventListener('mousedown', function(ev) {
            ev.preventDefault();
            dInput.value = d.d;
            portalDropdown.style.display = 'none';
            var inf = updateTypesForDomain(modal, d.d);
            // Rebuild header rows
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

    // ── Close ──
    function close() {
      modal.remove();
      if (portalDropdown) {
        portalDropdown.remove();
        if (portalScrollHandler) {
          window.removeEventListener('scroll', portalScrollHandler, true);
          window.removeEventListener('resize', portalScrollHandler);
        }
      }
      modalOpen = false;
    }
    modal.querySelector('.modal-overlay').addEventListener('click', close);
    var clr = modal.querySelector('.btn-clear');
    if (clr) clr.addEventListener('click', close);
    var cancel = modal.querySelector('#dlg-cancel');
    if (cancel) cancel.addEventListener('click', close);

    // ── Save ──
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
        var tv = modal.querySelector('#dlg-token-value').value.trim();
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
    var declared = needs.h || [];
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

// ── Run on initial load ──
initAdapters();

// ── Re-run on Turbo Drive navigations ──
if (!window.__ld_adapters_turbo_bound) {
  window.__ld_adapters_turbo_bound = true;
  document.addEventListener('turbo:load', function() { initAdapters(); });
}
