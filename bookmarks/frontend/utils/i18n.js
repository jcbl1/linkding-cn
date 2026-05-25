export function gettext(message) {
  return typeof window.gettext === "function" ? window.gettext(message) : message;
}

export function ngettext(singular, plural, count) {
  if (typeof window.ngettext === "function") {
    return window.ngettext(singular, plural, count);
  }
  return count === 1 ? singular : plural;
}

export function interpolate(message, mapping = {}) {
  if (typeof window.interpolate === "function") {
    return window.interpolate(message, mapping, true);
  }
  return message.replace(/%\(([^)]+)\)s/g, (_, key) =>
    Object.prototype.hasOwnProperty.call(mapping, key) ? String(mapping[key]) : ""
  );
}
