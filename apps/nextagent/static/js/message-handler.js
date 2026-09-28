import { state, activeConv, getConvState, taskToConv, convCache, taskMirror, taskLastSeq } from "./state.js";
import { syncComposerForConv, findChatInflightTask } from "./composer.js";
import { resumeAllInflightTasks, requestTaskResume, resolvePendingAck, rejectPendingAck, discardPendingAck, pendingAckRetryCount, flushOutbox } from "./ws.js";
import { resolveFileUrl } from "./asset-url.js";
import { messageBus } from "./message-bus.js";
import {
  addSystemMsg,
  addUserBubble,
  addAiBubble,
  setSendBtnSend,
  setSendBtnStop,
  syncSendButton,
  setBubbleContent,
  appendBubbleContent,
  setThinkContent,
  appendThinkContent,
  setThinkDone,
  hideThinkBlock,
  syncCurrentPageThinkToggle,
  scrollToBottom,
  shouldAutoFollowScroll,
  refreshAiBubbleCopyState,
  clearThreadSendFailures,
  getChatAreaEl,
} from "./chat-ui.js";
import {
  blocksResetTurn,
  clearStreamTimeline,
  upsertBlock,
  blockOpen,
  blockDelta,
  blockEnd,
  blockPatch,
  blockRemove,
  hasRenderedBlock,
  applyBlocksSnapshot,
  applyReconnectBlocksSnapshot,
  rebindTaskTimeline,
  showTurnWaiting,
  removeTurnWaiting,
  renderAgentRecallTrigger,
} from "./blocks-renderer.js";
import { showPlanReviewForCall, applyTodoList, restorePlanReviewFromBlocks, applyPlanStatus, applyPlanDoc } from "./plan-review.js";
import {
  loadHistory,
  reloadThreadHistory,
  applyHistoryEvents,
  applyPendingHistoryForAllConvs,
  commitHistoryRender,
} from "./history.js";
import { renderConversationList, updateConversationTitle, initFirstConversation } from "./conversations.js";
import { saveConversationList, saveFolderList, saveCurrentConvId } from "./cache.js";
import { syncAssetsThread, handleAssetEvent } from "./assets.js";
import { upsertFromMediaReady, upsertNode } from "./asset-catalog.js";
import { openWindow, focusWindow, getWindow, closeWindow } from "./modules/window-manager/index.js";
import { formatContent } from "./format.js";
import { setUserBubbleCheckpoint } from "./checkpoints.js";
var _remoteToolOverlay = null;
var _beforeUnloadHandler = null;
var _REMOTE_TOOL_NAMES = [];

function _showRemoteToolOverlay(toolName) {
  void toolName;
  if (_remoteToolOverlay) return;
  var overlay = document.createElement("div");
  overlay.id = "remote-tool-overlay";
  overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.35);z-index:99999;display:flex;align-items:center;justify-content:center;pointer-events:auto;";
  var box = document.createElement("div");
  box.style.cssText = "background:var(--bg-surface,#23232b);border-radius:12px;padding:28px 36px;text-align:center;box-shadow:0 4px 24px rgba(0,0,0,0.3);max-width:360px;";
  var spinner = document.createElement("div");
  spinner.style.cssText = "width:36px;height:36px;border:3px solid var(--text-muted,#888);border-top-color:var(--accent-color,#6c8cff);border-radius:50%;animation:_rtSpin 0.8s linear infinite;margin:0 auto 16px;";
  var style = document.createElement("style");
  style.textContent = "@keyframes _rtSpin{to{transform:rotate(360deg)}}";
  box.appendChild(style);
  box.appendChild(spinner);
  var label = document.createElement("div");
  label.style.cssText = "color:var(--text-primary,#fff);font-size:15px;font-weight:600;margin-bottom:6px;";
  label.textContent = "任务执行中";
  box.appendChild(label);
  var sub = document.createElement("div");
  sub.style.cssText = "color:var(--text-muted,#aaa);font-size:13px;";
  sub.textContent = "请耐心等待，不要关闭或刷新页面";
  box.appendChild(sub);
  overlay.appendChild(box);
  document.body.appendChild(overlay);
  _remoteToolOverlay = overlay;
  if (!_beforeUnloadHandler) {
    _beforeUnloadHandler = function (e) { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", _beforeUnloadHandler);
  }
}

function _hideRemoteToolOverlay() {
  _doHideOverlay();
}

function _doHideOverlay() {
  if (_remoteToolOverlay) {
    _remoteToolOverlay.remove();
    _remoteToolOverlay = null;
  }
  if (_beforeUnloadHandler) {
    window.removeEventListener("beforeunload", _beforeUnloadHandler);
    _beforeUnloadHandler = null;
  }
}

function _forceHideOverlay() {
  _doHideOverlay();
}

var _pendingReconnect = [];
var _pendingReconnectTimer = null;
var MAX_PENDING_RECONNECT = 50;
var _lastTasksSnapshot = [];
var _skillCatalogRefreshTimer = null;
var REMOTE_STOP_NOTICE_WINDOW_ID = "remote-stop-notice-window";

function _escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

function _showRemoteStopNotice() {
  var wid = REMOTE_STOP_NOTICE_WINDOW_ID;
  var html = '<div style="padding:4px 0 12px;line-height:1.55;color:var(--text-muted,#ccc);font-size:13px">';
  html += "本对话流已停止。若当时仍有远端任务在运行，请自行在目标环境检查或停止。";
  html += '</div><div style="text-align:right"><button type="button" class="cog-btn wm-remote-stop-ok">知道了</button></div>';
  function bindOk(body) {
    var btn = body.querySelector(".wm-remote-stop-ok");
    if (btn) btn.onclick = function () { closeWindow(wid); };
  }
  var existing = getWindow(wid);
  if (existing) {
    var body = existing.el.querySelector(".wm-body");
    if (body) {
      body.innerHTML = html;
      bindOk(body);
    }
    focusWindow(wid);
    return;
  }
  openWindow(wid, {
    title: "已停止",
    width: 440,
    height: 240,
    minWidth: 320,
    minHeight: 180,
    onMount: function (body) {
      body.innerHTML = html;
      bindOk(body);
    }
  });
}

function _scheduleSkillCatalogRefresh() {
  if (!state.api || typeof state.api.fetchSkills !== "function") return;
  if (_skillCatalogRefreshTimer) {
    clearTimeout(_skillCatalogRefreshTimer);
  }
  _skillCatalogRefreshTimer = setTimeout(function () {
    _skillCatalogRefreshTimer = null;
    state.api.fetchSkills();
  }, 150);
}

export function replayPendingReconnect() {
  if (_pendingReconnectTimer) {
    clearTimeout(_pendingReconnectTimer);
    _pendingReconnectTimer = null;
  }
  var msgs = _pendingReconnect.splice(0);
  msgs.forEach(function (m) { handleMessage(m); });
}

function _addPendingReconnect(msg) {
  if (_pendingReconnect.length >= MAX_PENDING_RECONNECT) return;
  _pendingReconnect.push(msg);
  if (_pendingReconnectTimer) clearTimeout(_pendingReconnectTimer);
  _pendingReconnectTimer = setTimeout(function () {
    _pendingReconnect = [];
    _pendingReconnectTimer = null;
  }, 30000);
}

function _replayPendingReconnectForTask(taskId) {
  if (!taskId || !_pendingReconnect.length) return;
  if (_pendingReconnectTimer && _pendingReconnect.length === 0) {
    clearTimeout(_pendingReconnectTimer);
    _pendingReconnectTimer = null;
    return;
  }
  var matched = [];
  var remain = [];
  for (var i = 0; i < _pendingReconnect.length; i++) {
    var item = _pendingReconnect[i];
    var itemTaskId = item && item.data && item.data.taskId;
    if (itemTaskId && itemTaskId === taskId) matched.push(item);
    else remain.push(item);
  }
  if (!matched.length) return;
  _pendingReconnect = remain;
  if (_pendingReconnectTimer && _pendingReconnect.length === 0) {
    clearTimeout(_pendingReconnectTimer);
    _pendingReconnectTimer = null;
  }
  matched.forEach(function (m) { handleMessage(m); });
}

var RETAINED_STREAM_WINDOW_MS = 5000;

function taskIdFromMsg(msg) {
  return (msg.data && msg.data.taskId) || msg.taskId || "";
}

function acceptSeq(msg) {
  var seq = parseInt(msg.seq, 10) || 0;
  if (seq <= 0) return true;
  var taskId = taskIdFromMsg(msg);
  if (!taskId) return true;
  var prev = taskLastSeq[taskId] || 0;
  if (seq <= prev) return false;
  taskLastSeq[taskId] = seq;
  return true;
}

function resolveConv(taskId) {
  const convId = taskToConv[taskId];
  if (!convId) return null;
  return getConvState(convId);
}

function isActive(taskId) {
  return taskToConv[taskId] === state.currentConversationId;
}

function _isInflightStatus(status) {
  var s = String(status || "");
  return s === "queued" || s === "pending" || s === "running";
}

function applyTaskSnapshot(task, skipUi) {
  if (!task || !task.taskId || !task.threadId) return;
  taskMirror[task.taskId] = task;
  taskToConv[task.taskId] = task.threadId;
  var snapSeq = parseInt(task.snapshotSeq || task.lastSeq, 10) || 0;
  if (snapSeq > 0) {
    taskLastSeq[task.taskId] = Math.max(taskLastSeq[task.taskId] || 0, snapSeq);
  }
  if (skipUi) return;
  var cs = getConvState(task.threadId);
  var status = String(task.status || "");
  if (_isInflightStatus(status)) {
    if (!cs.currentTaskId || status === "running") cs.currentTaskId = task.taskId;
  }
}

function reconcileTasksSnapshot(tasks, skipUi) {
  tasks = tasks || [];
  _lastTasksSnapshot = tasks.slice();
  var inflightIds = {};
  var inflightByThread = {};

  for (var i = 0; i < tasks.length; i++) {
    var task = tasks[i];
    if (!task || !task.taskId || !task.threadId) continue;
    if (_isInflightStatus(task.status)) {
      inflightIds[task.taskId] = true;
      inflightByThread[task.threadId] = true;
    }
  }

  for (var tid in taskMirror) {
    if (!Object.prototype.hasOwnProperty.call(taskMirror, tid)) continue;
    if (inflightIds[tid]) continue;
    delete taskMirror[tid];
    delete taskToConv[tid];
  }

  for (var j = 0; j < tasks.length; j++) {
    applyTaskSnapshot(tasks[j], skipUi);
  }

  if (!skipUi) {
    for (var convId in convCache) {
      if (!Object.prototype.hasOwnProperty.call(convCache, convId)) continue;
      var cs = convCache[convId];
      syncComposerForConv(cs, "reconcile_tasks_snapshot");
    }
    syncSendButton();
  }
}

function finishBootstrap() {
  state.bootstrapPhase = "ready";
  state.bootstrapping = false;
  reconcileTasksSnapshot(_lastTasksSnapshot, false);
  applyPendingHistoryForAllConvs().then(function () {
    replayPendingReconnect();
    resumeAllInflightTasks();
    flushOutbox();
    syncSendButton();
  });
}

function handlePong(msg) {
  if (!msg || msg.type !== "pong") return;
  if (state.pendingPingMessageId && msg.messageId && msg.messageId !== state.pendingPingMessageId) return;
  state.lastPongAt = Date.now();
  if (msg.data && msg.data.connectionId && !state.connectionId) {
    state.connectionId = msg.data.connectionId;
  }
}

function _clearRetainedStream(cs, taskId) {
  if (!cs) return;
  if (!taskId || cs.retainedTaskId === taskId) {
    if (cs.retainedTaskId) delete taskToConv[cs.retainedTaskId];
    cs.retainedStreamBubble = null;
    cs.retainedTaskId = null;
    cs.retainedUntil = 0;
    return;
  }
  if (cs.retainedTaskId && cs.retainedTaskId !== taskId && Date.now() >= (cs.retainedUntil || 0)) {
    delete taskToConv[cs.retainedTaskId];
    cs.retainedStreamBubble = null;
    cs.retainedTaskId = null;
    cs.retainedUntil = 0;
  }
}

function _applyStreamTaskError(cs, taskId, errText) {
  var text = errText != null ? String(errText) : "";
  if (taskId && taskMirror[taskId]) taskMirror[taskId].status = "failed";
  var b = upsertBlock(cs, { blockId: "error:" + taskId, kind: "text", text: text });
  if (b && b.bubbleEl) b.bubbleEl.classList.add("error");
  _cleanupStream(cs, taskId);
}

function _cleanupStream(cs, taskId, opts) {
  _hideRemoteToolOverlay();
  removeTurnWaiting(cs);
  var wasActive = isActive(taskId);
  var interrupted = !!(opts && opts.interrupted);
  if (taskId && taskMirror[taskId]) {
    if (interrupted) {
      taskMirror[taskId].status = "cancelled";
    } else if (taskMirror[taskId].status !== "failed" && taskMirror[taskId].status !== "cancelled") {
      taskMirror[taskId].status = "completed";
    }
  }
  _clearRetainedStream(cs);
  cs.retainedStreamBubble = cs.currentStreamBubble;
  cs.retainedTaskId = taskId;
  cs.retainedUntil = Date.now() + RETAINED_STREAM_WINDOW_MS;
  cs.currentStreamBubble = null;
  if (taskId) {
    delete taskMirror[taskId];
    delete taskToConv[taskId];
  }
  cs.pendingSend = false;
  cs.outboxFlushing = false;
  syncComposerForConv(cs, "cleanup_stream");
  syncSendButton();
  cs.editingUserMessageIndex = null;
  cs.thinkStartTime = null;
  cs.thinkEndTime = null;
  if (wasActive && !cs.isStreaming) setSendBtnSend();
}

export function handleMessage(msg) {
  if (msg.type === "online_count") {
    return;
  }

  if (msg.type === "pong") {
    handlePong(msg);
    return;
  }

  if (msg.type === "tasks_snapshot") {
    var snapTasks = (msg.data && msg.data.tasks) || [];
    reconcileTasksSnapshot(snapTasks, state.bootstrapPhase !== "ready");
  }

  var phase = state.bootstrapPhase;

  if (phase === "auth_pending" && msg.type !== "auth_response") {
    _addPendingReconnect(msg);
    return;
  }
  if (phase === "session_pending") {
    if (msg.type === "history_snapshot") {
      state.pendingHistorySnapshot = msg;
      return;
    }
    if (msg.type !== "auth_response") {
      _addPendingReconnect(msg);
      return;
    }
  }
  if (phase === "history_pending" && msg.type !== "history_snapshot") {
    _addPendingReconnect(msg);
    return;
  }

  var targetThread = msg.data && msg.data.threadId;
  if (targetThread && msg.type !== "auth_response" && msg.type !== "history_snapshot") {
    var csCheck = getConvState(targetThread);
    if (csCheck && csCheck.historyLoading) {
      _addPendingReconnect(msg);
      return;
    }
  }

  if (!messageBus.hasListeners(msg.type)) {
    return;
  }
  messageBus.emit(msg.type, msg);
}

messageBus.on("tasks_snapshot", function () {
  if (state.bootstrapPhase === "ready") {
    resumeAllInflightTasks();
  }
  syncSendButton();
});

messageBus.on("message_accepted", function (msg) {
  if (!msg.data) return;
  resolvePendingAck(msg.data.messageId || "");
  applyTaskSnapshot(msg.data);
  // 受理即进入「运行中」:发送键马上切成停止键,不用等第一个 stream_start
  // (多角色群聊里第一个 token 可能好几秒后才来)
  var threadId = msg.data.threadId || "";
  var cs = threadId ? getConvState(threadId) : activeConv();
  if (cs) {
    cs.outboxFlushing = false;
    clearThreadSendFailures(cs);
    syncComposerForConv(cs, "message_accepted");
    showTurnWaiting(cs, "message_accepted");
  }
  if (!threadId || threadId === state.currentConversationId) syncSendButton();
});

messageBus.on("message_rejected", function (msg) {
  if (!msg.data) return;
  var messageId = msg.data.messageId || "";
  var threadId = msg.data.threadId || "";
  var reason = msg.data.reason || "rejected";
  if (reason === "task_active" && findChatInflightTask(threadId) && pendingAckRetryCount(messageId) > 0) {
    discardPendingAck(messageId);
    return;
  }
  rejectPendingAck(messageId, reason);
});

messageBus.on("deepseek_key_saved", function (msg) {
  var threadId = (msg.data && msg.data.threadId) || "";
  var cs = threadId ? getConvState(threadId) : activeConv();
  var area = getChatAreaEl();
  if (area) {
    var rows = area.querySelectorAll(".msg-row.user");
    if (rows.length) {
      var last = rows[rows.length - 1];
      last.parentNode.removeChild(last);
      var known = parseInt(area.dataset.userCount, 10) || 0;
      if (known > 0) area.dataset.userCount = String(known - 1);
    }
  }
  if (cs) {
    cs.pendingSend = false;
    cs.outboxFlushing = false;
    syncComposerForConv(cs, "deepseek_key_saved");
  }
  syncSendButton();
});

messageBus.on("history_snapshot", function (msg) {
  var snapEvents = (msg.data && (msg.data.events || msg.data.messages)) || [];
  var snapThreadId = (msg.data && msg.data.threadId) || state.pendingBootstrapThreadId || state.currentConversationId;
  var snapCs = snapThreadId ? getConvState(snapThreadId) : activeConv();
  if (snapCs) {
    if (msg.data && msg.data.checkpoints) {
      snapCs.checkpointState = {
        cursor: String(msg.data.checkpoints.cursor || ""),
        hasLatest: !!msg.data.checkpoints.hasLatest,
      };
    }
    if (msg.data && "planDoc" in msg.data) {
      // 检查点恢复会把计划槽一起还回去,面板要跟着换
      snapCs.planDoc = msg.data.planDoc || null;
      if (snapThreadId === state.currentConversationId) applyPlanDoc(msg.data.planDoc || null);
    }
    if (!snapCs.isStreaming) {
      commitHistoryRender(snapThreadId, snapCs, snapEvents, "history_snapshot");
    } else {
      snapCs.pendingHistoryEvents = snapEvents;
    }
    snapCs.historyLoading = false;
  }
  if (state.bootstrapPhase === "history_pending") {
    finishBootstrap();
  }
});

messageBus.on("auth_response", function (msg) {
  if (msg.data && msg.data.connectionId) {
    state.connectionId = msg.data.connectionId;
  }
  state.bootstrapPhase = "session_pending";
  var authData = msg.data || {};
  var authConversations = authData.conversations || [];
  var bootstrapThreadId = authData.effectiveThreadId || authData.currentId || authData.threadId || state.currentConversationId || "";

  function afterHistoryBootstrap() {
    if (state.bootstrapPhase === "history_pending") {
      finishBootstrap();
    }
  }

  if (state.httpInitDone && state.currentConversationId && bootstrapThreadId === state.currentConversationId) {
    if (authConversations.length && state.currentUserId) {
      saveConversationList(state.currentUserId, authConversations);
      if (authData.folders) saveFolderList(state.currentUserId, authData.folders);
      saveCurrentConvId(state.currentUserId, bootstrapThreadId);
      renderConversationList(authConversations, bootstrapThreadId, authData.folders);
    }
    state.pendingBootstrapThreadId = bootstrapThreadId;
    var pendingSnapshot = state.pendingHistorySnapshot;
    state.pendingHistorySnapshot = null;
    if (pendingSnapshot) {
      state.bootstrapPhase = "history_pending";
      handleMessage(pendingSnapshot);
    } else {
      state.bootstrapPhase = "history_pending";
      reloadThreadHistory(bootstrapThreadId, "websocket_reconnect").finally(afterHistoryBootstrap);
    }
    return;
  }

  if (state.currentUserId) {
    saveConversationList(state.currentUserId, authConversations);
    if (authData.folders) saveFolderList(state.currentUserId, authData.folders);
    saveCurrentConvId(state.currentUserId, bootstrapThreadId);
  }
  initFirstConversation(bootstrapThreadId, { skipAssetConnect: state.httpInitDone && state.currentConversationId === bootstrapThreadId });
  renderConversationList(authConversations, bootstrapThreadId, authData.folders);
  state.pendingBootstrapThreadId = bootstrapThreadId;
  state.bootstrapPhase = "history_pending";

  var authSnapshot = state.pendingHistorySnapshot;
  state.pendingHistorySnapshot = null;
  if (authSnapshot) {
    handleMessage(authSnapshot);
  } else {
    loadHistory(bootstrapThreadId).finally(afterHistoryBootstrap);
  }
});

messageBus.on("checkpoint_created", function (msg) {
  // 直播中的用户气泡在发出时还没有检查点;服务端落盘后广播回来,补到最后一条用户气泡上
  var d = msg.data || {};
  var cs = d.threadId ? getConvState(d.threadId) : null;
  if (!cs || !cs.el || !d.checkpointId) return;
  var rows = cs.el.querySelectorAll(".msg-row.user");
  if (!rows.length) return;
  setUserBubbleCheckpoint(rows[rows.length - 1], d.checkpointId);
});

messageBus.on("title_updated", function (msg) {
  if (msg.data && msg.data.threadId && msg.data.title) {
    updateConversationTitle(msg.data.threadId, msg.data.title);
    if (state.currentConversationId === msg.data.threadId || !state.currentConversationId) {
      state.currentConversationTitle = msg.data.title;
    }
  }
});

messageBus.on("thread_switched", function (msg) {
  var switchedThreadId = msg.data && msg.data.threadId;
  if (switchedThreadId && switchedThreadId === state.currentConversationId) {
    syncAssetsThread(switchedThreadId);
  }
});

function _onWorkspaceNode(msg) {
  if (msg.data && msg.data.node) upsertNode(msg.data.node);
  handleAssetEvent(msg);
}
messageBus.on("workspace_node_created", _onWorkspaceNode);
messageBus.on("workspace_node_updated", _onWorkspaceNode);
messageBus.on("workspace_node_deleted", _onWorkspaceNode);

messageBus.on("context_cleared", function (msg) {
  var clearedThreadId = (msg.data && msg.data.threadId) || "";
  var cs = clearedThreadId ? getConvState(clearedThreadId) : activeConv();
  if (cs) addSystemMsg("上下文已清除", cs.el);
});

messageBus.on("skill_catalog_changed", function () {
  _scheduleSkillCatalogRefresh();
});

messageBus.on("container_status_changed", function () {
});

function _mapAtts(atts) {
  if (!atts || !atts.length) return null;
  return atts.map(function (a) {
    return {
      name: a.filename || "",
      mime: a.mime_type || "",
      media_id: a.media_id || "",
      previewUrl: resolveFileUrl(a.media_id || a),
    };
  });
}

function _resolveBlockConv(msg) {
  var d = msg.data || {};
  var taskId = d.taskId || msg.taskId || "";
  var threadId = d.threadId || msg.threadId || d.thread_id || "";
  var blockId = String(d.blockId || "");
  var cs = taskId ? resolveConv(taskId) : null;
  if (!cs && threadId) {
    cs = convCache[threadId] || null;
  }
  if (!cs) return null;
  var hasBlock = blockId ? hasRenderedBlock(cs, blockId) : false;
  if (taskId && cs.currentTaskId && taskId !== cs.currentTaskId) {
    if (!hasBlock) {
      return null;
    }
  }
  if (taskId && !acceptSeq(msg)) return null;
  return cs;
}

messageBus.on("turn_resume", function (msg) {
  if (!acceptSeq(msg)) return;
  var d = msg.data || {};
  var thread = d.threadId || "";
  var task = d.taskId || taskIdFromMsg(msg);
  if (!thread || !task) return;
  var taskKind = d.taskKind || "chat";
  var cs = getConvState(thread);
  var snapSeq = parseInt(d.snapshotSeq, 10) || 0;
  if (snapSeq > 0) taskLastSeq[task] = snapSeq;
  taskToConv[task] = thread;
  taskMirror[task] = Object.assign({}, taskMirror[task] || {}, {
    taskId: task,
    threadId: thread,
    status: d.status || "running",
    taskKind: taskKind,
  });
  cs.currentTaskId = task;
  cs.renderSource = "ws";
  cs.liveTaskId = task;
  rebindTaskTimeline(cs);
  if (d.reconnect && d.content && cs.el && !cs.el.querySelector(".msg-row.user")) {
    addUserBubble(d.content, _mapAtts(d.attachments), cs.el);
  }
  if (cs.historyLoaded) {
    if (Array.isArray(d.blocks) && d.blocks.length) {
      applyReconnectBlocksSnapshot(cs, d.blocks);
    }
  } else {
    if (d.snapshotReasoning) {
      upsertBlock(cs, {
        blockId: "thinking:resume:" + task,
        kind: "thinking",
        text: String(d.snapshotReasoning),
        status: "open",
      });
    }
    if (d.snapshotContent) {
      upsertBlock(cs, {
        blockId: "text:resume:" + task,
        kind: "text",
        text: String(d.snapshotContent),
        status: "open",
      });
    }
    if (Array.isArray(d.blocks) && d.blocks.length) {
      applyBlocksSnapshot(cs, d.blocks);
      restorePlanReviewFromBlocks(cs, d.blocks);
    }
  }
  clearThreadSendFailures(cs);
  showTurnWaiting(cs, "turn_resume");
  syncComposerForConv(cs, "turn_resume");
  if (thread === state.currentConversationId) syncSendButton();
});

messageBus.on("stream_start", function (msg) {
  if (!acceptSeq(msg)) return;
  var d = msg.data || {};
  var convId = d.threadId || "";
  if (!convId) return;
  var cs = getConvState(convId);
  cs.renderSource = "ws";
  cs.liveTaskId = d.taskId;
  cs.currentTaskId = d.taskId;
  taskToConv[d.taskId] = convId;
  taskMirror[d.taskId] = Object.assign({}, taskMirror[d.taskId] || {}, {
    taskId: d.taskId,
    threadId: convId,
    status: "running",
    content: d.content || "",
    taskKind: d.taskKind || "chat",
  });
  if (d.reconnect) {
    if (!cs.historyLoaded && d.content) {
      var hasUserMsg = cs.el && cs.el.querySelector(".msg-row.user");
      var hasRecall = cs.blocks && cs.currentTaskId && cs.blocks["recall:" + d.taskId];
      if (!hasUserMsg && !hasRecall) {
        if (d.isAgentRecall) {
          renderAgentRecallTrigger(cs, d.content, d.recallSource || "", d.taskId || "");
        } else {
          addUserBubble(d.content, _mapAtts(d.attachments), cs.el);
        }
      }
    }
    if (!cs.historyLoaded) {
      blocksResetTurn(cs);
    } else {
      rebindTaskTimeline(cs);
    }
    if (Array.isArray(d.blocks) && d.blocks.length) {
      if (cs.historyLoaded) {
        applyReconnectBlocksSnapshot(cs, d.blocks);
      } else {
        applyBlocksSnapshot(cs, d.blocks);
      }
      restorePlanReviewFromBlocks(cs, d.blocks);
    }
  } else {
    cs.blkTimelineEl = null;
    cs.blkTimelineRow = null;
    if (cs.pendingSend) {
      cs.pendingSend = false;
    } else if (cs.editingUserMessageIndex) {
      cs.editingUserMessageIndex = null;
    } else if (d.content) {
      if (d.isAgentRecall) {
        renderAgentRecallTrigger(cs, d.content, d.recallSource || "", d.taskId || "");
      } else {
        addUserBubble(d.content, _mapAtts(d.attachments), cs.el);
      }
    }
    if (!cs.historyLoaded) {
      clearStreamTimeline(cs);
    }
  }
  var snapSeq = parseInt(d.snapshotSeq, 10) || 0;
  if (snapSeq > 0) taskLastSeq[d.taskId] = snapSeq;
  _replayPendingReconnectForTask(d.taskId);
  clearThreadSendFailures(cs);
  showTurnWaiting(cs, "stream_start");
  syncComposerForConv(cs, "stream_start");
  if (convId === state.currentConversationId) syncSendButton();
});

messageBus.on("turn_start", function (msg) {
  var d = msg.data || {};
  var cs = d.threadId ? getConvState(d.threadId) : null;
  if (!cs || !d.taskId) return;
  cs.currentTaskId = d.taskId;
  taskToConv[d.taskId] = d.threadId;
  cs.renderSource = "ws";
  cs.liveTaskId = d.taskId;
  rebindTaskTimeline(cs);
  showTurnWaiting(cs, "turn_start");
  syncComposerForConv(cs, "turn_start");
  if (d.threadId === state.currentConversationId) syncSendButton();
});

messageBus.on("agent_recall", function (msg) {
  var d = msg.data || {};
  var convId = d.threadId || msg.threadId || "";
  var taskId = d.taskId || msg.taskId || "";
  if (!convId || !d.content) return;
  var cs = getConvState(convId);
  if (!taskId) return;
  taskToConv[taskId] = convId;
  taskMirror[taskId] = Object.assign({}, taskMirror[taskId] || {}, {
    taskId: taskId,
    threadId: convId,
    status: "running",
    content: d.content || "",
    taskKind: "chat",
  });
  cs.renderSource = "ws";
  cs.liveTaskId = taskId;
  cs.currentTaskId = taskId;
  cs.blkTimelineEl = null;
  cs.blkTimelineRow = null;
  if (!cs.historyLoaded) {
    clearStreamTimeline(cs);
  }
  renderAgentRecallTrigger(cs, d.content, d.source || "", taskId);
  requestTaskResume(taskId, taskLastSeq[taskId] || 0);
  showTurnWaiting(cs, "agent_recall");
  syncComposerForConv(cs, "agent_recall");
  if (convId === state.currentConversationId) syncSendButton();
});

messageBus.on("block_open", function (msg) {
  var cs = _resolveBlockConv(msg);
  if (!cs) {
    var taskId = (msg.data && msg.data.taskId) || msg.taskId || "";
    if (taskId) {
      var fallback = resolveConv(taskId);
      if (fallback && taskId === fallback.currentTaskId && acceptSeq(msg)) {
        blockOpen(fallback, msg.data || {});
        return;
      }
    }
    _addPendingReconnect(msg);
    return;
  }
  blockOpen(cs, msg.data || {});
});

messageBus.on("block_delta", function (msg) {
  var cs = _resolveBlockConv(msg);
  if (!cs) {
    var taskId = (msg.data && msg.data.taskId) || msg.taskId || "";
    if (taskId) {
      var fallback = resolveConv(taskId);
      if (fallback && taskId === fallback.currentTaskId && acceptSeq(msg)) {
        blockDelta(fallback, msg.data || {});
        return;
      }
    }
    _addPendingReconnect(msg);
    return;
  }
  blockDelta(cs, msg.data || {});
});

messageBus.on("block_end", function (msg) {
  var cs = _resolveBlockConv(msg);
  if (!cs) {
    _addPendingReconnect(msg);
    return;
  }
  blockEnd(cs, msg.data || {});
  var endData = msg.data || {};
  if (String(endData.name || "") === "todo_write" && endData.result) {
    var parsedTodos = endData.result;
    if (typeof parsedTodos === "string") {
      parsedTodos = JSON.parse(parsedTodos);
    }
    if (parsedTodos && parsedTodos.todos) {
      if (cs.planDoc) cs.planDoc.todos = parsedTodos.todos;
      if (cs.id === state.currentConversationId) applyTodoList(parsedTodos.todos);
    }
  }
});

messageBus.on("block_patch", function (msg) {
  var d = msg.data || {};
  if (d.media) return;
  var cs = _resolveBlockConv(msg);
  if (!cs) {
    _addPendingReconnect(msg);
    return;
  }
  blockPatch(cs, d);
  if (d.planReview && d.planReview.plan) {
    if (cs.id === state.currentConversationId) {
      showPlanReviewForCall(d.planReview.plan, d.blockId, d.planReview.todos, d.planReview.planId);
    } else {
      cs.planDoc = {
        plan: d.planReview.plan,
        todos: d.planReview.todos || [],
        phase: "review",
        planId: d.planReview.planId || "",
        reviewBlockId: d.blockId || "",
      };
    }
  }
});

messageBus.on("block_remove", function (msg) {
  var cs = _resolveBlockConv(msg);
  if (!cs) return;
  blockRemove(cs, msg.data || {});
});

function _onTurnComplete(msg) {
  const taskId = msg.data && msg.data.taskId;
  if (!taskId) return;
  const cs = resolveConv(taskId);
  var endThreadId = (msg.data && msg.data.threadId) || taskToConv[taskId] || "";
  if (!cs) {
    _addPendingReconnect(msg);
    return;
  }
  if (taskId !== cs.currentTaskId) {
    delete taskMirror[taskId];
    delete taskToConv[taskId];
    syncComposerForConv(cs, "turn_complete_other");
    syncSendButton();
    return;
  }
  if (!acceptSeq(msg)) return;
  var hadRemoteOverlay = !!_remoteToolOverlay;
  _forceHideOverlay();
  removeTurnWaiting(cs);
  if (endThreadId) syncAssetsThread(endThreadId);
  _cleanupStream(cs, taskId, { interrupted: !!(msg.data && msg.data.interrupted) });
  if (endThreadId) {
    cs.liveTaskId = "";
    if (!cs.historyLoaded) cs.historyLoaded = true;
  }
  if (msg.data && msg.data.interrupted && hadRemoteOverlay) {
    _showRemoteStopNotice();
  }
}
messageBus.on("plan_status", function (msg) {
  applyPlanStatus(msg.data || {});
});

messageBus.on("turn_complete", _onTurnComplete);
messageBus.on("stream_end", _onTurnComplete);

messageBus.on("stream_error", function (msg) {
  const taskId = msg.data && msg.data.taskId;
  if (!taskId) return;
  const cs = resolveConv(taskId);
  if (!cs || taskId !== cs.currentTaskId) {
    _addPendingReconnect(msg);
    return;
  }
  _applyStreamTaskError(cs, taskId, msg.data.errorMessage || "未知错误");
});

messageBus.on("error", function (msg) {
  const taskId = msg.data && msg.data.taskId;
  if (!taskId) return;
  const cs = resolveConv(taskId);
  if (!cs || taskId !== cs.currentTaskId) {
    _addPendingReconnect(msg);
    return;
  }
  _applyStreamTaskError(cs, taskId, msg.data.errorMessage || msg.data.detail || "未知错误");
});

messageBus.on("program_timer", function (msg) {
  var d = msg.data || {};
  var threadId = d.threadId || msg.threadId || "";
  if (!threadId) return;
  var cs = getConvState(threadId);
  if (!cs || !cs.el) return;
  if (d.taskId) {
    cs.currentTaskId = d.taskId;
    taskToConv[d.taskId] = threadId;
  }
  upsertBlock(cs, {
    blockId: "ptimer:" + (d.id || d.taskId || Date.now()),
    kind: "program_timer",
    name: "程序定时",
    text: d.content || "",
    status: "done",
    completedAt: d.createdAt || null,
  });
});

messageBus.on("recall_insert", function (msg) {
  var d = msg.data || {};
  var cs = getConvState(d.threadId || state.currentConversationId);
  if (!cs || !cs.el) return;
  // 仅当前查看的对话才插入,其他对话等切过去时从 JSONL 恢复
  var bid = "recall:" + (d.completedAt || Date.now());
  upsertBlock(cs, {
    blockId: bid,
    kind: "recall",
    name: "Agent 召回",
    text: d.content || "",
    status: "done",
    completedAt: d.completedAt || null,
  });
});

messageBus.on("assistant_insert", function (msg) {
  var d = msg.data || {};
  var cs = getConvState(d.threadId || state.currentConversationId);
  if (!cs || !cs.el) return;
  var bid = "text:bg_" + Date.now();
  upsertBlock(cs, {
    blockId: bid,
    kind: "text",
    text: d.content || "",
    status: "done",
  });
});

export { messageBus };
