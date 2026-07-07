import { Behavior, registerBehavior } from "./runtime.js";
import { gettext, interpolate } from "../utils/i18n.js";

const STORAGE_KEY = "linkding:bulk-edit";

class BulkEdit extends Behavior {
  constructor(element) {
    super(element);

    this.active = element.classList.contains("active");

    this.init = this.init.bind(this);
    this.onToggleActive = this.onToggleActive.bind(this);
    this.onToggleAll = this.onToggleAll.bind(this);
    this.onToggleBookmark = this.onToggleBookmark.bind(this);
    this.onActionSelected = this.onActionSelected.bind(this);
    this.onSubmit = this.onSubmit.bind(this);

    this.isStickyOn = element.querySelector(".section-header")?.dataset.stickyOn === 'true'
    this.bulkEditBar = element.querySelector('.bulk-edit-bar');

    // 初始状态：页面加载时如已激活，同步粘性类
    if (this.isStickyOn) {
      this.bulkEditBar.classList.add("sticky");
    }

    this.init();
    // Reset when bookmarks are updated
    document.addEventListener("bookmark-list-updated", this.init);
  }

  destroy() {
    this.removeListeners();
    document.removeEventListener("bookmark-list-updated", this.init);
  }

  init() {
    // Update elements
    this.activeToggle = this.element.querySelector(".bulk-edit-active-toggle");
    this.actionSelect = this.element.querySelector(
      "select[name='bulk_action']",
    );
    this.tagAutoComplete = this.element.querySelector(".tag-autocomplete");
    this.executeButton = this.element.querySelector("button[name='bulk_execute']");
    this.cancelButton = this.element.querySelector("button[name='bulk_cancel']");
    this.selectAcross = this.element.querySelector("label.select-across");
    this.selectAcrossInput = this.selectAcross.querySelector("input");
    this.allCheckbox = this.element.querySelector(
      ".bulk-edit-checkbox.all input",
    );
    this.bookmarkCheckboxes = Array.from(
      this.element.querySelectorAll(".bulk-edit-checkbox:not(.all) input"),
    );

    this.form = this.element.querySelector("form.bookmark-actions");
    this.countElement = this.element.querySelector(".bulk-edit-count");

    // Add listeners, ensure there are no dupes by possibly removing existing listeners
    this.removeListeners();
    this.addListeners();

    // Update total number of bookmarks
    const totalHolder = this.element.querySelector("[data-bookmarks-total]");
    const total = totalHolder?.dataset.bookmarksTotal || 0;
    const totalSpan = this.selectAcross.querySelector("span.total");
    totalSpan.textContent = total;

    // Restore saved state from sessionStorage
    this.restoreState();
  }

  addListeners() {
    this.activeToggle.addEventListener("click", this.onToggleActive);
    this.cancelButton.addEventListener("click", this.onToggleActive);
    this.actionSelect.addEventListener("change", this.onActionSelected);
    this.allCheckbox.addEventListener("change", this.onToggleAll);
    this.bookmarkCheckboxes.forEach((checkbox) => {
      checkbox.addEventListener("change", this.onToggleBookmark);
    });
    if (this.form) {
      this.form.addEventListener("submit", this.onSubmit);
    }
  }

  removeListeners() {
    this.activeToggle.removeEventListener("click", this.onToggleActive);
    this.cancelButton.removeEventListener("click", this.onToggleActive);
    this.actionSelect.removeEventListener("change", this.onActionSelected);
    this.allCheckbox.removeEventListener("change", this.onToggleAll);
    this.bookmarkCheckboxes.forEach((checkbox) => {
      checkbox.removeEventListener("change", this.onToggleBookmark);
    });
    if (this.form) {
      this.form.removeEventListener("submit", this.onSubmit);
    }
  }

  onToggleActive() {
    this.active = !this.active;
    if (this.active) {
      this.element.classList.add("active");
      if(this.isStickyOn) {
        this.bulkEditBar.classList.add("sticky");
      }
    } else {
      this.element.classList.remove("active");
      if(this.isStickyOn) {
        this.bulkEditBar.classList.remove("sticky");
      }
      this.clearState();
    }
  }

  onSubmit() {
    this.clearState();
  }

  onToggleBookmark(event) {
    const checkbox = event.target;
    const state = this._loadState();

    if (state && state.selectAll) {
      // Transitioning out of selectAll: start tracking individual IDs
      // All checkboxes on the page are checked (from selectAll restore).
      // Collect the ones still checked (includes all except the one just unchecked).
      const selectedIds = this.bookmarkCheckboxes
        .filter((cb) => cb.checked)
        .map((cb) => cb.value);
      this._saveState({ selectAll: false, selectedIds });
    } else if (checkbox.checked) {
      this._addId(checkbox.value);
    } else {
      this._removeId(checkbox.value);
    }

    // Sync allCheckbox
    const allChecked = this.bookmarkCheckboxes.every((cb) => cb.checked);
    this.allCheckbox.checked = allChecked;
    this._updateSelectAcross(allChecked);
    this._updateExecuteState();
  }

  onToggleAll() {
    const allChecked = this.allCheckbox.checked;
    this.bookmarkCheckboxes.forEach((checkbox) => {
      checkbox.checked = allChecked;
    });

    if (allChecked) {
      this._saveState({ selectAll: true, selectedIds: [] });
    } else {
      this._saveState({ selectAll: false, selectedIds: [] });
    }

    this._updateSelectAcross(allChecked);
    this._updateExecuteState();
  }

  onActionSelected() {
    const action = this.actionSelect.value;

    if (action === "bulk_tag" || action === "bulk_untag") {
      this.tagAutoComplete.classList.remove("d-none");
    } else {
      this.tagAutoComplete.classList.add("d-none");
    }
  }

  _updateSelectAcross(allChecked) {
    if (allChecked) {
      this.selectAcross.classList.remove("d-none");
    } else {
      this.selectAcross.classList.add("d-none");
      this.selectAcrossInput.checked = false;
    }
  }

  reset() {
    this.allCheckbox.checked = false;
    this.bookmarkCheckboxes.forEach((checkbox) => {
      checkbox.checked = false;
    });
    this._updateSelectAcross(false);
    this._updateExecuteState();
  }

  _updateExecuteState() {
    if (!this.executeButton) {
      return;
    }
    const state = this._loadState();
    const hasSelection =
      (state && state.selectAll) ||
      (state && state.selectedIds && state.selectedIds.length > 0) ||
      this.bookmarkCheckboxes.some((checkbox) => checkbox.checked);
    this.executeButton.disabled = !hasSelection;
    this._updateCount();
  }

  _updateCount() {
    if (!this.countElement) return;
    const state = this._loadState();
    let count = 0;
    if (state && state.selectAll) {
      count = parseInt(this.selectAcross.querySelector("span.total")?.textContent || "0", 10);
    } else if (state && state.selectedIds) {
      count = state.selectedIds.length;
    }
    if (count > 0) {
      this.countElement.textContent = interpolate(gettext("Selected(%(count)s)"), { count });
      this.countElement.classList.remove("d-none");
    } else {
      this.countElement.classList.add("d-none");
    }
  }

  // --- State persistence ---

  _loadState() {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      if (raw) return JSON.parse(raw);
    } catch (e) {
      // ignore
    }
    return null;
  }

  _saveState(state) {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (e) {
      // ignore
    }
  }

  _addId(id) {
    const state = this._loadState() || { selectAll: false, selectedIds: [] };
    if (!state.selectedIds.includes(id)) {
      state.selectedIds.push(id);
    }
    this._saveState(state);
  }

  _removeId(id) {
    const state = this._loadState();
    if (!state || !state.selectedIds) return;
    state.selectedIds = state.selectedIds.filter((x) => x !== id);
    state.selectAll = false;
    this._saveState(state);
  }

  clearState() {
    sessionStorage.removeItem(STORAGE_KEY);
  }

  restoreState() {
    const state = this._loadState();

    if (!state || (!state.selectAll && (!state.selectedIds || state.selectedIds.length === 0))) {
      // No saved state, reset checkboxes
      this.reset();
      return;
    }

    // Restore active mode
    this.active = true;
    this.element.classList.add("active");
    if (this.isStickyOn) {
      this.bulkEditBar.classList.add("sticky");
    }

    if (state.selectAll) {
      // Select all across pages: check all on current page and show indicator
      this.bookmarkCheckboxes.forEach((checkbox) => {
        checkbox.checked = true;
      });
      this.allCheckbox.checked = true;
      this.selectAcross.classList.remove("d-none");
      this.selectAcrossInput.checked = true;
    } else {
      // Restore individual selections
      const selectedIds = new Set(state.selectedIds.map(String));
      this.bookmarkCheckboxes.forEach((checkbox) => {
        checkbox.checked = selectedIds.has(checkbox.value);
      });
      const allChecked =
        this.bookmarkCheckboxes.length > 0 &&
        this.bookmarkCheckboxes.every((checkbox) => checkbox.checked);
      this.allCheckbox.checked = allChecked;
      this._updateSelectAcross(allChecked);
    }

    this._updateExecuteState();
  }
}

registerBehavior("ld-bulk-edit", BulkEdit);
