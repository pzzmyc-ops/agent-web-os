import { state } from "./state.js";
import { API_BASE } from "./config.js";
import { naFetch } from "./http.js";
import { openWindow, closeWindow, focusWindow, getWindow, getWindowBody } from "./modules/window-manager/index.js";
import { messageBus } from "./message-bus.js";

var WIN_ID = "session-memory";
var _boundTid = "";

function loadMemory(threadId) {
  return naFetch(API_BASE + "/api/v1/session-memory?threadId=" + encodeURIComponent(threadId))
    .then(function (r) {
      if (!r.ok) throw new Error("session-memory load failed: " + r.status);
      return r.json();
    })
    .then(function (res) {
      if (typeof res.text !== "string") throw new Error("session-memory text must be a string");
      return res.text;
    });
}

function saveMemory(threadId, text) {
  return naFetch(API_BASE + "/api/v1/session-memory", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ threadId: threadId, text: text }),
  }).then(function (r) {
    if (!r.ok) throw new Error("session-memory save failed: " + r.status);
    return r.json();
  });
}

function renderEditor(body, text) {
  body.innerHTML = "";
  var wrap = document.createElement("div");
  wrap.className = "memory-editor";
  var ta = document.createElement("textarea");
  ta.className = "memory-editor-text";
  ta.value = text;
  var controls = document.createElement("div");
  controls.className = "memory-editor-controls";
  var cancelBtn = document.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "memory-editor-cancel";
  cancelBtn.textContent = "取消";
  cancelBtn.onclick = function () {
    closeWindow(WIN_ID);
  };
  var saveBtn = document.createElement("button");
  saveBtn.type = "button";
  saveBtn.className = "memory-editor-save";
  saveBtn.textContent = "保存";
  saveBtn.onclick = function () {
    var tid = _boundTid || state.currentConversationId;
    if (!tid) throw new Error("session-memory missing threadId");
    saveBtn.disabled = true;
    saveMemory(tid, ta.value).then(function () {
      closeWindow(WIN_ID);
    });
  };
  controls.appendChild(cancelBtn);
  controls.appendChild(saveBtn);
  wrap.appendChild(ta);
  wrap.appendChild(controls);
  body.appendChild(wrap);
  ta.focus();
}

function fillWindow() {
  var tid = state.currentConversationId;
  if (!tid) throw new Error("session-memory missing threadId");
  _boundTid = tid;
  var body = getWindowBody(WIN_ID);
  if (!body) throw new Error("session-memory window body missing");
  body.innerHTML = '<div class="memory-editor-loading">加载中...</div>';
  loadMemory(tid).then(function (text) {
    var next = getWindowBody(WIN_ID);
    if (!next) return;
    if (String(_boundTid) !== String(state.currentConversationId)) return;
    renderEditor(next, text);
  });
}

export function syncMemoryWindow() {
  if (!getWindow(WIN_ID)) return;
  if (!state.currentConversationId) {
    closeWindow(WIN_ID);
    return;
  }
  fillWindow();
}

export function openMemoryWindow() {
  var tid = state.currentConversationId;
  if (!tid) throw new Error("session-memory missing threadId");
  var existing = getWindow(WIN_ID);
  if (existing) {
    focusWindow(WIN_ID);
    fillWindow();
    return existing;
  }
  var win = openWindow(WIN_ID, {
    title: "记忆",
    width: 480,
    height: 420,
    minWidth: 320,
    minHeight: 240,
    onMount: function (body) {
      body.innerHTML = '<div class="memory-editor-loading">加载中...</div>';
    },
    onClose: function () {
      _boundTid = "";
    },
  });
  fillWindow();
  return win;
}

export function initMemory() {
  var btn = document.getElementById("memoryBtn");
  if (!btn) throw new Error("memoryBtn missing");
  btn.onclick = function (e) {
    e.stopPropagation();
    openMemoryWindow();
  };
  messageBus.on("memory_changed", function (msg) {
    var tid = msg && msg.data && msg.data.threadId;
    if (!tid) throw new Error("memory_changed missing threadId");
    if (String(tid) !== String(state.currentConversationId)) return;
    if (!getWindow(WIN_ID)) return;
    fillWindow();
  });
}
