/**
 * Shared copy-format helpers used by:
 *  - reader.js (ES module import)
 *  - settings-page.js (ES module import)
 *  - highlights/index.html (IIFE bundle via <script>)
 */

export const DEFAULT_ITEM_FORMAT = "> ${highlight}\n\n${annotation}";
export const DEFAULT_SEPARATOR = "\n\n---\n\n";

/**
 * Replace ${highlight} / ${annotation} in format, drop lines whose only
 * variable is empty, then strip leading/trailing blank lines.
 */
export function renderCopyText(format, highlightText, annotationText) {
  const hl = highlightText || "";
  const ann = annotationText || "";
  const lines = format
    .split("\n")
    .map((line) => {
      if (!hl && /^[ >"]*\$\{highlight\}[ >"]*$/.test(line)) return null;
      if (!ann && /^[ >"]*\$\{annotation\}[ >"]*$/.test(line)) return null;
      return line
        .replace(/\$\{highlight\}/g, hl)
        .replace(/\$\{annotation\}/g, ann);
    })
    .filter((l) => l !== null);
  while (lines.length && lines[0] === "") lines.shift();
  while (lines.length && lines[lines.length - 1] === "") lines.pop();
  return lines.join("\n");
}

/**
 * Like renderCopyText but honour the "copy button action" setting:
 *  - "highlight": drop ${annotation}-only lines, replace only ${highlight}
 *  - "note":      drop ${highlight}-only lines, replace only ${annotation}
 *  - "both":      replace both variables
 */
export function renderByAction(format, highlightText, annotationText, action) {
  if (action === "highlight") {
    const kept = format
      .split("\n")
      .filter(
        (l) =>
          l.includes("${highlight}") ||
          (!l.includes("${annotation}") && l.trim() !== ""),
      );
    return renderCopyText(kept.join("\n"), highlightText, "");
  } else if (action === "note") {
    const kept = format
      .split("\n")
      .filter(
        (l) =>
          l.includes("${annotation}") ||
          (!l.includes("${highlight}") && l.trim() !== ""),
      );
    return renderCopyText(kept.join("\n"), "", annotationText);
  }
  return renderCopyText(format, highlightText, annotationText);
}
