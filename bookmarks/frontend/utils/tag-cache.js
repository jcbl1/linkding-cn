import { api } from "../api.js";

// CJK Unified Ideographs and common extensions
const CJK_RE = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;

function hasCJK(name) {
  return CJK_RE.test(name);
}

class Cache {
  constructor(api) {
    this.api = api;

    // Reset cached tags after a form submission
    document.addEventListener("turbo:submit-end", () => {
      this.tagsPromise = null;
    });
  }

  getTags() {
    if (!this.tagsPromise) {
      this.tagsPromise = this.api
        .getTags({
          limit: 5000,
          offset: 0,
        })
        .then((tags) => {
          // Sort: non-CJK tags first, then CJK tags; alphabetically within each group
          tags.sort((left, right) => {
            const leftCJK = hasCJK(left.name) ? 1 : 0;
            const rightCJK = hasCJK(right.name) ? 1 : 0;
            if (leftCJK !== rightCJK) return leftCJK - rightCJK;
            return left.name.toLowerCase().localeCompare(right.name.toLowerCase());
          });
          return tags;
        })
        .catch((e) => {
          console.warn("Cache: Error loading tags", e);
          return [];
        });
    }

    return this.tagsPromise;
  }
}

export const cache = new Cache(api);
