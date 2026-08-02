(function () {
  'use strict';

  function gettext(s) { return typeof window.gettext === 'function' ? window.gettext(s) : s; }
  function esc(s) { return s ? String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;') : ''; }

  var csrf = document.querySelector('input[name="csrfmiddlewaretoken"]');
  var csrfToken = csrf ? csrf.value : '';
  var urls = window.__ld_urls || {};
  var TAB_KEY = "ld:site-adapters-tab";
var MODE = (function () { try { return localStorage.getItem(TAB_KEY) || "subscriptions"; } catch (e) { return "subscriptions"; } })();

  function apiPost(url, data) {
    var fd = new FormData();
    for (var k in data) { if (data.hasOwnProperty(k)) fd.append(k, data[k]); }
    return fetch(url, { method: 'POST', headers: { 'X-CSRFToken': csrfToken }, body: fd }).then(function (r) { return r.json(); });
  }
  function apiGet(url) {
    return fetch(url, { headers: { 'X-CSRFToken': csrfToken } }).then(function (r) { return r.json(); });
  }
  // 使用 main 分支统一 toast 系统
  function toast(msg, tone) {
    if (typeof window.showToast === 'function') {
      window.showToast(msg, { tone: tone || 'info' });
    }
  }

  // ===== Mode =====
  function switchMode(mode) {
    MODE = mode;
    try { localStorage.setItem(TAB_KEY, mode); } catch (e) {}
    document.querySelectorAll('[name="view-mode"]').forEach(function (r) { r.checked = r.value === mode; });
    document.getElementById('view-subscriptions').hidden = mode !== 'subscriptions';
    document.getElementById('view-domains').hidden = mode !== 'domains';
    document.getElementById('view-resources').hidden = mode !== 'resources';
    if (mode === 'subscriptions') loadSubscriptions();
    if (mode === 'domains') loadDomainList();
    if (mode === 'resources') loadResourceTree('');
  }
  document.querySelectorAll('[name="view-mode"]').forEach(function (r) {
    r.addEventListener('change', function () { switchMode(this.value); });
  });

  // ===== Domain list =====
  var domainEntries = [];
  function loadDomainList() {
    apiGet(urls.domainsAll).then(function (data) {
      domainEntries = [];
      (data.local || []).forEach(function (key) {
        domainEntries.push({ domain_key: key, filename: key + '.jsonc', source: 'local', is_alias: false, target: '', requires_cookie: false, has_cookie: false, disabled: false });
      });
      (data.subscriptions || []).forEach(function (sub) {
        (sub.domains || []).forEach(function (d) {
          domainEntries.push({ domain_key: d.domain, filename: d.domain + '.jsonc', source: 'subscription', sub_name: sub.name, is_alias: false, target: '', requires_cookie: false, has_cookie: false, disabled: !d.enabled || d.overridden });
        });
      });
      renderDomainList();
    }).catch(function (e) { console.error('loadDomainList:', e); });
  }

  var domainSortAsc = true, activeDomainFile = null;
  function renderDomainList(filter) {
    var list = document.getElementById('domain-list'); if (!list) return;
    var h = '<div class="wa-domain-item wa-domain-global" data-filename="global.jsonc" data-domain="*" data-is-global="1"><code>*</code><small class="wa-domain-note">' + gettext('global defaults') + '</small></div>';
    var items = domainEntries.slice();
    if (filter) { var q = filter.toLowerCase(); items = items.filter(function (x) { return x.domain_key.toLowerCase().indexOf(q) >= 0; }); }
    if (!domainSortAsc) items.reverse();
    items.forEach(function (d) {
      var cls = 'wa-domain-item' + (d.disabled ? ' wa-domain-sub-disabled' : '') + (activeDomainFile && activeDomainFile.filename === d.filename ? ' active' : '');
      h += '<div class="' + cls + '" data-filename="' + esc(d.filename) + '" data-domain="' + esc(d.domain_key) + '">';
      h += '<code>' + esc(d.domain_key) + '</code>';
      if (d.is_alias && d.target) h += '<small class="wa-alias-target">&rarr; ' + esc(d.target) + '</small>';
      h += '<label class="form-switch wa-domain-local-switch"><input type="checkbox" ' + (d.disabled ? '' : 'checked') + ' data-local-domain="' + esc(d.domain_key) + '"><i class="form-icon"></i></label>';
      h += '</div>';
    });
    list.innerHTML = h;
    list.querySelectorAll('.wa-domain-item').forEach(function (el) {
      el.addEventListener('click', function (e) { if (!e.target.closest('input')) selectDomain(this.dataset.filename); });
    });
    list.querySelectorAll('input[data-local-domain]').forEach(function (cb) {
      cb.addEventListener('change', function () { apiPost(urls.localDomainToggle, { domain: this.dataset.localDomain, enabled: this.checked ? '1' : '0' }).then(function (r) { if (r.error) toast(r.error, 'error'); }); });
    });
  }
  function selectDomain(fn) {
    activeDomainFile = domainEntries.find(function (d) { return d.filename === fn; }) || { filename: fn, domain_key: fn.replace(/\.(jsonc|json)$/, '') };
    renderDomainList(document.getElementById('domain-search').value);
    document.getElementById('domain-editor-header').hidden = false;
    document.getElementById('editor-empty').hidden = true;
    document.getElementById('editor-content').hidden = false;
    document.getElementById('editor-filename').textContent = fn;
    document.getElementById('btn-delete-domain').hidden = (fn === 'global.jsonc');
    document.getElementById('cookie-banner').hidden = true;
    apiGet(urls.domainRead + '?filename=' + encodeURIComponent(fn)).then(function (d) {
      var c = document.getElementById('cm-domain-container'); c.textContent = d.content || '';
      c.style.cssText = 'font-family:monospace;font-size:13px;white-space:pre-wrap;padding:12px;min-height:300px;outline:none;overflow:auto;background:var(--wa-surface-alt);';
      c.contentEditable = 'true'; c.setAttribute('spellcheck', 'false');
    });
  }
  document.getElementById('domain-search').addEventListener('input', function () { renderDomainList(this.value); });
  document.getElementById('btn-sort-domains').addEventListener('click', function () { domainSortAsc = !domainSortAsc; renderDomainList(document.getElementById('domain-search').value); });
  document.getElementById('btn-save-domain').addEventListener('click', function () {
    if (!activeDomainFile) return;
    apiPost(urls.domainSave, { filename: activeDomainFile.filename, content: document.getElementById('cm-domain-container').textContent || '' })
      .then(function (r) { if (r.error) toast(r.error, 'error'); else { toast(gettext('Saved'), 'success'); loadDomainList(); } });
  });
  document.getElementById('btn-delete-domain').addEventListener('click', function () {
    if (!activeDomainFile || activeDomainFile.filename === 'global.jsonc') return;
    if (!confirm(gettext('Delete') + ' ' + activeDomainFile.domain_key + '?')) return;
    apiPost(urls.domainDelete, { filename: activeDomainFile.filename }).then(function (r) {
      if (r.error) { toast(r.error, 'error'); return; }
      activeDomainFile = null; document.getElementById('domain-editor-header').hidden = true;
      document.getElementById('editor-empty').hidden = false; document.getElementById('editor-content').hidden = true;
      document.getElementById('cookie-banner').hidden = true; loadDomainList();
    });
  });
  document.getElementById('btn-add-domain').addEventListener('click', function () {
    var n = prompt(gettext('Domain name (e.g. example.com):')); if (!n) return;
    apiPost(urls.domainCreate, { domain_key: n }).then(function (r) { if (r.error) toast(r.error, 'error'); else loadDomainList(); });
  });
  document.getElementById('btn-rename-domain').addEventListener('click', function () {
    if (!activeDomainFile || activeDomainFile.filename === 'global.jsonc') return;
    var nn = prompt(gettext('New domain name:'), activeDomainFile.domain_key);
    if (!nn || nn === activeDomainFile.domain_key) return;
    apiPost(urls.domainRename, { old_filename: activeDomainFile.filename, new_domain: nn }).then(function (r) {
      if (r.error) toast(r.error, 'error'); else { activeDomainFile = null; loadDomainList(); }
    });
  });
  document.getElementById('btn-copy-to-local').addEventListener('click', function () {
    if (!activeDomainFile) return;
    apiPost(urls.domainSave, { filename: activeDomainFile.filename, action: 'copy_to_local' })
      .then(function (r) { if (r.error) toast(r.error, 'error'); else toast(gettext('Copied to local'), 'success'); });
  });
  document.getElementById('btn-validate').addEventListener('click', function () {
    if (!activeDomainFile) return;
    apiPost(urls.action, { action: 'validate', filename: activeDomainFile.filename, content: document.getElementById('cm-domain-container').textContent || '' })
      .then(function (r) {
        document.getElementById('domain-validate-results').hidden = false;
        document.getElementById('domain-validate-status').textContent = r.error ? gettext('Invalid') : gettext('Valid');
        document.getElementById('domain-validate-status').style.color = r.error ? 'red' : 'green';
        document.getElementById('domain-validate-output').textContent = r.error || r.message || JSON.stringify(r, null, 2);
      });
  });
  document.getElementById('btn-close-domain-validate').addEventListener('click', function () { document.getElementById('domain-validate-results').hidden = true; });
  document.getElementById('btn-paste-cookie').addEventListener('click', function () {
    if (!activeDomainFile) return;
    var c = prompt(gettext('Paste cookie string:')); if (!c) return;
    apiPost(urls.saveCookie, { domain: activeDomainFile.domain_key, cookie: c })
      .then(function (r) { if (r.error) toast(r.error, 'error'); else { toast(gettext('Cookie saved'), 'success'); loadDomainList(); } });
  });

  // ===== Subscriptions =====
  function loadSubscriptions() { apiGet(urls.subscriptionManage).then(function (d) { renderSubscriptions(d.subscriptions || []); }).catch(function (e) { console.error('loadSubscriptions:', e); }); }
  function renderSubscriptions(subs) {
    var list = document.getElementById('subscription-list'); if (!list) return;
    if (!subs.length) { list.innerHTML = '<p class="text-gray" style="padding:16px">' + gettext('No subscriptions.') + '</p>'; return; }
    var h = '';
    subs.forEach(function (s) {
      h += '<div class="wa-cookie-row" style="padding:10px 12px"><div class="wa-cookie-header"><strong>' + esc(s.name || s.url) + '</strong><code style="font-size:11px;color:var(--wa-muted)">' + esc(s.url) + '</code><div style="margin-left:auto;display:flex;gap:4px"><button class="btn btn-sm js-sub-fetch" data-url="' + esc(s.url) + '">' + gettext('Fetch') + '</button><button class="btn btn-sm btn-error js-sub-delete" data-url="' + esc(s.url) + '">' + gettext('Delete') + '</button></div></div></div>';
    });
    list.innerHTML = h;
    list.querySelectorAll('.js-sub-fetch').forEach(function (b) { b.addEventListener('click', function () { apiPost(urls.subscriptionManage, { action: 'update', url: this.dataset.url }).then(function (r) { if (r.error) toast(r.error, 'error'); else toast(gettext('Fetched'), 'success'); }); }); });
    list.querySelectorAll('.js-sub-delete').forEach(function (b) { b.addEventListener('click', function () { if (confirm(gettext('Delete subscription') + '?')) apiPost(urls.subscriptionManage, { action: 'delete', url: this.dataset.url }).then(function (r) { if (r.error) toast(r.error, 'error'); else loadSubscriptions(); }); }); });
  }
  document.getElementById('btn-add-subscription').addEventListener('click', function () {
    var url = prompt(gettext('Subscription URL:')); if (!url) return; var name = prompt(gettext('Name (optional):'));
    var data = { action: 'add', url: url }; if (name) data.name = name;
    apiPost(urls.subscriptionManage, data).then(function (r) { if (r.error) toast(r.error, 'error'); else loadSubscriptions(); });
  });

  // ===== Resources =====
  function loadResourceTree(path) {
    path = path || '';
    apiGet(urls.resources + (path ? '?path=' + encodeURIComponent(path) : '')).then(function (d) {
      renderResourceTree(d.path || '', d.items || []);
    }).catch(function (e) { console.error('loadResourceTree:', e); });
  }
  function renderResourceTree(currentPath, items) {
    var tree = document.getElementById('resource-tree'); if (!tree) return;
    if (!items.length) { tree.innerHTML = '<p style="padding:12px;color:var(--wa-muted);font-size:13px">' + gettext('No files.') + '</p>'; return; }
    var h = '';
    if (currentPath) {
      var parent = currentPath.replace(/\/[^/]*\/?$/, '') || '';
      h += '<div class="wa-domain-item js-resource-dir" data-path="' + esc(parent) + '" style="color:var(--wa-accent)">&#x1F4C1; ..</div>';
    }
    items.forEach(function (item) {
      var full = currentPath ? currentPath + '/' + item.name : item.name;
      if (item.is_dir) {
        h += '<div class="wa-domain-item js-resource-dir" data-path="' + esc(full) + '">&#x1F4C1; <span>' + esc(item.name) + '/</span></div>';
      } else {
        h += '<div class="wa-domain-item js-resource-file" data-path="' + esc(full) + '">&#x1F4C4; <span>' + esc(item.name) + '</span></div>';
      }
    });
    tree.innerHTML = h;
    tree.querySelectorAll('.js-resource-dir').forEach(function (el) { el.addEventListener('click', function () { loadResourceTree(this.dataset.path); }); });
    tree.querySelectorAll('.js-resource-file').forEach(function (el) { el.addEventListener('click', function () { loadResourceContent(this.dataset.path); }); });
  }
  function loadResourceContent(path) {
    document.getElementById('resource-empty').hidden = true; document.getElementById('resource-content').hidden = false;
    document.getElementById('resource-editor-header').hidden = false; document.getElementById('resource-filename').textContent = path;
    document.getElementById('btn-save-resource').hidden = false; document.getElementById('btn-resource-delete').hidden = false;
    document.getElementById('btn-resource-rename').hidden = false;
    apiGet(urls.resources + '?path=' + encodeURIComponent(path)).then(function (d) {
      var c = document.getElementById('cm-resource-container');
      if (d.error) { c.textContent = d.error; return; }
      c.textContent = d.content || '';
      c.style.cssText = 'font-family:monospace;font-size:13px;white-space:pre-wrap;padding:12px;min-height:300px;outline:none;overflow:auto;background:var(--wa-surface-alt);';
      c.contentEditable = 'true'; c.setAttribute('spellcheck', 'false'); c.dataset.currentPath = path;
    });
  }
  document.getElementById('btn-save-resource').addEventListener('click', function () {
    var p = document.getElementById('cm-resource-container').dataset.currentPath; if (!p) return;
    apiPost(urls.resourceSave, { path: p, content: document.getElementById('cm-resource-container').textContent || '' })
      .then(function (r) { if (r.error) toast(r.error, 'error'); else toast(gettext('Saved'), 'success'); });
  });
  document.getElementById('btn-resource-delete').addEventListener('click', function () {
    var p = document.getElementById('cm-resource-container').dataset.currentPath; if (!p || !confirm(gettext('Delete') + ' ' + p + '?')) return;
    apiPost(urls.resourceManage, { action: 'delete', path: p }).then(function (r) {
      if (r.error) { toast(r.error, 'error'); return; }
      document.getElementById('resource-empty').hidden = false; document.getElementById('resource-content').hidden = true;
      document.getElementById('resource-editor-header').hidden = true; document.getElementById('btn-save-resource').hidden = true;
      document.getElementById('btn-resource-delete').hidden = true; document.getElementById('btn-resource-rename').hidden = true;
      loadResourceTree('');
    });
  });
  document.getElementById('btn-resource-rename').addEventListener('click', function () {
    var o = document.getElementById('cm-resource-container').dataset.currentPath; if (!o) return;
    var nn = prompt(gettext('New name:'), o); if (!nn || nn === o) return;
    // Extract just the basename as 'name' parameter
    var newName = nn.indexOf('/') >= 0 ? nn.substring(nn.lastIndexOf('/') + 1) : nn;
    apiPost(urls.resourceManage, { action: 'rename', path: o, name: newName }).then(function (r) {
      if (r.error) { toast(r.error, 'error'); return; }
      var newPath = r.path || (o.substring(0, o.lastIndexOf('/') + 1) + newName);
      document.getElementById('cm-resource-container').dataset.currentPath = newPath;
      document.getElementById('resource-filename').textContent = newPath;
    });
  });
  document.getElementById('btn-resource-new-file').addEventListener('click', function () {
    var p = prompt(gettext('File path (e.g. scripts/hello.js):')); if (!p) return;
    var lastSlash = p.lastIndexOf('/');
    var dir = lastSlash >= 0 ? p.substring(0, lastSlash) : '';
    var name = lastSlash >= 0 ? p.substring(lastSlash + 1) : p;
    apiPost(urls.resourceManage, { action: 'create_file', path: dir, name: name }).then(function (r) { if (r.error) toast(r.error, 'error'); else { loadResourceTree(dir); loadResourceContent(p); } });
  });
  document.getElementById('btn-resource-new-folder').addEventListener('click', function () {
    var p = prompt(gettext('Folder path (e.g. scripts/):')); if (!p) return;
    p = p.replace(/\/+$/, '');
    var lastSlash = p.lastIndexOf('/');
    var dir = lastSlash >= 0 ? p.substring(0, lastSlash) : '';
    var name = lastSlash >= 0 ? p.substring(lastSlash + 1) : p;
    apiPost(urls.resourceManage, { action: 'create_dir', path: dir, name: name }).then(function (r) { if (r.error) toast(r.error, 'error'); else loadResourceTree(''); });
  });

  // ===== URL Test =====
  var URL_HISTORY_KEY = 'ld:test-urls', RESULT_KEY = 'ld:test-result';
  var testHistory = [];
  try { testHistory = JSON.parse(localStorage.getItem(URL_HISTORY_KEY) || '[]'); } catch (e) {}
  var testUrlInput = document.getElementById('test-url');
  var testDropdown = document.getElementById('wa-url-dropdown');
  var resultsSection = document.getElementById('test-results');
  var testOutput = document.getElementById('test-output');
  var testStatus = document.getElementById('test-status');

  function restoreResult() {
    try { var s = JSON.parse(localStorage.getItem(RESULT_KEY)); if (s && s.output) { resultsSection.hidden = false; document.getElementById('test-bar').hidden = false; testStatus.textContent = s.status || ''; testStatus.style.color = s.isError ? 'var(--ld-error,#dc2626)' : 'var(--ld-success,#16a34a)'; testOutput.textContent = s.output; } } catch (e) {}
  }
  function saveResult(status, output, isError) { try { localStorage.setItem(RESULT_KEY, JSON.stringify({ status: status, output: output, isError: !!isError })); } catch (e) {} }
  function updateDropdown(filter) {
    testDropdown.innerHTML = ''; var q = (filter || '').toLowerCase();
    var matches = testHistory.filter(function (u) { return u.toLowerCase().indexOf(q) >= 0; });
    if (!matches.length) { testDropdown.classList.remove('open'); return; }
    matches.slice(0, 8).forEach(function (url) { var div = document.createElement('div'); div.className = 'wa-url-dropdown-item'; div.innerHTML = '<span>' + esc(url) + '</span>'; div.addEventListener('mousedown', function (e) { e.preventDefault(); testUrlInput.value = url; testDropdown.classList.remove('open'); }); testDropdown.appendChild(div); });
    testDropdown.classList.add('open');
  }
  testUrlInput.addEventListener('input', function () { updateDropdown(this.value); });
  testUrlInput.addEventListener('focus', function () { updateDropdown(this.value); });
  testUrlInput.addEventListener('blur', function () { setTimeout(function () { testDropdown.classList.remove('open'); }, 200); });
  document.querySelector('[data-action="clear-url"]').addEventListener('click', function () { testUrlInput.value = ''; testUrlInput.focus(); });
  document.getElementById('test-form').addEventListener('submit', function (e) {
    e.preventDefault(); var url = testUrlInput.value.trim(); if (!url) return;
    testHistory = testHistory.filter(function (u) { return u !== url; }); testHistory.unshift(url); if (testHistory.length > 50) testHistory.length = 50;
    try { localStorage.setItem(URL_HISTORY_KEY, JSON.stringify(testHistory)); } catch (ex) {}
    var type = this.test_type.value, username = this.test_username.value.trim();
    resultsSection.hidden = false; document.getElementById('test-bar').hidden = false; testStatus.textContent = gettext('Running\u2026'); testStatus.style.color = ''; testOutput.textContent = '';
    apiPost(urls.action, { action: 'test', url: url, test_type: type, test_username: username || '' })
      .then(function (r) { var isErr = !!r.error; var out = typeof r === 'string' ? r : JSON.stringify(r, null, 2); testStatus.textContent = isErr ? gettext('Error') : gettext('Completed'); testStatus.style.color = isErr ? 'var(--ld-error,#dc2626)' : 'var(--ld-success,#16a34a)'; testOutput.textContent = out; saveResult(isErr ? gettext('Error') : gettext('Completed'), out, isErr); })
      .catch(function (err) { var msg = String(err); testStatus.textContent = gettext('Request failed'); testStatus.style.color = 'var(--ld-error,#dc2626)'; testOutput.textContent = msg; saveResult(gettext('Request failed'), msg, true); });
  });
  document.getElementById('btn-show-details').addEventListener('click', function () {
    var raw = document.getElementById('test-raw');
    var showingRaw = !raw.hidden;
    if (showingRaw) { raw.hidden = true; testOutput.hidden = false; this.textContent = gettext('Show raw'); }
    else { raw.hidden = false; testOutput.hidden = true; this.textContent = gettext('Show output'); }
  });
  document.getElementById('btn-clear-test').addEventListener('click', function () {
    testOutput.textContent = ''; document.getElementById('test-raw').textContent = '';
    testStatus.textContent = ''; document.getElementById('test-bar').hidden = true;
    resultsSection.hidden = true; try { localStorage.removeItem(RESULT_KEY); } catch (e) {}
  });
  document.getElementById('btn-clean-test-files').addEventListener('click', function () { apiPost(urls.action, { action: 'clean_test_files' }).then(function (r) { if (r.error) toast(r.error, 'error'); else toast(gettext('Test files cleaned'), 'success'); }); });

  restoreResult();
  switchMode(MODE);
})();
