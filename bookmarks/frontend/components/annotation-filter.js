import { Behavior, registerBehavior } from "./runtime.js";

class AnnotationFilterBehavior extends Behavior {
  constructor(element) {
    super(element);
    this.updateVisibility = this.updateVisibility.bind(this);
    this.init();
  }

  init() {
    // Find highlight radio buttons in the same form
    const form = this.element.closest("form") || this.element.parentElement;
    this.highlightRadios = form.querySelectorAll('input[name="highlight"]');
    this.annotationRadios = this.element.querySelectorAll('input[name="annotation"]');

    this.highlightRadios.forEach((radio) => {
      radio.addEventListener("change", this.updateVisibility);
    });

    this.updateVisibility();
  }

  getSelectedHighlight() {
    const checked = Array.from(this.highlightRadios).find((r) => r.checked);
    return checked ? checked.value : "off";
  }

  updateVisibility() {
    const highlight = this.getSelectedHighlight();

    if (highlight === "no") {
      // Hide annotation filter when highlight is "no"
      this.element.style.display = "none";
      // Reset annotation to "off" if it's not already
      this.annotationRadios.forEach((radio) => {
        if (radio.value === "off") {
          radio.checked = true;
        }
      });
    } else {
      // Show annotation filter when highlight is "off" or "yes"
      this.element.style.display = "";
    }
  }

  destroy() {
    this.highlightRadios.forEach((radio) => {
      radio.removeEventListener("change", this.updateVisibility);
    });
  }
}

registerBehavior("ld-annotation-filter", AnnotationFilterBehavior);
