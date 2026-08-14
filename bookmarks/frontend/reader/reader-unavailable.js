(function() {
  var link = document.getElementById('add-bookmark-link');
  var text = document.getElementById('add-bookmark-text');
  if (!link) return;

  var cfg = JSON.parse(document.getElementById("unavailable-config").textContent);
  var apiBase = JSON.parse(document.getElementById("reader-api-base-url").textContent);
  var bookmarkUrl = link.dataset.url;
  var csrfToken = document.querySelector('meta[name=csrfmiddlewaretoken]').content;

  function normalizeBaseUrl(baseUrl) {
    var value = String(baseUrl || "").trim();
    if (!value) return "/api";
    return value.replace(/\/+$/, "");
  }

  var apiUrl = normalizeBaseUrl(apiBase) + "/bookmarks/";

  link.addEventListener('click', function(e) {
    e.preventDefault();
    if (link.dataset.loading) return;
    link.dataset.loading = '1';
    text.textContent = cfg.msg.adding;

    fetch(apiUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken
      },
      body: JSON.stringify({ url: bookmarkUrl })
    })
    .then(function(r) {
      if (!r.ok) throw new Error('API error');
      return r.json();
    })
    .then(function(data) {
      window.location.href = '/bookmarks/' + data.id + '/read';
    })
    .catch(function() {
      text.textContent = cfg.msg.failed;
      delete link.dataset.loading;
    });
  });
})();
