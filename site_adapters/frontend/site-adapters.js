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
    // 从模板提供的 domain_files_json 读取
    var data = window.__ld_domain_files || [];
    domainEntries = data.map(function (d) {
      return {
        domain_key: d.domain_key || '',
        adapter: d.adapter || '',
        source: d.adapter === 'defaults' ? 'local' : 'subscription',
        is_alias: d.is_alias || false,
        target: d.target || '',
        requires_cookie: d.requires_cookie || false,
        has_cookie: d.has_cookie || false,
        disabled: d.disabled || false
      };
    });
    renderDomainList();
  }

  var domainSortAsc = true, activeDomainFile = null;
  function renderDomainList(filter) {
    var list = document.getElementById('domain-list'); if (!list) return;
    var h = '<div class="wa-domain-item wa-domain-global" data-domain_key="*" data-is-global="1" data-domain="*" data-is-global="1"><code>*</code><small class="wa-domain-note">' + gettext('global defaults') + '</small></div>';
    var items = domainEntries.slice();
    if (filter) { var q = filter.toLowerCase(); items = items.filter(function (x) { return x.domain_key.toLowerCase().indexOf(q) >= 0; }); }
    if (!domainSortAsc) items.reverse();
    items.forEach(function (d) {
      var cls = 'wa-domain-item' + (d.disabled ? ' wa-domain-sub-disabled' : '') + (activeDomainFile && activeDomainFile.domain_key === d.filename ? ' active' : '');
      h += '<div class="' + cls + '" data-domain_key="' + esc(d.filename) + '" data-domain="' + esc(d.domain_key) + '">';
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
    activeDomainFile = domainEntries.find(function (d) { return d.domain_key === fn; }) || { domain_key: fn, source: 'local' };
    renderDomainList(document.getElementById('domain-search').value);
    document.getElementById('domain-editor-header').hidden = false;
    document.getElementById('editor-empty').hidden = true;
    document.getElementById('editor-content').hidden = false;
    document.getElementById('editor-filename').textContent = fn;
    document.getElementById('btn-delete-domain').hidden = (fn === '*');
    document.getElementById('cookie-banner').hidden = true;
    apiGet(urls.domainRead + '?domain_key=' + encodeURIComponent(fn)).then(function (d) {
      var c = document.getElementById('cm-domain-container'); c.textContent = d.content || '';
      c.style.cssText = 'font-family:monospace;font-size:13px;white-space:pre-wrap;padding:12px;min-height:300px;outline:none;overflow:auto;background:var(--wa-surface-alt);';
      c.contentEditable = 'true'; c.setAttribute('spellcheck', 'false');
    });
  }
  document.getElementById('domain-search').addEventListener('input', function () { renderDomainList(this.value); });
  document.getElementById('btn-sort-domains').addEventListener('click', function () { domainSortAsc = !domainSortAsc; renderDomainList(document.getElementById('domain-search').value); });
  document.getElementById('btn-save-domain').addEventListener('click', function () {
    if (!activeDomainFile) return;
    apiPost(urls.domainSave, { filename: activeDomainFile.domain_key, content: document.getElementById('cm-domain-container').textContent || '' })
      .then(function (r) { if (r.error) toast(r.error, 'error'); else { toast(gettext('Saved'), 'success'); loadDomainList(); } });
  });
  document.getElementById('btn-delete-domain').addEventListener('click', function () {
    if (!activeDomainFile || activeDomainFile.domain_key === 'global.jsonc') return;
    if (!confirm(gettext('Delete') + ' ' + activeDomainFile.domain_key + '?')) return;
    apiPost(urls.domainDelete, { filename: activeDomainFile.domain_key }).then(function (r) {
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
    if (!activeDomainFile || activeDomainFile.domain_key === 'global.jsonc') return;
    var nn = prompt(gettext('New domain name:'), activeDomainFile.domain_key);
    if (!nn || nn === activeDomainFile.domain_key) return;
    apiPost(urls.domainRename, { old_filename: activeDomainFile.domain_key, new_domain: nn }).then(function (r) {
      if (r.error) toast(r.error, 'error'); else { activeDomainFile = null; loadDomainList(); }
    });
  });
  document.getElementById('btn-copy-to-local').addEventListener('click', function () {
    if (!activeDomainFile) return;
    apiPost(urls.domainSave, { filename: activeDomainFile.domain_key, action: 'copy_to_local' })
      .then(function (r) { if (r.error) toast(r.error, 'error'); else toast(gettext('Copied to local'), 'success'); });
  });
  document.getElementById('btn-validate').addEventListener('click', function () {
    if (!activeDomainFile) return;
    apiPost(urls.action, { action: 'validate', filename: activeDomainFile.domain_key, content: document.getElementById('cm-domain-container').textContent || '' })
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
    apiPost(urls.saveCookie, { domain_key: activeDomainFile.domain_key, cookie: c })
      .then(function (r) { if (r.error) toast(r.error, 'error'); else { toast(gettext('Cookie saved'), 'success'); loadDomainList(); } });
  });

  // ===== Subscriptions =====
  function loadSubscriptions() { apiGet(urls.subscriptionManage).then(function (d) { renderSubscriptions(d.adapters || []); }).catch(function (e) { console.error('loadSubscriptions:', e); }); }
  function renderSubscriptions(subs) {
    var list = document.getElementById('subscription-list'); if (!list) return;
    if (!subs.length) { list.innerHTML = '<p class="text-gray" style="padding:16px">' + gettext('No adapters.') + '</p>'; return; }
    var h = '';
    subs.forEach(function (s) {
      var label = (s.id ? s.id + '.' : '') + s.name;
      var source = s.source || '(local)';
      var enabled = s.enabled !== false;
      h += '<div class="wa-cookie-row" style="padding:10px 12px"><div class="wa-cookie-header">';
      h += '<strong>' + esc(label) + '</strong>';
      h += '<code style="font-size:11px;color:var(--wa-muted)">' + esc(source) + '</code>';
      h += '<small style="color:var(--wa-muted)">' + (s.domain_count || 0) + ' domains</small>';
      h += '<div style="margin-left:auto;display:flex;gap:4px">';
      if (!enabled) h += '<span class="wa-badge wa-badge-warn">disabled</span>';
      if (s.source && s.source.startsWith('http')) h += '<button class="btn btn-sm js-sub-fetch" data-index="' + s.index + '">' + gettext('Fetch') + '</button>';
      h += '<button class="btn btn-sm btn-error js-sub-delete" data-index="' + s.index + '">' + gettext('Delete') + '</button>';
      h += '</div></div></div>';
    });
    list.innerHTML = h;
    list.querySelectorAll('.js-sub-fetch').forEach(function (b) { b.addEventListener('click', function () { apiPost(urls.subscriptionManage, { action: 'update', index: this.dataset.index }).then(function (r) { if (r.error) toast(r.error, 'error'); else { toast(gettext('Fetched'), 'success'); loadSubscriptions(); } }); }); });
    list.querySelectorAll('.js-sub-delete').forEach(function (b) { b.addEventListener('click', function () { if (confirm(gettext('Delete adapter') + '?')) apiPost(urls.subscriptionManage, { action: 'delete', index: this.dataset.index }).then(function (r) { if (r.error) toast(r.error, 'error'); else loadSubscriptions(); }); }); });
  }
  document.getElementById('btn-add-subscription').addEventListener('click', function () {
    var source = prompt(gettext('Source (URL or local path):')); if (!source) return;
    var name = prompt(gettext('Name:')); if (!name) return;
    var adapterId = prompt(gettext('ID (optional):'));
    var data = { action: 'add', source: source, name: name };
    if (adapterId) data.id = adapterId;
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
    try { var s = JSON.parse(localStorage.getItem(RESULT_KEY)); if (s && s.data) { resultsSection.hidden = false; document.getElementById('test-bar').hidden = false; renderTestResult(s.data, s.elapsed || 0, s.testType || ''); } } catch (e) {}
  }
  function saveResult(data, elapsed, testType) { try { localStorage.setItem(RESULT_KEY, JSON.stringify({ data: data, elapsed: elapsed, testType: testType })); } catch (e) {} }
  var blurTimer = null;
  function updateDropdown(filter) {
    testDropdown.innerHTML = ''; var q = (filter || '').toLowerCase();
    var matches = testHistory.filter(function (u) { return u.toLowerCase().indexOf(q) >= 0; });
    if (!matches.length) { testDropdown.classList.remove('open'); return; }
    matches.slice(0, 8).forEach(function (url) { var div = document.createElement('div'); div.className = 'wa-url-dropdown-item'; div.innerHTML = '<span>' + esc(url) + '</span><button type="button" class="wa-url-dropdown-del" title="' + gettext('Delete') + '" aria-label="' + gettext('Delete') + '">&times;</button>'; div.querySelector('span').addEventListener('mousedown', function (e) { e.preventDefault(); testUrlInput.value = url; testDropdown.classList.remove('open'); }); div.querySelector('.wa-url-dropdown-del').addEventListener('mousedown', function (e) { e.preventDefault(); e.stopPropagation(); removeHistoryItem(url); updateDropdown(testUrlInput.value); }); testDropdown.appendChild(div); });
    if (testHistory.length > 0) { var clearDiv = document.createElement('div'); clearDiv.className = 'wa-url-dropdown-clear'; clearDiv.textContent = gettext('Clear all history'); clearDiv.addEventListener('mousedown', function (e) { e.preventDefault(); testHistory = []; try { localStorage.removeItem(URL_HISTORY_KEY); } catch (ex) {} testDropdown.innerHTML = ''; testDropdown.classList.remove('open'); }); testDropdown.appendChild(clearDiv); }
    testDropdown.classList.add('open');
  }
  function removeHistoryItem(url) { testHistory = testHistory.filter(function (u) { return u !== url; }); try { localStorage.setItem(URL_HISTORY_KEY, JSON.stringify(testHistory)); } catch (e) {} }
  testUrlInput.addEventListener('input', function () { updateDropdown(this.value); });
  testUrlInput.addEventListener('focus', function () { if (blurTimer) { clearTimeout(blurTimer); blurTimer = null; } updateDropdown(this.value); });
  testUrlInput.addEventListener('blur', function () { blurTimer = setTimeout(function () { testDropdown.classList.remove('open'); }, 200); });
  document.querySelector('[data-action="clear-url"]').addEventListener('click', function () { testUrlInput.value = ''; if (blurTimer) { clearTimeout(blurTimer); blurTimer = null; } testUrlInput.focus(); });
  document.getElementById('test-form').addEventListener('submit', function (e) {
    e.preventDefault(); var url = testUrlInput.value.trim(); if (!url) return;
    testHistory = testHistory.filter(function (u) { return u !== url; }); testHistory.unshift(url); if (testHistory.length > 50) testHistory.length = 50;
    try { localStorage.setItem(URL_HISTORY_KEY, JSON.stringify(testHistory)); } catch (ex) {}
    var type = this.test_type.value, username = this.test_username.value.trim();
    var startTime = Date.now();
    resultsSection.hidden = false; document.getElementById('test-bar').hidden = false; testStatus.innerHTML = '<span class="wa-status-tag wa-status-tag-running">' + esc(type) + '</span> ' + gettext('Running\u2026'); testStatus.style.color = ''; testOutput.innerHTML = '';
    apiPost(urls.action, { action: 'test', url: url, test_type: type, test_username: username || '' })
      .then(function (r) {
        var elapsed = Date.now() - startTime;
        renderTestResult(r, elapsed, type);
        saveResult(r, elapsed, type);
      })
      .catch(function (err) {
        var elapsed = Date.now() - startTime;
        var msg = String(err);
        testStatus.innerHTML = '<span class="wa-status-tag wa-status-tag-error">Error</span> ' + gettext('Request failed');
        testStatus.style.color = 'var(--ld-error,#dc2626)';
        testOutput.innerHTML = '<div class="wa-result-section"><pre class="wa-result-raw">' + esc(msg) + '</pre></div>';
      });
  });
  document.getElementById('btn-show-details').addEventListener('click', function () {
    var raw = document.getElementById('test-raw');
    var showingRaw = !raw.hidden;
    if (showingRaw) { raw.hidden = true; testOutput.hidden = false; this.textContent = gettext('Raw JSON'); }
    else { raw.hidden = false; testOutput.hidden = true; this.textContent = gettext('Show output'); }
  });
  document.getElementById('btn-clear-test').addEventListener('click', function () {
    testOutput.innerHTML = ''; document.getElementById('test-raw').textContent = '';
    testStatus.innerHTML = ''; document.getElementById('test-bar').hidden = true;
    resultsSection.hidden = true; try { localStorage.removeItem(RESULT_KEY); } catch (e) {}
  });
  document.getElementById('btn-clean-test-files').addEventListener('click', function () { apiPost(urls.action, { action: 'clean_test_files' }).then(function (r) { if (r.error) toast(r.error, 'error'); else toast(gettext('Test files cleaned'), 'success'); }); });

  // ===== Test Result Renderers =====
  function formatBytes(bytes) {
    if (bytes == null || bytes === '') return '-';
    var n = Number(bytes);
    if (n < 1024) return n + ' B';
    if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1048576).toFixed(1) + ' MB';
  }

  function formatDuration(ms) {
    if (ms == null) return '-';
    if (ms < 1000) return ms + 'ms';
    if (ms < 10000) return (ms / 1000).toFixed(2) + 's';
    return (ms / 1000).toFixed(1) + 's';
  }

  function urlLink(url) {
    if (!url) return '-';
    return '<a href="' + esc(url) + '" target="_blank" rel="noopener">' + esc(url) + '</a>';
  }

  function renderValue(key, value) {
    if (value == null || value === '') return '<span class="wa-result-empty">-</span>';
    var s = String(value);
    if (key === 'preview_image' || key === 'image') {
      return '<div><a href="' + esc(s) + '" target="_blank" rel="noopener" class="wa-result-link">' + esc(s) + '</a></div>' +
             '<img src="' + esc(s) + '" class="wa-preview-img" loading="lazy" onerror="this.style.display=\'none\'" alt="">';
    }
    if (/^https?:\/\//.test(s)) return urlLink(s);
    if (key === 'size' || key === 'html_size' || key === 'snapshot_size') return formatBytes(value);
    if (key === 'word_count') return Number(value).toLocaleString();
    if (key === 'has_cookie') return value ? gettext('Yes') : gettext('No');
    if (key === 'refreshed') return value ? gettext('Yes') : gettext('No');
    return esc(s);
  }

  function renderSummaryRows(items) {
    var h = '';
    items.forEach(function (item) {
      if (item.value == null || item.value === '') return;
      h += '<div class="wa-result-row">';
      h += '<span class="wa-result-label">' + esc(item.label) + '</span>';
      h += '<span class="wa-result-value">' + (item.link ? urlLink(item.value) : renderValue(item.label, item.value)) + '</span>';
      h += '</div>';
    });
    return h;
  }

  function renderResultRows(fields, handlers) {
    handlers = handlers || {};
    var h = '';
    var keys = Object.keys(fields);
    keys.forEach(function (key) {
      var val = fields[key];
      if (val == null || val === '') return;
      h += '<div class="wa-result-row">';
      h += '<span class="wa-result-label">' + esc(key) + '</span>';
      h += '<span class="wa-result-value">';
      if (handlers[key]) { h += handlers[key](val); }
      else { h += renderValue(key, val); }
      h += '</span>';
      h += '</div>';
    });
    return h;
  }

  function renderCollapsible(title, contentHTML, open) {
    return '<details class="wa-result-collapse"' + (open ? ' open' : '') + '>' +
      '<summary>' + esc(title) + '</summary>' +
      '<div class="wa-result-collapse-body">' + contentHTML + '</div>' +
      '</details>';
  }

  function renderConfigJSON(config) {
    if (!config || !Object.keys(config).length) return '';
    return '<pre class="wa-result-code">' + esc(JSON.stringify(config, null, 2)) + '</pre>';
  }

  function extractViewFilename(viewUrl) {
    if (!viewUrl) return '';
    var m = viewUrl.match(/[?&]file=([^&]+)/);
    return m ? decodeURIComponent(m[1]) : viewUrl.split('/').pop();
  }
  function filterExecutions(executions, steps) {
    if (!executions || !executions.length) return [];
    if (typeof steps === 'string') steps = [steps];
    return executions.filter(function (e) { return steps.indexOf(e.step) >= 0; });
  }

  function renderCommandInfo(executions) {
    if (!executions || !executions.length) return '';
    var items = [];
    executions.forEach(function (e) {
      if (e.cmd && e.cmd.length) {
        items.push({type: 'cmd', cmd: e.cmd});
      }
      if (e.stdin) {
        items.push({type: 'stdin', text: e.stdin});
      }
    });
    if (!items.length) return '';
    var firstLabel = true;
    var h = '';
    items.forEach(function (item, i) {
      var detailId = 'wa-summary-detail-' + i;
      if (item.type === 'cmd') {
        var cmd = item.cmd;
        var fullCmd;
        if (cmd.length > 1) {
          var last = cmd.length - 1;
          fullCmd = cmd.map(function(a, j) {
            var arg;
            if (a.indexOf(' ') < 0) {
              arg = a;
            } else {
              var eq = a.indexOf('=');
              arg = eq >= 0 ? a.substring(0, eq + 1) + '"' + a.substring(eq + 1) + '"' : '"' + a + '"';
            }
            return j < last ? arg + ' \\' : arg;
          }).join('\n  ');
        } else {
          fullCmd = cmd[0];
        }
        h += '<div class="wa-result-row">';
        h += '<span class="wa-result-label">' + (firstLabel ? gettext('command') : '') + '</span>';
        h += '<span class="wa-result-value">';
        h += '<span class="wa-cmd-toggle" onclick="var d=document.getElementById(\'' + detailId + '\');var s=this.querySelector(\'.wa-cmd-arrow\');if(d.hidden){d.hidden=false;s.textContent=\'\\u25BC\';}else{d.hidden=true;s.textContent=\'\\u25B6\';}">';
        h += '<span class="wa-cmd-arrow">\u25B6</span> ' + esc(cmd[0]) + '</span>';
        h += '<div id="' + detailId + '" class="wa-cmd-detail" hidden>';
        h += '<code>' + esc(fullCmd) + '</code>';
        h += '</div>';
        h += '</span>';
        h += '</div>';
      } else {
        var stdinText = item.text;
        try { stdinText = JSON.stringify(JSON.parse(stdinText), null, 2); } catch (e) {}
        h += '<div class="wa-result-row">';
        h += '<span class="wa-result-label">' + gettext('stdin') + '</span>';
        h += '<span class="wa-result-value">';
        h += '<span class="wa-cmd-toggle" onclick="var d=document.getElementById(\'' + detailId + '\');var s=this.querySelector(\'.wa-cmd-arrow\');if(d.hidden){d.hidden=false;s.textContent=\'\\u25BC\';}else{d.hidden=true;s.textContent=\'\\u25B6\';}">';
        h += '<span class="wa-cmd-arrow">\u25B6</span> ' + esc(stdinText.substring(0, 80)) + (stdinText.length > 80 ? '...' : '') + '</span>';
        h += '<div id="' + detailId + '" class="wa-cmd-detail" hidden>';
        h += '<pre class="wa-stdin-pre">' + esc(stdinText) + '</pre>';
        h += '</div>';
        h += '</span>';
        h += '</div>';
      }
      firstLabel = false;
    });
    return h;
  }

  function renderExecutionLog(executions) {
    if (!executions || !executions.length) return '';
    var h = '<table class="wa-execution-table"><thead><tr>' +
      '<th>' + gettext('Step') + '</th>' +
      '<th>' + gettext('Domain') + '</th>' +
      '<th>' + gettext('Return') + '</th>' +
      '<th>' + gettext('Duration') + '</th>' +
      '</tr></thead><tbody>';
    executions.forEach(function (e) {
      h += '<tr>';
      h += '<td>' + esc(e.step || '-') + '</td>';
      h += '<td><code>' + esc(e.domain_key || '-') + '</code></td>';
      h += '<td>' + (e.returncode === 0 ? '<span class="wa-badge wa-badge-ok">0</span>' : '<span class="wa-badge wa-badge-warn">' + esc(String(e.returncode)) + '</span>') + '</td>';
      h += '<td>' + formatDuration(e.duration_ms) + '</td>';
      h += '</tr>';
    });
    h += '</tbody></table>';
    return h;
  }

  function renderStatusBar(type, isError, elapsed, extra) {
    var tagClass = isError ? 'wa-status-tag-error' : 'wa-status-tag-ok';
    var tagText = isError ? gettext('Error') : gettext('Completed');
    var h = '<span class="wa-status-tag ' + tagClass + '">' + esc(type) + '</span> ';
    h += tagText;
    if (extra) h += ' <span class="wa-status-extra">' + esc(extra) + '</span>';
    if (elapsed) h += ' <span class="wa-status-time">' + formatDuration(elapsed) + '</span>';
    return h;
  }

  function renderTestResult(r, elapsed, testType) {
    var isError = !!r.error;
    testStatus.innerHTML = renderStatusBar(testType || r.type, isError, elapsed);
    testStatus.style.color = '';

    document.getElementById('test-raw').textContent = JSON.stringify(r, null, 2);

    if (isError) {
      testOutput.innerHTML = '<div class="wa-result-section"><div class="wa-result-error">' + esc(r.error) + '</div></div>';
      return;
    }

    var handlers = {
      'config': renderConfigResult,
      'metadata': renderMetadataResult,
      'snapshot': renderSnapshotResult,
      'reader': renderReaderResult,
      'cookie': renderCookieResult,
      'pipeline': renderPipelineResult
    };
    var fn = handlers[r.type];
    if (fn) {
      testOutput.innerHTML = fn(r);
    } else {
      testOutput.innerHTML = '<div class="wa-result-section"><pre class="wa-result-raw">' + esc(JSON.stringify(r, null, 2)) + '</pre></div>';
    }
  }

  function renderConfigResult(r) {
    var result = r.result || {};
    var h = '<div class="wa-result-section">';
    h += '<div class="wa-result-block">';
    h += '<h3 class="wa-result-heading">' + gettext('Summary') + '</h3>';
    h += renderSummaryRows([
      {label: 'url', value: result.url, link: true},
      {label: 'domain', value: result.domain},
      {label: 'domain_key', value: result.domain_key}
    ]);
    h += renderCommandInfo(r.executions);
    h += '</div>';
    if (result.merged && Object.keys(result.merged).length) {
      h += '<div class="wa-result-block">';
      h += '<h3 class="wa-result-heading">' + gettext('Merged Config') + '</h3>';
      h += renderConfigJSON(result.merged);
      h += '</div>';
    }
    if (r.executions && r.executions.length) {
      h += renderCollapsible(gettext('Execution Log'), renderExecutionLog(r.executions), false);
    }
    h += '</div>';
    return h;
  }

  function renderMetadataResult(r) {
    var h = '<div class="wa-result-section">';
    h += '<div class="wa-result-block">';
    h += '<h3 class="wa-result-heading">' + gettext('Summary') + '</h3>';
    h += renderSummaryRows([
      {label: 'original_url', value: r.original_url, link: true},
      {label: 'request_url', value: r.request_url, link: true}
    ]);
    h += renderCommandInfo(filterExecutions(r.executions, ['metadata', 'metadata_script']));
    h += '</div>';
    if (r.result) {
      var fields = r.result;
      if (fields && Object.keys(fields).length) {
        h += '<div class="wa-result-block">';
        h += '<h3 class="wa-result-heading">' + gettext('Result') + '</h3>';
        var orderedKeys = ['title', 'description', 'preview_image'];
        var restKeys = Object.keys(fields).filter(function (k) { return orderedKeys.indexOf(k) < 0 && k !== 'url'; });
        var allKeys = orderedKeys.concat(restKeys);
        var orderedFields = {};
        allKeys.forEach(function (k) { if (k in fields) orderedFields[k] = fields[k]; });
        h += renderResultRows(orderedFields);
        h += '</div>';
      }
    }
    if (r.config && Object.keys(r.config).length) {
      h += renderCollapsible(gettext('Config'), renderConfigJSON(r.config), false);
    }
    if (r.executions && r.executions.length) {
      h += renderCollapsible(gettext('Execution Log'), renderExecutionLog(r.executions), false);
    }
    h += '</div>';
    return h;
  }

  function renderSnapshotResult(r) {
    var h = '<div class="wa-result-section">';
    h += '<div class="wa-result-block">';
    h += '<h3 class="wa-result-heading">' + gettext('Summary') + '</h3>';
    h += renderSummaryRows([
      {label: 'original_url', value: r.original_url, link: true},
      {label: 'request_url', value: r.request_url, link: true}
    ]);
    h += renderCommandInfo(filterExecutions(r.executions, ['snapshot', 'snapshot_script']));
    h += '</div>';
    if (r.result) {
      var fields = r.result;
      h += '<div class="wa-result-block">';
      h += '<h3 class="wa-result-heading">' + gettext('Result') + '</h3>';
      var snapFields = {};
      Object.keys(fields).forEach(function (k) {
        if (k !== 'view_url' && k !== 'size') snapFields[k] = fields[k];
      });
      h += renderResultRows(snapFields, {
        'file': function (val) { return '<a href="' + esc(fields.view_url) + '" target="_blank">' + esc(val) + '</a> (' + formatBytes(fields.size) + ')'; }
      });
      h += '</div>';
    }
    if (r.config && Object.keys(r.config).length) {
      h += renderCollapsible(gettext('Config'), renderConfigJSON(r.config), false);
    }
    if (r.executions && r.executions.length) {
      h += renderCollapsible(gettext('Execution Log'), renderExecutionLog(r.executions), false);
    }
    h += '</div>';
    return h;
  }

  function renderReaderResult(r) {
    var h = '<div class="wa-result-section">';
    h += '<div class="wa-result-block">';
    h += '<h3 class="wa-result-heading">' + gettext('Summary') + '</h3>';
    h += renderSummaryRows([
      {label: 'original_url', value: r.original_url, link: true},
      {label: 'request_url', value: r.request_url, link: true}
    ]);
    h += renderCommandInfo(filterExecutions(r.executions, ['reader']));
    h += '</div>';
    if (r.result) {
      var fields = r.result;
      h += '<div class="wa-result-block">';
      h += '<h3 class="wa-result-heading">' + gettext('Result') + '</h3>';
      h += renderResultRows({
        'title': fields.title,
        'word_count': fields.word_count,
        'reader_file': fields.view_url ? extractViewFilename(fields.view_url) : null,
        'snapshot_file': fields.snapshot_view_url ? extractViewFilename(fields.snapshot_view_url) : null
      }, {
        'title': function (val) { return esc(val); },
        'word_count': function (val) { return Number(val).toLocaleString(); },
        'reader_file': function (val) { return '<a href="' + esc(fields.view_url) + '" target="_blank">' + esc(val) + '</a> (' + formatBytes(fields.html_size) + ')'; },
        'snapshot_file': function (val) { return '<a href="' + esc(fields.snapshot_view_url) + '" target="_blank">' + esc(val) + '</a> (' + formatBytes(fields.snapshot_size) + ')'; }
      });
      h += '</div>';
    }
    if (r.config && Object.keys(r.config).length) {
      h += renderCollapsible(gettext('Config'), renderConfigJSON(r.config), false);
    }
    if (r.executions && r.executions.length) {
      h += renderCollapsible(gettext('Execution Log'), renderExecutionLog(r.executions), false);
    }
    h += '</div>';
    return h;
  }

  function renderCookieResult(r) {
    var h = '<div class="wa-result-section">';
    h += '<div class="wa-result-block">';
    h += '<h3 class="wa-result-heading">' + gettext('Summary') + '</h3>';
    h += renderSummaryRows([
      {label: 'domain_key', value: r.domain_key}
    ]);
    h += renderCommandInfo(filterExecutions(r.executions, ['cookie_refresh', 'cookie_verify']));
    h += '</div>';
    h += '<div class="wa-result-block">';
    h += '<h3 class="wa-result-heading">' + gettext('Result') + '</h3>';
    h += renderResultRows({
      'has_cookie': r.has_cookie,
      'cookie_preview': r.cookie_preview,
      'refreshed': r.refreshed
    });
    h += '</div>';
    if (r.executions && r.executions.length) {
      h += renderCollapsible(gettext('Execution Log'), renderExecutionLog(r.executions), false);
    }
    h += '</div>';
    return h;
  }

  function renderPipelineResult(r) {
    var h = '<div class="wa-result-section">';
    if (r.config) {
      var cfg = r.config;
      h += '<div class="wa-result-block wa-pipeline-step">';
      h += '<h3 class="wa-result-heading"><span class="wa-pipeline-step-num">1</span> ' + gettext('Config') + '</h3>';
      h += renderSummaryRows([
        {label: 'url', value: cfg.url, link: true},
        {label: 'domain', value: cfg.domain},
        {label: 'domain_key', value: cfg.domain_key}
      ]);
      h += '</div>';
    }
    if (r.metadata) {
      var m = r.metadata;
      h += '<div class="wa-result-block wa-pipeline-step">';
      h += '<h3 class="wa-result-heading"><span class="wa-pipeline-step-num">2</span> ' + gettext('Metadata') + '</h3>';
      h += renderSummaryRows([
        {label: 'original_url', value: m.original_url, link: true},
        {label: 'request_url', value: m.request_url, link: true}
      ]);
      if (m.result) {
        var orderedKeys = ['title', 'description', 'preview_image'];
        var restKeys = Object.keys(m.result).filter(function (k) { return orderedKeys.indexOf(k) < 0 && k !== 'url'; });
        var allKeys = orderedKeys.concat(restKeys);
        var orderedFields = {};
        allKeys.forEach(function (k) { if (k in m.result) orderedFields[k] = m.result[k]; });
        h += renderResultRows(orderedFields);
      }
      h += renderCommandInfo(filterExecutions(r.executions, ['metadata', 'metadata_script']));
      h += '</div>';
    }
    if (r.snapshot) {
      var s = r.snapshot;
      h += '<div class="wa-result-block wa-pipeline-step">';
      h += '<h3 class="wa-result-heading"><span class="wa-pipeline-step-num">3</span> ' + gettext('Snapshot') + '</h3>';
      h += renderSummaryRows([
        {label: 'original_url', value: s.original_url, link: true},
        {label: 'request_url', value: s.request_url, link: true}
      ]);
      if (s.result) {
        var pipeSnapFields = {};
        Object.keys(s.result).forEach(function (k) {
          if (k !== 'view_url' && k !== 'size') pipeSnapFields[k] = s.result[k];
        });
        h += renderResultRows(pipeSnapFields, {
          'file': function (val) { return '<a href="' + esc(s.result.view_url) + '" target="_blank">' + esc(val) + '</a> (' + formatBytes(s.result.size) + ')'; }
        });
      }
      h += renderCommandInfo(filterExecutions(r.executions, ['snapshot', 'snapshot_script']));
      h += '</div>';
    }
    if (r.reader) {
      var rd = r.reader;
      h += '<div class="wa-result-block wa-pipeline-step">';
      h += '<h3 class="wa-result-heading"><span class="wa-pipeline-step-num">4</span> ' + gettext('Reader') + '</h3>';
      h += renderSummaryRows([
        {label: 'original_url', value: rd.original_url, link: true},
        {label: 'request_url', value: rd.request_url, link: true}
      ]);
      if (rd.result) {
        h += renderResultRows({
          'title': rd.result.title,
          'word_count': rd.result.word_count,
          'reader_file': rd.result.view_url ? extractViewFilename(rd.result.view_url) : null,
          'snapshot_file': rd.result.snapshot_view_url ? extractViewFilename(rd.result.snapshot_view_url) : null
        }, {
          'title': function (val) { return esc(val); },
          'word_count': function (val) { return Number(val).toLocaleString(); },
          'reader_file': function (val) { return '<a href="' + esc(rd.result.view_url) + '" target="_blank">' + esc(val) + '</a> (' + formatBytes(rd.result.html_size) + ')'; },
          'snapshot_file': function (val) { return '<a href="' + esc(rd.result.snapshot_view_url) + '" target="_blank">' + esc(val) + '</a> (' + formatBytes(rd.result.snapshot_size) + ')'; }
        });
      }
      h += '</div>';
    }
    if (r.config && r.config.merged && Object.keys(r.config.merged).length) {
      h += renderCollapsible(gettext('Merged Config'), renderConfigJSON(r.config.merged), false);
    }
    if (r.executions && r.executions.length) {
      h += renderCollapsible(gettext('Execution Log'), renderExecutionLog(r.executions), false);
    }
    h += '</div>';
    return h;
  }

  restoreResult();
  switchMode(MODE);
})();
