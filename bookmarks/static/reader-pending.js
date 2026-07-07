(function() {
  var cfg = JSON.parse(document.getElementById("pending-config").textContent);
  var bookmarkId = cfg.bookmarkId;
  var assetId = cfg.assetId;
  var apiBaseRaw = JSON.parse(document.getElementById("reader-api-base-url").textContent);

  function normalizeBaseUrl(baseUrl) {
    var value = String(baseUrl || "").trim();
    if (!value) return "/api/";
    return value.replace(/\/+$/, "") + "/";
  }
  function joinPath(baseUrl, path) {
    return normalizeBaseUrl(baseUrl) + String(path || "").replace(/^\/+/, "");
  }
  var apiBase = normalizeBaseUrl(apiBaseRaw);
  var maxAttempts = 60; // 60 * 2s = 2 min max
  var attempt = 0;

  function poll() {
    attempt++;
    if (attempt > maxAttempts) {
      showError(cfg.msg.timeout);
      return;
    }

    fetch(joinPath(apiBase, "bookmarks/" + bookmarkId + "/assets/" + assetId + "/"), {
      headers: { "X-CSRFToken": document.querySelector("meta[name=csrfmiddlewaretoken]").content }
    })
      .then(function(r) {
        if (r.status === 404) {
          showError(cfg.msg.processFailed);
          return null;
        }
        return r.json();
      })
      .then(function(data) {
        if (!data) return;
        if (data.status === "complete") {
          window.location.reload();
        } else if (data.status === "failure") {
          showError(cfg.msg.retry);
        } else {
          setTimeout(poll, 2000);
        }
      })
      .catch(function() {
        setTimeout(poll, 2000);
      });
  }

  function showError(msg) {
    document.querySelector(".loading-spinner").style.display = "none";
    document.querySelector(".loading-text").style.display = "none";
    var el = document.getElementById("error-msg");
    el.textContent = msg;
    el.style.display = "block";
  }

  setTimeout(poll, 1000);
})();
