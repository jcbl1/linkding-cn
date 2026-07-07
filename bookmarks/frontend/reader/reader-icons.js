// Shared SVG icons for reader toolbar and sidebar components.
// All icons now reference the SVG sprite defined in _svg_sprite.html for consistency.
export const READER_ICONS = {
  // Toolbar icons
  "open-original": `<svg><use href="#ld-icon-globe"></use></svg>`,
  "open-snapshot": `<svg><use href="#ld-icon-camera"></use></svg>`,
  "font-size": `<svg><use href="#ld-icon-adjustments-horizontal"></use></svg>`,
  "toggle-sidebar": `<svg><use href="#ld-icon-toggle-sidebar"></use></svg>`,
  "chevron-down": `<svg width="10" height="10"><use href="#ld-icon-chevron-down"></use></svg>`,
  reset: `<svg width="14" height="14"><use href="#ld-icon-refresh"></use></svg>`,

  // Sidebar tab icons
  "tab-annotations": `<svg width="18" height="18"><use href="#ld-icon-highlight"></use></svg>`,
  "tab-details": `<svg width="18" height="18"><use href="#ld-icon-file"></use></svg>`,
  copy: `<svg width="16" height="16"><use href="#ld-icon-copy"></use></svg>`,
  "external-link": `<svg width="12" height="12"><use href="#ld-icon-external-link"></use></svg>`,
  empty: `<svg width="32" height="32"><use href="#ld-icon-file"></use></svg>`,

  // Action icons — use sprite references
  delete: `<svg width="16" height="16"><use href="#ld-icon-remove"></use></svg>`,
  archive: `<svg width="16" height="16"><use href="#ld-icon-archive"></use></svg>`,
  "archive-slash": `<svg width="16" height="16"><use href="#ld-icon-archive-slash"></use></svg>`,

  // Status icons — use sprite references
  share: `<svg width="16" height="16"><use href="#ld-icon-share"></use></svg>`,
  "share-x": `<svg width="16" height="16"><use href="#ld-icon-share-x"></use></svg>`,
  unread: `<svg width="16" height="16"><use href="#ld-icon-unread"></use></svg>`,
  "unread-x": `<svg width="16" height="16"><use href="#ld-icon-unread-x"></use></svg>`,
  "read-check": `<svg width="16" height="16"><use href="#ld-icon-read-check"></use></svg>`,

  // Other actions
  restore: `<svg width="16" height="16"><use href="#ld-icon-restore"></use></svg>`,
  rename: `<svg width="16" height="16"><use href="#ld-icon-edit"></use></svg>`,
  "clock-deleted": `<svg width="16" height="16"><use href="#ld-icon-delete"></use></svg>`,

  // Date icons
  "clock-added": `<svg width="16" height="16"><use href="#ld-icon-clock"></use></svg>`,
  "clock-modified": `<svg width="16" height="16"><use href="#ld-icon-clock-edit"></use></svg>`,

  // Feature buttons
  eye: `<svg width="14" height="14"><use href="#ld-icon-eye"></use></svg>`,
  "eye-off": `<svg width="14" height="14"><use href="#ld-icon-eye-off"></use></svg>`,

  "add-bookmark": `<svg width="18" height="18"><use href="#ld-icon-circle-plus"></use></svg>`,
};
