import { WS_URL } from "./config.js";
import { state, convCache, taskMirror, taskLastSeq } from "./state.js";
import { handleMessage } from "./message-handler.js";
import { addSystemMsg, refreshAiBubbleCopyState } from "./chat-ui.js";

let heartbeatTimer = null;
var visibilityBound = false;
var HEARTBEAT_INTERVAL_MS = 30000;
var ACK_TIMEOUT_MS = 8000;
var ACK_MAX_RETRY = 1;
var pendingAcks = {};
var outbox = [];

function createMessageId() {
  if (!globalThis.crypto || typeof globalThis.crypto.getRandomValues !== "function") {
    throw new Error("当前浏览器不支持 Web Crypto");
  }
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, function (b) {
    return b.toString(16).padStart(2, "0");
  });
  return [
    hex.slice(0, 4).join(""),
    hex.slice(4, 6).join(""),
    hex.slice(6, 8).join(""),
    hex.slice(8, 10).join(""),
    hex.slice(10, 16).join("")
  ].join("-");
}

function resetConnectionState() {
  state.connectionId = "";
  state.pendingPingMessageId = "";
  state.lastPingAt = 0;
  state.lastPongAt = 0;
}

function sendHeartbeatPing() {
  var pingId = sendWsMsg("ping", {
    connectionId: state.connectionId || "",
    threadId: state.currentConversationId || "",
    visible: state.pageVisible,
  });
  if (!pingId) return;
  state.lastPingAt = Date.now();
  state.pendingPingMessageId = pingId;
}

function bindVisibilityListener() {
  if (visibilityBound || typeof document === "undefined") return;
  visibilityBound = true;
  state.pageVisible = document.visibilityState === "visible";
  document.addEventListener("visibilitychange", function () {
    state.pageVisible = document.visibilityState === "visible";
  });
}

function startHeartbeat() {
  stopHeartbeat();
  bindVisibilityListener();
  heartbeatTimer = setInterval(function () {
    sendHeartbeatPing();
    state.heartbeatCount++;
  }, HEARTBEAT_INTERVAL_MS);
}

function stopHeartbeat() {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
}

export function sendWsMsg(type, data) {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return null;
  const id = createMessageId();
  data = data || {};
  const threadScopedTypes = {
    text: true,
    clear_context: true,
    edit_and_regenerate: true,
    edit_ai_message: true,
    thread_focus: true,
    resume: true,
  };
  if (threadScopedTypes[type] && !data.threadId && type !== "resume") {
    console.error("[ws] missing threadId for message type:", type, data);
    return null;
  }
  const msg = {
    type: type,
    messageId: id,
    timestamp: Date.now(),
    topic: "chat",
    data: data
  };
  state.ws.send(JSON.stringify(msg));
  return id;
}

function registerPendingAck(wsId, type, data, retryCount, onAck, onFail) {
  var entry = {
    type: type,
    data: data,
    retryCount: retryCount,
    onAck: onAck,
    onFail: onFail,
    timer: null,
  };
  entry.timer = setTimeout(function () {
    handleAckTimeout(wsId);
  }, ACK_TIMEOUT_MS);
  pendingAcks[wsId] = entry;
}

function handleAckTimeout(wsId) {
  var entry = pendingAcks[wsId];
  if (!entry) return;
  delete pendingAcks[wsId];
  if (entry.retryCount >= ACK_MAX_RETRY) {
    if (entry.onFail) entry.onFail("ack_timeout", wsId);
    return;
  }
  var retryId = sendWsMsg(entry.type, entry.data);
  if (!retryId) {
    if (entry.onFail) entry.onFail("connection_down", wsId);
    return;
  }
  registerPendingAck(retryId, entry.type, entry.data, entry.retryCount + 1, entry.onAck, entry.onFail);
}

export function sendReliable(type, data, callbacks) {
  var onAck = callbacks && callbacks.onAck;
  var onFail = callbacks && callbacks.onFail;
  var wsId = sendWsMsg(type, data);
  if (!wsId) {
    if (onFail) onFail("connection_down", "");
    return null;
  }
  registerPendingAck(wsId, type, data, 0, onAck, onFail);
  return wsId;
}

export function resolvePendingAck(messageId) {
  var key = String(messageId || "");
  var entry = pendingAcks[key];
  if (!entry) return false;
  clearTimeout(entry.timer);
  delete pendingAcks[key];
  if (entry.onAck) entry.onAck(key);
  return true;
}

export function pendingAckRetryCount(messageId) {
  var entry = pendingAcks[String(messageId || "")];
  if (!entry) return -1;
  return entry.retryCount;
}

export function discardPendingAck(messageId) {
  var key = String(messageId || "");
  var entry = pendingAcks[key];
  if (!entry) return false;
  clearTimeout(entry.timer);
  delete pendingAcks[key];
  return true;
}

export function rejectPendingAck(messageId, reason) {
  var key = String(messageId || "");
  var entry = pendingAcks[key];
  if (!entry) return false;
  clearTimeout(entry.timer);
  delete pendingAcks[key];
  if (entry.onFail) entry.onFail(reason || "rejected", key);
  return true;
}

export function enqueueOutbox(key, resend, onReplaced) {
  var k = String(key || "");
  for (var i = 0; i < outbox.length; i++) {
    if (outbox[i].key !== k) continue;
    var prev = outbox[i];
    outbox[i] = { key: k, resend: resend, onReplaced: onReplaced };
    if (prev.onReplaced) prev.onReplaced();
    return;
  }
  outbox.push({ key: k, resend: resend, onReplaced: onReplaced });
}

export function flushOutbox() {
  if (!outbox.length) return 0;
  var items = outbox.slice();
  outbox = [];
  for (var i = 0; i < items.length; i++) {
    items[i].resend();
  }
  return items.length;
}

export function isNetworkFailure(reason) {
  return reason === "connection_down" || reason === "ack_timeout";
}

function failAllPendingAcks(reason) {
  var keys = Object.keys(pendingAcks);
  for (var i = 0; i < keys.length; i++) {
    var entry = pendingAcks[keys[i]];
    delete pendingAcks[keys[i]];
    clearTimeout(entry.timer);
    if (entry.onFail) entry.onFail(reason, keys[i]);
  }
}

export function requestTaskResume(taskId, lastSeq) {
  if (!taskId) return;
  sendWsMsg("resume", {
    taskId: taskId,
    lastSeq: lastSeq || 0,
    threadId: state.currentConversationId || "",
  });
}

export function resumeAllInflightTasks() {
  for (var tid in taskMirror) {
    var t = taskMirror[tid];
    if (!t || !t.taskId) continue;
    var st = String(t.status || "");
    if (st !== "queued" && st !== "pending" && st !== "running") continue;
    var seq = taskLastSeq[tid];
    if (seq === undefined) {
      seq = t.lastSeq || t.snapshotSeq || 0;
    }
    requestTaskResume(tid, seq);
  }
}

export function connect() {
  state.bootstrapping = true;
  state.bootstrapPhase = "auth_pending";
  state.pendingHistorySnapshot = null;
  state.pendingBootstrapThreadId = null;
  resetConnectionState();
  state.ws = new WebSocket(WS_URL);

  state.ws.onopen = function () {
    document.getElementById("statusDot").className = "status-dot connected";
    document.getElementById("statusText").textContent = "已连接";
    state.onlineUserCount = 0;
    state.reconnectDelay = 5000;
    startHeartbeat();
    state.ws.send(JSON.stringify({
      type: "auth",
      messageId: createMessageId(),
      timestamp: Date.now(),
      topic: "chat",
      data: { threadId: state.currentConversationId || "" }
    }));
  };

  state.ws.onmessage = function (e) {
    var t0 = performance.now();
    const msg = JSON.parse(e.data);
    handleMessage(msg);
    var ms = performance.now() - t0;
    if (ms >= 16) {
      console.log(
        "[block_diag] handleMessage_slow type=" + (msg.type || "")
        + " ms=" + Math.round(ms)
        + " seq=" + (msg.seq || 0)
        + " taskId=" + ((msg.data && msg.data.taskId) || msg.taskId || "")
      );
    }
  };

  state.ws.onerror = function () {
    document.getElementById("statusDot").className = "status-dot disconnected";
    document.getElementById("statusText").textContent = "连接错误";
  };

  state.ws.onclose = function (e) {
    document.getElementById("statusDot").className = "status-dot disconnected";
    document.getElementById("statusText").textContent = "\u5df2\u65ad\u5f00";
    stopHeartbeat();
    resetConnectionState();
    state.ws = null;
    failAllPendingAcks("connection_down");
    state.bootstrapping = true;
    state.bootstrapPhase = "auth_pending";
    state.pendingHistorySnapshot = null;
    state.pendingBootstrapThreadId = null;

    for (var convId in convCache) {
      var cs = convCache[convId];
      if (cs.isStreaming && cs.currentStreamBubble) {
        var bubble = cs.currentStreamBubble.querySelector(".bubble");
        if (bubble && (bubble.classList.contains("is-loading") || bubble.classList.contains("is-media-loading"))) {
          bubble.classList.remove("is-loading", "is-media-loading");
          bubble.dataset.raw = "\u8fde\u63a5\u5df2\u65ad\u5f00\uff0c\u6b63\u5728\u91cd\u8fde...";
          bubble.textContent = "\u8fde\u63a5\u5df2\u65ad\u5f00\uff0c\u6b63\u5728\u91cd\u8fde...";
          bubble.classList.add("reconnecting");
          refreshAiBubbleCopyState(cs.currentStreamBubble);
        }
      }
      cs.historyLoaded = false;
    }

    const delaySec = state.reconnectDelay / 1000;
    addSystemMsg("\u8fde\u63a5\u5df2\u65ad\u5f00, " + delaySec + "\u79d2\u540e\u91cd\u8fde...");
    setTimeout(function () {
      connect();
    }, state.reconnectDelay);
    state.reconnectDelay = Math.min(state.reconnectDelay * 2, 40000);
  };
}

export function getWs() {
  return state.ws;
}
