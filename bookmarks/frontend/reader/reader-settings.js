const SETTINGS_KEY = "ld:reader:settings";

export function loadReaderSettings() {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch { return {}; }
}

export function saveReaderSettings(partial) {
  const current = loadReaderSettings();
  localStorage.setItem(SETTINGS_KEY, JSON.stringify({ ...current, ...partial }));
}

export function setReaderTheme(theme) {
  saveReaderSettings({ theme });
  if (theme === "auto") return; // Reload handled by UI hint
  // Forced theme: remove all, add target
  const existing = document.querySelector('link[href*="theme-"]');
  const base = existing ? existing.href.replace(/theme-[^/]*\.css.*/, "") : "/static/";
  document.querySelectorAll('link[href*="theme-"]').forEach(el => el.remove());
  const l = document.createElement("link");
  l.rel = "stylesheet";
  l.href = base + `theme-${theme}.css`;
  document.head.appendChild(l);
}
