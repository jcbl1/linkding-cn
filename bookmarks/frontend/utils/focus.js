let keyboardActive = false;

window.addEventListener(
  "keydown",
  () => {
    keyboardActive = true;
  },
  { capture: true },
);

window.addEventListener(
  "mousedown",
  () => {
    keyboardActive = false;
  },
  { capture: true },
);

export function isKeyboardActive() {
  return keyboardActive;
}

export class FocusTrapController {
  constructor(element) {
    this.element = element;
    this.focusableElements = this.element.querySelectorAll(
      'a[href]:not([disabled]), button:not([disabled]), textarea:not([disabled]), input[type="text"]:not([disabled]), input[type="radio"]:not([disabled]), input[type="checkbox"]:not([disabled]), select:not([disabled])',
    );
    this.firstFocusableElement = this.focusableElements[0];
    this.lastFocusableElement =
      this.focusableElements[this.focusableElements.length - 1];

    this.onKeyDown = this.onKeyDown.bind(this);

    // Only auto-focus for keyboard navigation to avoid triggering the mobile virtual keyboard
    if (keyboardActive && this.firstFocusableElement) {
      const focusFirst = () => {
        const element = this.firstFocusableElement;
        if (element && element.isConnected && element.getClientRects().length) {
          element.focus({ focusVisible: true });
        }
      };
      // Modals rendered in a Turbo frame become visible only after the frame
      // has rendered, so focus on frame load as well.
      requestAnimationFrame(focusFirst);
      this.element
        .closest("turbo-frame")
        ?.addEventListener("turbo:frame-load", focusFirst, { once: true });
    }
    this.element.addEventListener("keydown", this.onKeyDown);
  }

  destroy() {
    this.element.removeEventListener("keydown", this.onKeyDown);
  }

  onKeyDown(event) {
    if (event.key !== "Tab") {
      return;
    }
    if (event.shiftKey) {
      if (document.activeElement === this.firstFocusableElement) {
        event.preventDefault();
        this.lastFocusableElement?.focus();
      }
    } else if (document.activeElement === this.lastFocusableElement) {
      event.preventDefault();
      this.firstFocusableElement?.focus();
    }
  }
}

let afterPageLoadFocusTarget = [];
let firstPageLoad = true;
let focusAppliedOnFrameLoad = false;

export function setAfterPageLoadFocusTarget(...targets) {
  afterPageLoadFocusTarget = targets;
}

function programmaticFocus(element) {
  const isFocusable = element.tabIndex >= 0;
  if (!isFocusable) {
    element.tabIndex = -1;
    element.style.outline = "none";
  }
  element.focus({
    focusVisible: isKeyboardActive() && isFocusable,
    preventScroll: true,
  });
}

function applyFocusTargets() {
  for (const target of afterPageLoadFocusTarget) {
    const element = document.querySelector(target);
    if (element) {
      programmaticFocus(element);
      afterPageLoadFocusTarget = [];
      return true;
    }
  }
  afterPageLoadFocusTarget = [];
  return false;
}

document.addEventListener("turbo:load", () => {
  if (firstPageLoad) {
    firstPageLoad = false;
    return;
  }

  if (focusAppliedOnFrameLoad) {
    focusAppliedOnFrameLoad = false;
    return;
  }

  if (applyFocusTargets()) {
    return;
  }

  const autofocus = document.querySelector("[autofocus]");
  if (autofocus) {
    return;
  }

  const toast = document.querySelector(".toast");
  if (toast) {
    programmaticFocus(toast);
    return;
  }

  const main = document.querySelector("main");
  if (main) {
    programmaticFocus(main);
  }
});

// Details modals are rendered in a Turbo frame. Closing one performs a frame
// visit, which fires turbo:frame-load instead of turbo:load.
document.addEventListener("turbo:frame-load", () => {
  if (applyFocusTargets()) {
    focusAppliedOnFrameLoad = true;
  }
});
