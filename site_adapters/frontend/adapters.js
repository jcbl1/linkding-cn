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

  function escapeHtml(s) {
    return s ? String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;') : '';
  }

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
  //  Toggle switch — optimistic update + fetch
  // ===================================================================
  container.addEventListener('change', function(e) {
    var cb = e.target.closest('.wa-toggle-pref');
    if (!cb) return;

    var domain = cb.dataset.domain;
    var toggleId = cb.dataset.toggle;
    var newValue = cb.checked;

    // Default value from data attribute; compare old & new states to
    // decide whether the toggle is departing from or returning to default.
    var defaultVal = cb.dataset.default === 'true';
    var wasModified = (!newValue) !== defaultVal;  // before the click
    var isModified = newValue !== defaultVal;       // after the click

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
  //  Toggle filter form — natural form submission via Turbo Frame
  // ===================================================================
  var filterForm = container.querySelector('#toggle-filter-form');
  if (filterForm) {
    // "Only modified" checkbox — auto-submit on change
    filterForm.addEventListener('change', function(e) {
      if (e.target.name === 'modified_only') {
        filterForm.requestSubmit();
      }
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
  //  Submit credential form helper
  // ===================================================================
  function submitCredentialForm(targetDomain, credType, extras) {
    var f = document.createElement('form');
    f.method = 'POST';
    f.action = window.location.pathname + window.location.search;
    f.style.display = 'none';
    var csrf = document.createElement('input');
    csrf.type = 'hidden';
    csrf.name = 'csrfmiddlewaretoken';
    csrf.value = csrfToken;
    f.appendChild(csrf);
    addHidden(f, 'action', 'save_credential');
    addHidden(f, 'domain', targetDomain);
    addHidden(f, 'type', credType);
    if (extras) {
      Object.keys(extras).forEach(function(k) {
        addHidden(f, k, extras[k]);
      });
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
  //  Modal
  // ===================================================================
  function showModal(title, domain, type) {
    var host = container.querySelector('.modals');
    if (!host) return;
    if (modalOpen) return;
    modalOpen = true;

    host.innerHTML = '';

    var existingCred = null;
    if (domain && window.__ld_credentials_data) {
      existingCred = window.__ld_credentials_data.find(function(c) {
        return c.domain === domain && c.type === type;
      }) || null;
    }

    var info = domain ? (allDomains.find(function(d) { return d.d === domain; }) || {}) : {};
    var needs = info;
    var availableTypes = [];
    if (needs.c) availableTypes.push('cookie');
    if (needs.h && needs.h.length) availableTypes.push('header');
    if (needs.t) availableTypes.push('token');
    var selectedType = type || (availableTypes[0] || 'cookie');

    var modal = document.createElement('div');
    modal.className = 'modal active wa-dialog';

    var html = '<div class="modal-overlay"></div>'
      + '<div class="modal-container" style="max-width:90vw;width:600px">'
      + '<div class="modal-header"><h2>' + escapeHtml(title) + '</h2><button class="btn btn-clear" aria-label="Close"></button></div>'
      + '<div class="modal-body">';

    if (availableTypes.length >= 1) {
      html += '<div class="form-group"><label class="form-label">Type</label><div style="display:flex;gap:12px">';
      availableTypes.forEach(function(t) {
        html += '<label><input type="radio" name="dlg-type" value="' + t + '" ' + (t === selectedType ? 'checked' : '') + '> ' + t.charAt(0).toUpperCase() + t.slice(1) + '</label>';
      });
      html += '</div></div>';
    } else {
      html += '<div class="form-group"><label class="form-label">Type</label><div style="display:flex;gap:12px">'
        + '<label><input type="radio" name="dlg-type" value="cookie" ' + (selectedType === 'cookie' ? 'checked' : '') + '> Cookie</label>'
        + '<label><input type="radio" name="dlg-type" value="header" ' + (selectedType === 'header' ? 'checked' : '') + '> Header</label>'
        + '<label><input type="radio" name="dlg-type" value="token" ' + (selectedType === 'token' ? 'checked' : '') + '> Token</label>'
        + '</div></div>';
    }

    if (domain) {
      html += '<p style="margin-bottom:12px"><strong>' + escapeHtml(domain) + '</strong></p>'
        + '<input type="hidden" name="dlg-domain" value="' + escapeHtml(domain) + '">';
    } else {
      html += '<div class="form-group"><label class="form-label">Domain</label>'
        + '<div style="position:relative"><input type="text" class="form-input" id="dlg-domain" placeholder="example.com" autocomplete="off">'
        + '<div id="dlg-domain-dropdown" class="wa-url-dropdown"></div></div></div>';
    }

    html += '<div id="dlg-cookie-fields" style="display:' + (selectedType === 'cookie' ? 'block' : 'none') + '">'
      + '<div class="form-group"><label class="form-label">Cookie</label>'
      + '<textarea class="form-input" id="dlg-cookie-value" rows="3" placeholder="name1=value1; name2=value2">' + escapeHtml(existingCred && existingCred.cookie || '') + '</textarea></div></div>';

    html += '<div id="dlg-header-fields" style="display:' + (selectedType === 'header' ? 'block' : 'none') + '">'
      + '<div id="dlg-header-rows"></div>'
      + '<button class="btn btn-sm" id="btn-add-header-row" style="margin-top:8px" type="button">+ Add header</button></div>';

    html += '<div id="dlg-token-fields" style="display:' + (selectedType === 'token' ? 'block' : 'none') + '">'
      + '<div class="form-group"><label class="form-label">Refresh Token</label>'
      + '<textarea class="form-input" id="dlg-token-value" rows="2" placeholder="Paste your refresh_token here">' + escapeHtml(existingCred && existingCred.token || '') + '</textarea></div></div>';

    html += '</div>'
      + '<div class="wa-modal-footer"><button class="btn" id="dlg-cancel" type="button">Cancel</button><button class="btn btn-primary" id="dlg-save" type="button">Save</button></div>'
      + '</div>';

    modal.innerHTML = html;

    buildHeaderRows(modal, needs, existingCred);

    var addHdr = modal.querySelector('#btn-add-header-row');
    if (addHdr) {
      addHdr.addEventListener('click', function() {
        addHeaderRow(modal.querySelector('#dlg-header-rows'), '', '', false);
      });
    }

    modal.querySelectorAll('input[name="dlg-type"]').forEach(function(r) {
      r.addEventListener('change', function() {
        var cf = modal.querySelector('#dlg-cookie-fields');
        var hf = modal.querySelector('#dlg-header-fields');
        var tf = modal.querySelector('#dlg-token-fields');
        if (cf) cf.style.display = this.value === 'cookie' ? 'block' : 'none';
        if (hf) hf.style.display = this.value === 'header' ? 'block' : 'none';
        if (tf) tf.style.display = this.value === 'token' ? 'block' : 'none';
      });
    });

    if (!domain) {
      var dInput = modal.querySelector('#dlg-domain');
      var dropdown = modal.querySelector('#dlg-domain-dropdown');

      function updateTypes(d) {
        var inf = allDomains.find(function(a) { return a.d === d; }) || {};
        var types = [];
        if (inf.c) types.push('cookie');
        if (inf.h && inf.h.length) types.push('header');
        if (inf.t) types.push('token');
        if (!types.length) {
          modal.querySelectorAll('input[name="dlg-type"]').forEach(function(r) {
            r.closest('label').style.display = '';
          });
          return;
        }
        modal.querySelectorAll('input[name="dlg-type"]').forEach(function(r) {
          r.closest('label').style.display = types.indexOf(r.value) >= 0 ? '' : 'none';
        });
        var first = modal.querySelector('input[name="dlg-type"][value="' + types[0] + '"]');
        if (first) {
          first.checked = true;
          first.dispatchEvent(new Event('change', {bubbles: true}));
        }
        needs = inf;
        buildHeaderRows(modal, needs, existingCred);
      }

      function showDropdown() {
        var q = dInput.value.toLowerCase();
        var filtered = allDomains.filter(function(d) { return d.d.toLowerCase().indexOf(q) >= 0; });
        dropdown.innerHTML = '';
        if (!filtered.length) { dropdown.classList.remove('open'); return; }
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
            dropdown.classList.remove('open');
            updateTypes(d.d);
          });
          dropdown.appendChild(item);
        });
        dropdown.classList.add('open');
      }

      if (dInput) {
        dInput.addEventListener('input', showDropdown);
        dInput.addEventListener('focus', showDropdown);
        dInput.addEventListener('blur', function() {
          setTimeout(function() { dropdown.classList.remove('open'); }, 200);
        });
      }
    }

    function close() {
      modal.remove();
      modalOpen = false;
    }
    modal.querySelector('.modal-overlay').addEventListener('click', close);
    var clr = modal.querySelector('.btn-clear');
    if (clr) clr.addEventListener('click', close);
    var cancel = modal.querySelector('#dlg-cancel');
    if (cancel) cancel.addEventListener('click', close);

    modal.querySelector('#dlg-save').addEventListener('click', function() {
      var d = domain || (modal.querySelector('#dlg-domain') ? modal.querySelector('#dlg-domain').value.trim() : '');
      if (!d) { alert('Please enter a domain'); return; }
      var saveBtn = this;
      saveBtn.disabled = true;

      var tr = modal.querySelector('input[name="dlg-type"]:checked');
      var ct = tr ? tr.value : 'cookie';

      if (ct === 'cookie') {
        var val = modal.querySelector('#dlg-cookie-value').value.trim();
        if (!val) { alert('Please enter cookie value'); saveBtn.disabled = false; return; }
        submitCredentialForm(d, ct, {value: val});
      } else if (ct === 'token') {
        var tv = modal.querySelector('#dlg-token-value').value.trim();
        if (!tv) { alert('Please enter refresh token'); saveBtn.disabled = false; return; }
        submitCredentialForm(d, ct, {value: tv});
      } else {
        var rows = modal.querySelectorAll('#dlg-header-rows > div');
        var sub = 0;
        rows.forEach(function(row) {
          var inputs = row.querySelectorAll('input[type="text"]');
          if (inputs.length < 2) return;
          var hn, hv;
          var ns = row.querySelector('span');
          if (ns) { hn = ns.textContent.trim(); hv = inputs[0].value.trim(); }
          else { hn = inputs[0].value.trim(); hv = inputs[1].value.trim(); }
          if (!hn || !hv) return;
          sub++;
          submitCredentialForm(d, ct, {value: hv, header_name: hn});
        });
        if (!sub) alert('Please enter at least one header value');
      }
      saveBtn.disabled = false;
    });

    host.appendChild(modal);
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
    row.style.cssText = 'display:flex;gap:8px;align-items:center;margin-bottom:8px';
    var nameEl;
    if (isDeclared) {
      nameEl = document.createElement('span');
      nameEl.style.cssText = 'white-space:nowrap;font-family:monospace;font-size:13px';
      nameEl.textContent = name;
    } else {
      nameEl = document.createElement('input');
      nameEl.type = 'text';
      nameEl.className = 'form-input';
      nameEl.style.width = '180px';
      nameEl.placeholder = 'Header-Name';
      nameEl.value = name;
    }
    var valEl = document.createElement('input');
    valEl.type = 'text';
    valEl.className = 'form-input';
    valEl.style.cssText = 'flex:1;min-width:0';
    valEl.placeholder = 'value';
    valEl.value = value;
    row.appendChild(nameEl);
    row.appendChild(valEl);
    if (!isDeclared) {
      var rmv = document.createElement('button');
      rmv.className = 'btn btn-sm btn-error';
      rmv.textContent = '\u00d7';
      rmv.style.whiteSpace = 'nowrap';
      rmv.addEventListener('click', function() { row.remove(); });
      row.appendChild(rmv);
    }
    cnt.appendChild(row);
  }
}

// ── Run on initial load ──
initAdapters();

// ── Re-run on Turbo Drive navigations (only register listener once) ──
if (!window.__ld_adapters_turbo_bound) {
  window.__ld_adapters_turbo_bound = true;
  document.addEventListener('turbo:load', function() {
    initAdapters();
  });
}
