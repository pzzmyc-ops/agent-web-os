import { taskMirror, taskToConv } from "./state.js";

var INFLIGHT = { queued: true, pending: true, running: true };

export function isInflightStatus(status) {
  return !!INFLIGHT[String(status || "")];
}

export function findChatInflightTask(threadId) {
  var tidKey = String(threadId || "");
  if (!tidKey) return "";
  for (var tid in taskMirror) {
    if (!Object.prototype.hasOwnProperty.call(taskMirror, tid)) continue;
    var row = taskMirror[tid];
    if (!row) continue;
    if (String(row.threadId || "") !== tidKey) continue;
    if (isInflightStatus(row.status)) return tid;
  }
  return "";
}

//: 正在压缩上下文的会话。压缩会把整个上下文区重写一遍,这期间发出去的消息会被
//: 后端以 task_active 拒掉(和回合中同一条路径),所以输入口要一起锁上。
var _compacting = {};

export function setThreadCompacting(threadId, active) {
  var key = String(threadId || "");
  if (!key) return;
  if (active) _compacting[key] = true;
  else delete _compacting[key];
}

export function isThreadCompacting(threadId) {
  return !!_compacting[String(threadId || "")];
}

export function deriveComposerAction(threadId, pendingSend, outboxFlushing) {
  if (isThreadCompacting(threadId)) {
    return { mode: "compacting", taskId: "", reason: "context_compacting" };
  }
  var chatTid = findChatInflightTask(threadId);
  if (chatTid) {
    return { mode: "stop", taskId: chatTid, reason: "chat_inflight" };
  }
  if (pendingSend) {
    return { mode: "waiting", taskId: "", reason: "pending_send" };
  }
  if (outboxFlushing) {
    return { mode: "waiting", taskId: "", reason: "outbox_flushing" };
  }
  return { mode: "send", taskId: "", reason: "idle" };
}

export function applyComposerState(cs, action) {
  if (!cs || !action) return;
  cs.isStreaming = action.mode === "stop";
  if (action.taskId) {
    cs.currentTaskId = action.taskId;
    cs.liveTaskId = action.taskId;
  } else if (!cs.isStreaming && !cs.retainedTaskId) {
    cs.currentTaskId = null;
    cs.liveTaskId = "";
  }
}

export function syncComposerForConv(cs, reason) {
  if (!cs) return { mode: "send", taskId: "", reason: "no_conv" };
  var action = deriveComposerAction(cs.id, !!cs.pendingSend, !!cs.outboxFlushing);
  applyComposerState(cs, action);
  return action;
}

export function applyHttpInflightBootstrap(threadId, cs, res) {
  if (!cs || !res) return "";
  var inflight = Array.isArray(res.inflight) ? res.inflight : [];
  var chatTid = String(res.chatInflightTaskId || "");
  for (var i = 0; i < inflight.length; i++) {
    var row = inflight[i];
    if (!row || !row.taskId) continue;
    taskMirror[row.taskId] = row;
    taskToConv[row.taskId] = String(row.threadId || threadId);
  }
  if (!chatTid) {
    chatTid = findChatInflightTask(threadId);
  }
  if (chatTid) {
    syncComposerForConv(cs);
  }
  return chatTid;
}
