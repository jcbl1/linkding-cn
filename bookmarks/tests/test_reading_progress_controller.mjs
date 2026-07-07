/**
 * ReadingProgressController 前端状态机测试。
 * 运行: node --experimental-vm-modules bookmarks/tests/test_reading_progress_controller.mjs
 *
 * 测试 _isRemoteProgressUpdate 逻辑和状态机流转，防止回归。
 */

// ---- 最小化模拟 ----

class MockElement {
  constructor() {
    this.scrollTop = 0;
    this.scrollHeight = 5000;
    this.clientHeight = 800;
    this.clientWidth = 1000;
    this.listeners = {};
  }
  addEventListener(event, fn) {
    (this.listeners[event] ??= []).push(fn);
  }
  removeEventListener() {}
  getBoundingClientRect() {
    return { top: 0, bottom: this.clientHeight, left: 0, right: this.clientWidth, width: this.clientWidth, height: this.clientHeight };
  }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  appendChild() {}
  remove() {}
  setAttribute() {}
  get style() { return {}; }
}

// 从 reader.js 中提取的工具函数
function getScrollableHeight(el) {
  return Math.max(0, el.scrollHeight - el.clientHeight);
}
function isAtReadingEnd(el) {
  const h = getScrollableHeight(el);
  return h <= 24 || el.scrollTop >= h - 24;
}
function getScrollMetrics(el) {
  const h = getScrollableHeight(el);
  const raw = h > 0 ? Math.min(1, Math.max(0, el.scrollTop / h)) : 1;
  return {
    progress: isAtReadingEnd(el) ? 1 : raw,
    scroll_top: Math.round(el.scrollTop),
    scroll_height: Math.round(el.scrollHeight),
    client_width: Math.round(el.clientWidth),
    client_height: Math.round(el.clientHeight),
  };
}

// 最小化 ReadingProgressController（只测试状态机和冲突逻辑）
// 状态：loading → resume / active → remote → active（Go to/Override）或 closed（Close）
class TestController {
  static MIN_RESUME_SCROLL_TOP = 600;
  static MIN_RESUME_PROGRESS = 0.02;

  constructor() {
    this.state = "loading";
    this._progressData = null;
    this._remoteProgress = null;
    this._remoteToastShown = 0;
    this.contentEl = new MockElement();
    this.readingRoot = new MockElement();
  }

  // 从 reader.js 复制的 _isRemoteProgressUpdate 逻辑
  // remote/closed 状态阻止新 toast
  _isRemoteProgressUpdate(serverData) {
    if (this.state === "remote" || this.state === "closed") return false;
    const serverScroll = serverData.scroll_top || 0;
    const localScroll = this._progressData?.scroll_top || 0;
    const atEnd = (serverData.progress || 0) >= 0.98;
    return (
      serverScroll >= TestController.MIN_RESUME_SCROLL_TOP &&
      !atEnd &&
      Math.abs(serverScroll - localScroll) >= TestController.MIN_RESUME_SCROLL_TOP
    );
  }

  // 模拟 _showRemoteToast
  _showRemoteToast(progress) {
    this._remoteToastShown++;
    this.state = "remote";
    this._remoteProgress = progress;
  }

  // 模拟 _enterSaving
  _enterSaving() {
    this.state = "active";
  }

  // 模拟用户点击 Go to → 回到 active（后续冲突仍弹 toast）
  simulateGoTo() {
    this._enterSaving();
  }

  // 模拟用户点击 Override → 回到 active（后续冲突仍弹 toast）
  simulateOverride() {
    this._enterSaving();
  }

  // 模拟用户点击 Close → closed 状态（不保存、不弹 toast）
  simulateClose() {
    this._remoteProgress = null;
    this.state = "closed";
  }

  // 模拟 _syncFromServer 中的冲突检测逻辑
  // remote 状态：更新 _remoteProgress（不弹新 toast）
  // 其他状态：检查 _isRemoteProgressUpdate → 弹 toast 或跳过
  handleServerData(data) {
    if (data && this.state === "remote") {
      this._remoteProgress = data;
    } else if (data && this._isRemoteProgressUpdate(data)) {
      this._showRemoteToast(data);
    }
    if (data) this._progressData = data;
  }
}

// ---- 测试 ----

let passed = 0;
let failed = 0;

function assert(condition, message) {
  if (condition) {
    passed++;
  } else {
    failed++;
    console.error(`  FAIL: ${message}`);
  }
}

function test(name, fn) {
  console.log(`  ${name}`);
  fn();
}

console.log("ReadingProgressController state machine tests:\n");

// Test 1: _isRemoteProgressUpdate 基本行为
console.log("1. _isRemoteProgressUpdate basic behavior:");
test("returns true when server has significant scroll delta", () => {
  const c = new TestController();
  c.state = "active";
  c._progressData = { scroll_top: 100, progress: 0.05 };
  const result = c._isRemoteProgressUpdate({ scroll_top: 800, progress: 0.3 });
  assert(result === true, `expected true, got ${result}`);
});

test("returns false when scroll delta is too small", () => {
  const c = new TestController();
  c.state = "active";
  c._progressData = { scroll_top: 100, progress: 0.05 };
  const result = c._isRemoteProgressUpdate({ scroll_top: 300, progress: 0.1 });
  assert(result === false, `expected false, got ${result}`);
});

test("returns false when server scroll < MIN_RESUME_SCROLL_TOP", () => {
  const c = new TestController();
  c.state = "active";
  c._progressData = { scroll_top: 0, progress: 0 };
  const result = c._isRemoteProgressUpdate({ scroll_top: 100, progress: 0.05 });
  assert(result === false, `expected false, got ${result}`);
});

test("returns false when at end (progress >= 0.98)", () => {
  const c = new TestController();
  c.state = "active";
  c._progressData = { scroll_top: 100, progress: 0.05 };
  const result = c._isRemoteProgressUpdate({ scroll_top: 5000, progress: 0.99 });
  assert(result === false, `expected false, got ${result}`);
});

// Test 2: remote 状态不弹新 toast（更新现有 toast）
console.log("\n2. Remote state — no new toast, updates existing:");
test("returns false when state is 'remote'", () => {
  const c = new TestController();
  c.state = "remote";
  c._progressData = { scroll_top: 100, progress: 0.05 };
  const result = c._isRemoteProgressUpdate({ scroll_top: 800, progress: 0.3 });
  assert(result === false, `expected false, got ${result}`);
});

test("handleServerData updates _remoteProgress when state is 'remote'", () => {
  const c = new TestController();
  c.state = "remote";
  c._progressData = { scroll_top: 100, progress: 0.05 };
  c._remoteProgress = { scroll_top: 800, progress: 0.3, date_modified: "T1" };

  c.handleServerData({ scroll_top: 1200, progress: 0.5, date_modified: "T2" });

  assert(c._remoteToastShown === 0, `should not show new toast, got ${c._remoteToastShown}`);
  assert(c._remoteProgress.scroll_top === 1200, `_remoteProgress should be updated to 1200`);
  assert(c._remoteProgress.date_modified === "T2", `_remoteProgress.date_modified should be T2`);
});

// Test 3: closed 状态阻止新 toast
console.log("\n3. Closed state — no new toast:");
test("returns false when state is 'closed'", () => {
  const c = new TestController();
  c.state = "closed";
  c._progressData = { scroll_top: 100, progress: 0.05 };
  const result = c._isRemoteProgressUpdate({ scroll_top: 800, progress: 0.3 });
  assert(result === false, `expected false, got ${result}`);
});

// Test 4: Go to / Override 后回到 active，后续冲突仍弹新 toast
console.log("\n4. Go to / Override → back to active → new toast on next conflict:");
test("Go to → subsequent 409 shows new toast", () => {
  const c = new TestController();
  c.state = "active";
  c._progressData = { scroll_top: 100, progress: 0.05 };

  // 第一次冲突 → 弹 toast
  c.handleServerData({ scroll_top: 800, progress: 0.3, date_modified: "T1" });
  assert(c.state === "remote", `state should be remote, got ${c.state}`);
  assert(c._remoteToastShown === 1, `should show 1 toast, got ${c._remoteToastShown}`);

  // 用户点击 Go to → 回到 active
  c.simulateGoTo();
  assert(c.state === "active", `state should be active, got ${c.state}`);

  // 第二次冲突（scroll_top 差值 ≥ 600）→ 弹新 toast
  c.handleServerData({ scroll_top: 1600, progress: 0.6, date_modified: "T2" });
  assert(c._remoteToastShown === 2, `should show 2 toasts, got ${c._remoteToastShown}`);
  assert(c.state === "remote", `state should be remote, got ${c.state}`);
});

test("Override → subsequent 409 shows new toast", () => {
  const c = new TestController();
  c.state = "active";
  c._progressData = { scroll_top: 100, progress: 0.05 };

  c.handleServerData({ scroll_top: 800, progress: 0.3, date_modified: "T1" });
  assert(c._remoteToastShown === 1, `should show 1 toast`);

  c.simulateOverride();
  assert(c.state === "active", `state should be active, got ${c.state}`);

  c.handleServerData({ scroll_top: 1600, progress: 0.6, date_modified: "T2" });
  assert(c._remoteToastShown === 2, `should show 2 toasts, got ${c._remoteToastShown}`);
});

// Test 5: Close 后不再弹 toast
console.log("\n5. Close → no more toasts:");
test("Close → subsequent conflict does not show toast", () => {
  const c = new TestController();
  c.state = "active";
  c._progressData = { scroll_top: 100, progress: 0.05 };

  c.handleServerData({ scroll_top: 800, progress: 0.3, date_modified: "T1" });
  assert(c._remoteToastShown === 1, `should show 1 toast`);

  c.simulateClose();
  assert(c.state === "closed", `state should be closed, got ${c.state}`);

  c.handleServerData({ scroll_top: 1200, progress: 0.5, date_modified: "T2" });
  assert(c._remoteToastShown === 1, `should still show 1 toast, got ${c._remoteToastShown}`);
});

// ---- 结果 ----
console.log(`\n${"=".repeat(50)}`);
console.log(`Results: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
} else {
  console.log("All tests passed!");
}
