import { state } from "./state.js";
import { API_BASE } from "./config.js";
import { naFetch } from "./http.js";
import {
  openWindow,
  closeWindow,
  focusWindow,
  getWindow,
  getWindowBody,
  getWindowAction,
} from "./modules/window-manager/index.js";
import { messageBus } from "./message-bus.js";
import { setThreadCompacting } from "./composer.js";
import { syncSendButton } from "./chat-ui.js";

var WIN_ID = "session-context";
var _boundTid = "";

function fmtTokens(n) {
  n = Number(n) || 0;
  if (n >= 1000000) return (n / 1000000).toFixed(n % 1000000 === 0 ? 0 : 1) + "M";
  if (n >= 1000) return (n / 1000).toFixed(n % 1000 === 0 ? 0 : 1) + "k";
  return String(n);
}

function parseTokens(raw) {
  var text = String(raw || "").trim().toLowerCase();
  var m = text.match(/^([0-9]+(?:\.[0-9]+)?)\s*([km]?)$/);
  if (!m) throw new Error("上下文长度只能填数字，可带 k 或 m，例如 1m、200k、128000");
  var value = parseFloat(m[1]);
  if (m[2] === "k") value *= 1000;
  if (m[2] === "m") value *= 1000000;
  value = Math.round(value);
  if (value <= 0) throw new Error("上下文长度必须大于 0");
  return value;
}

function currentThread() {
  var tid = _boundTid || state.currentConversationId;
  if (!tid) throw new Error("session-context missing threadId");
  return tid;
}

function loadContext(threadId) {
  return naFetch(API_BASE + "/api/v1/session-context?threadId=" + encodeURIComponent(threadId))
    .then(function (r) {
      if (!r.ok) throw new Error("session-context load failed: " + r.status);
      return r.json();
    });
}

function postJson(path, payload) {
  return naFetch(API_BASE + path, {
    method: path === "/api/v1/session-context" ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then(function (r) {
    return r.json().then(function (data) {
      if (!r.ok || !data.ok) throw new Error(data.error || data.detail || "请求失败: " + r.status);
      return data;
    });
  });
}

function setBusy(busy) {
  ["force", "compact", "cancel", "save"].forEach(function (id) {
    var btn = getWindowAction(WIN_ID, id);
    if (btn) btn.disabled = busy;
  });
}

//: 强力压缩会把整场对话烧掉,按一下不算数,得再按一次确认。
var _forceArmed = false;

function disarmForce() {
  _forceArmed = false;
  var btn = getWindowAction(WIN_ID, "force");
  if (btn) {
    btn.textContent = "强力压缩";
    btn.classList.remove("is-armed");
  }
}

function showError(message) {
  var body = getWindowBody(WIN_ID);
  if (!body) return;
  var el = body.querySelector("#ctxWinError");
  if (!el) return;
  el.textContent = message;
  el.hidden = false;
}

function row(label, value) {
  return '<div class="ctxwin-row"><span>' + label + "</span><b>" + value + "</b></div>";
}

function renderPanel(body, res) {
  var usage = res.usage;
  var pct = Number(usage.usage_percent) || 0;
  body.innerHTML =
    '<div class="ctxwin">'
    + '<div class="ctxwin-bar"><i style="width:' + Math.max(0, Math.min(100, pct)) + '%"></i></div>'
    + row("已用", fmtTokens(usage.used) + " / " + fmtTokens(usage.context_length) + " tokens（" + pct + "%）")
    + row("压缩阈值", fmtTokens(usage.threshold_tokens) + "（" + usage.threshold_percent + "%）")
    + row("已压缩次数", String(usage.compression_count))
    + '<label class="ctxwin-label">本对话的上下文长度</label>'
    + '<input id="ctxWinInput" class="ctxwin-input" type="text" autocomplete="off" spellcheck="false" value="'
    + fmtTokens(usage.context_length) + '">'
    + '<p class="ctxwin-hint">可填 1m、200k 或具体数字。到达上限的 '
    + usage.threshold_percent + '% 时自动压缩，也可以按下面的「立即压缩」手动压一次：'
    + '前三分之一叫模型总结成一段文字，中间三分之一就地摊平成一条条工具记录（丢掉思考、'
    + '裁掉超长的结果），最后三分之一原样保留。'
    + '压缩会替换掉这个对话的历史记录，原文不再保留；压缩期间不能发消息。'
    + '如果压到最后仍然降不下来（尾段本身就超了），用左下角的「强力压缩」把整场对话烧成一条摘要，'
    + '那个只能手动按，自动压缩永远不会走它。</p>'
    + '<p id="ctxWinError" class="ctxwin-error" hidden></p>'
    + "</div>";
}

function fillWindow() {
  var tid = state.currentConversationId;
  if (!tid) throw new Error("session-context missing threadId");
  _boundTid = tid;
  var body = getWindowBody(WIN_ID);
  if (!body) throw new Error("session-context window body missing");
  disarmForce();
  body.innerHTML = '<div class="ctxwin-loading">加载中...</div>';
  return loadContext(tid).then(function (res) {
    var next = getWindowBody(WIN_ID);
    if (!next) return;
    if (String(_boundTid) !== String(state.currentConversationId)) return;
    renderPanel(next, res);
  });
}

function onSave() {
  var body = getWindowBody(WIN_ID);
  if (!body) return;
  var input = body.querySelector("#ctxWinInput");
  if (!input) return;
  var errorEl = body.querySelector("#ctxWinError");
  if (errorEl) errorEl.hidden = true;
  var value;
  try {
    value = parseTokens(input.value);
  } catch (err) {
    showError(err.message);
    return;
  }
  setBusy(true);
  postJson("/api/v1/session-context", { threadId: currentThread(), window: value })
    .then(function () {
      setBusy(false);
      closeWindow(WIN_ID);
    })
    .catch(function (err) {
      setBusy(false);
      showError(err.message);
    });
}

function runCompact(path) {
  var tid = currentThread();
  var body = getWindowBody(WIN_ID);
  if (body) {
    var errorEl = body.querySelector("#ctxWinError");
    if (errorEl) errorEl.hidden = true;
  }
  setBusy(true);
  // 压完聊天记录就变了,但这里不自己重拉 —— 后端会广播 context_compacted,
  // 由下面那个订阅统一刷新,本页和别的页走同一条路径,不会拉两遍。
  postJson(path, { threadId: tid })
    .then(function () {
      setBusy(false);
      if (getWindow(WIN_ID)) fillWindow();
    })
    .catch(function (err) {
      setBusy(false);
      showError(err.message);
    });
}

function onCompact() {
  disarmForce();
  runCompact("/api/v1/session-context/compact");
}

function onForceCompact() {
  if (!_forceArmed) {
    _forceArmed = true;
    var btn = getWindowAction(WIN_ID, "force");
    if (btn) {
      btn.textContent = "确认烧掉全部记录";
      btn.classList.add("is-armed");
    }
    showError("强力压缩会把这个对话的全部记录换成一条摘要，原文不再保留。再按一次执行。");
    return;
  }
  disarmForce();
  runCompact("/api/v1/session-context/force-compact");
}

export function syncContextWindow() {
  if (!getWindow(WIN_ID)) return;
  if (!state.currentConversationId) {
    closeWindow(WIN_ID);
    return;
  }
  fillWindow();
}

export function openContextWindow() {
  if (!state.currentConversationId) throw new Error("session-context missing threadId");
  if (getWindow(WIN_ID)) {
    focusWindow(WIN_ID);
    fillWindow();
    return;
  }
  openWindow(WIN_ID, {
    title: "上下文",
    width: 440,
    height: 360,
    minWidth: 340,
    minHeight: 240,
    footerActions: [
      {
        id: "force",
        label: "强力压缩",
        title: "压不动时用：整场对话烧成一条摘要，只能手动触发",
        variant: "danger",
        onClick: onForceCompact,
      },
      { id: "compact", label: "立即压缩", title: "现在就压一次，不等到阈值", onClick: onCompact },
      { id: "cancel", label: "取消", onClick: function () { closeWindow(WIN_ID); } },
      { id: "save", label: "保存", variant: "primary", onClick: onSave },
    ],
    onMount: function (body) {
      body.innerHTML = '<div class="ctxwin-loading">加载中...</div>';
    },
    onClose: function () {
      _forceArmed = false;
      _boundTid = "";
    },
  });
  fillWindow();
}

export function initContextWindow() {
  var btn = document.getElementById("contextWindowBtn");
  if (!btn) throw new Error("contextWindowBtn missing");
  btn.onclick = function (e) {
    e.stopPropagation();
    openContextWindow();
  };
  messageBus.on("turn_complete", function () {
    syncContextWindow();
  });
  // 压缩期间这个对话是独占的:后端在 ws_chat 的占用表里占了位,这时发消息会被
  // 以 task_active 拒掉。所以输入框和发送按钮一起锁上,别让人白打一段字。
  messageBus.on("context_compacting", function (msg) {
    var data = (msg && msg.data) || {};
    if (!data.threadId) throw new Error("context_compacting missing threadId");
    setThreadCompacting(data.threadId, !!data.active);
    syncSendButton();
  });
  // 别的连接手动压过之后,这边的历史已经过期了,重拉一次。
  messageBus.on("context_compacted", function (msg) {
    var tid = msg && msg.data && msg.data.threadId;
    if (!tid) throw new Error("context_compacted missing threadId");
    if (String(tid) !== String(state.currentConversationId)) return;
    import("./history.js").then(function (mod) {
      mod.reloadThreadHistory(tid, "context_compacted");
    });
    syncContextWindow();
  });
}
