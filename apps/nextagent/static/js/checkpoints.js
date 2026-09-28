import { state, getConvState } from "./state.js";

var RESTORE_ICON = "\u21ba";
var RESTORE_CONFIRM =
  "\u5c06\u628a\u5de5\u4f5c\u533a\u6587\u4ef6\u6062\u590d\u5230\u8fd9\u6761\u6d88\u606f\u4e4b\u524d\u7684\u72b6\u6001\u3002\n" +
  "\u8fd9\u4e4b\u540e AI \u5bf9\u6587\u4ef6\u7684\u6539\u52a8\u4f1a\u88ab\u8fd8\u539f\uff0c\u4f60\u81ea\u5df1\u5728\u8fd9\u4e9b\u6587\u4ef6\u4e0a\u7684\u624b\u6539\u4e5f\u4f1a\u4e00\u8d77\u88ab\u8fd8\u539f\u3002\n" +
  "\u5bf9\u8bdd\u4e0d\u4f1a\u88ab\u5220\u9664\uff0c\u53ef\u4ee5\u64a4\u9500\u3002\u7ee7\u7eed\uff1f";

function _busy(cs) {
  return !!(cs && (cs.isStreaming || cs.pendingSend));
}

function _threadOf(row) {
  var area = row && row.closest ? row.closest(".chat-area") : null;
  return (area && area.dataset.threadId) || state.currentConversationId || "";
}

export function requestRestore(threadId, checkpointId) {
  var cs = threadId ? getConvState(threadId) : null;
  if (_busy(cs)) return false;
  if (!window.confirm(RESTORE_CONFIRM)) return false;
  state.api.sendWsMsg("checkpoint_restore", { threadId: threadId, checkpointId: checkpointId });
  return true;
}

export function requestRedo(threadId) {
  var cs = threadId ? getConvState(threadId) : null;
  if (_busy(cs)) return false;
  state.api.sendWsMsg("checkpoint_redo", { threadId: threadId });
  return true;
}

export function createRestoreBtn(row) {
  var btn = document.createElement("button");
  btn.className = "action-btn checkpoint-restore-btn";
  btn.title = "\u6062\u590d\u5230\u8fd9\u6761\u6d88\u606f\u4e4b\u524d\u7684\u6587\u4ef6\u72b6\u6001";
  btn.textContent = RESTORE_ICON;
  btn.hidden = !row.dataset.checkpointId;
  btn.onclick = function () {
    var cpId = row.dataset.checkpointId || "";
    if (!cpId) return;
    requestRestore(_threadOf(row), cpId);
  };
  return btn;
}

export function setUserBubbleCheckpoint(row, checkpointId) {
  if (!row || !checkpointId) return;
  row.dataset.checkpointId = String(checkpointId);
  var btn = row.querySelector(".checkpoint-restore-btn");
  if (btn) btn.hidden = false;
}

export function addCheckpointEntry(area, checkpointId, label) {
  var row = document.createElement("div");
  row.className = "msg-row checkpoint-entry";
  row.dataset.checkpointId = String(checkpointId);
  var btn = document.createElement("button");
  btn.className = "checkpoint-entry-btn";
  btn.textContent = RESTORE_ICON + " " + label;
  btn.onclick = function () {
    requestRestore(_threadOf(row), String(checkpointId));
  };
  row.appendChild(btn);
  area.appendChild(row);
  return row;
}

function _removeRedoBar(cs) {
  var bars = cs.el.querySelectorAll(".checkpoint-redo-bar");
  for (var i = 0; i < bars.length; i++) bars[i].parentNode.removeChild(bars[i]);
}

function _redoBar(cs) {
  var bar = document.createElement("div");
  bar.className = "checkpoint-redo-bar";
  var text = document.createElement("span");
  text.textContent = "\u6587\u4ef6\u5df2\u6062\u590d\u5230\u8fd9\u6761\u6d88\u606f\u4e4b\u524d\u7684\u72b6\u6001\uff0c\u540e\u9762\u7684\u5bf9\u8bdd\u5df2\u56de\u9000\u3002\u7ee7\u7eed\u53d1\u6d88\u606f\u4f1a\u5220\u6389\u5b83\u4eec\u3002";
  bar.appendChild(text);
  if (cs.checkpointState && cs.checkpointState.hasLatest) {
    var btn = document.createElement("button");
    btn.className = "checkpoint-redo-btn";
    btn.textContent = "\u64a4\u9500\u6062\u590d";
    btn.onclick = function () {
      requestRedo(cs.id);
    };
    bar.appendChild(btn);
  }
  return bar;
}

export function applyCheckpointCursor(cs) {
  if (!cs || !cs.el) return;
  _removeRedoBar(cs);
  var cursor = (cs.checkpointState && cs.checkpointState.cursor) || "";
  var children = cs.el.children;
  var i;
  if (!cursor) {
    for (i = 0; i < children.length; i++) children[i].classList.remove("reverted");
    return;
  }
  var win = cs.historyWindow;
  var cursorIdx = -1;
  if (win && win.events) {
    for (i = 0; i < win.events.length; i++) {
      if (String(win.events[i].checkpointId || "") === cursor) {
        cursorIdx = i;
        break;
      }
    }
  }
  var firstRendered = win && win.turns && win.turns.length ? win.turns[win.renderedTurn] || 0 : 0;
  var passed = cursorIdx >= 0 && cursorIdx < firstRendered;
  var anchor = null;
  for (i = 0; i < children.length; i++) {
    var el = children[i];
    if (el.classList.contains("history-more-sentinel")) continue;
    if (!passed && el.dataset && el.dataset.checkpointId === cursor) {
      passed = true;
      anchor = el;
    }
    el.classList.toggle("reverted", passed);
  }
  if (!passed) return;
  var bar = _redoBar(cs);
  if (anchor) {
    cs.el.insertBefore(bar, anchor);
  } else {
    var first = cs.el.firstChild;
    while (first && first.classList && first.classList.contains("history-more-sentinel")) first = first.nextSibling;
    cs.el.insertBefore(bar, first);
  }
}

export function discardRevertedRows(cs) {
  if (!cs || !cs.el) return;
  var cursor = (cs.checkpointState && cs.checkpointState.cursor) || "";
  if (!cursor) return;
  _removeRedoBar(cs);
  var children = Array.prototype.slice.call(cs.el.children);
  var passed = false;
  var firstReverted = cs.el.querySelector(".reverted");
  for (var i = 0; i < children.length; i++) {
    var el = children[i];
    if (!passed && (el === firstReverted || (el.dataset && el.dataset.checkpointId === cursor))) passed = true;
    if (passed) cs.el.removeChild(el);
  }
  cs.el.dataset.userCount = String(cs.el.querySelectorAll(".msg-row.user").length);
  cs.checkpointState = { cursor: "", hasLatest: false };
}

export function clearCheckpointCursorState(cs) {
  if (!cs) return;
  cs.checkpointState = { cursor: "", hasLatest: false };
  applyCheckpointCursor(cs);
}
