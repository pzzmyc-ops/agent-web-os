import { state, activeConv, getConvState, taskToConv, convCache, taskMirror, taskLastSeq } from "./state.js";
import { syncComposerForConv, findChatInflightTask, isInflightStatus } from "./composer.js";
import { sendWsMsg, resumeAllInflightTasks, requestTaskResume, resolvePendingAck, rejectPendingAck, discardPendingAck, pendingAckRetryCount, flushOutbox, rootMessageId } from "./ws.js";
import { resolveFileUrl } from "./asset-url.js";
import { messageBus } from "./message-bus.js";
import { applySessionUi } from "./session-ui.js";
import { applyQueueState } from "./queue-panel.js";
import {
  addSystemMsg,
  addUserBubble,
  syncSendButton,
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
  applyPendingHistoryForAllConvs,
  commitHistoryRender,
} from "./history.js";
import { renderConversationList, updateConversationTitle, initFirstConversation } from "./conversations.js";
import { saveConversationList, saveFolderList, saveCurrentConvId } from "./cache.js";
import { syncAssetsThread, handleAssetEvent } from "./assets.js";
import { upsertNode } from "./asset-catalog.js";
import { setUserBubbleCheckpoint } from "./checkpoints.js";

var _pendingReconnect = [];
var _pendingReconnectTimer = null;
var MAX_PENDING_RECONNECT = 50;
var _lastTasksSnapshot = [];
var _skillCatalogRefreshTimer = null;

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

var SYNC_PROBE_INTERVAL_MS = 3000;

/** 问后端要一份「谁在跑」的权威名单。 */
function requestTasksSnapshot() {
  sendWsMsg("sync_state", { threadId: state.currentConversationId || "" });
}

/** 前端只要还认为有回合在跑,就定期向后端要一次名单。
 *
 *  turn_complete 丢了、连接闪断、别的窗口发起的回合,都在下一次探测里自愈:
 *  后端说没有了,reconcileTasksSnapshot 就把按钮和等待提示收回去,探测随之停下。
 */
function startSyncProbe() {
  setInterval(function () {
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;
    for (var tid in taskMirror) {
      if (!Object.prototype.hasOwnProperty.call(taskMirror, tid)) continue;
      var row = taskMirror[tid];
      if (row && isInflightStatus(row.status)) {
        requestTasksSnapshot();
        return;
      }
    }
  }, SYNC_PROBE_INTERVAL_MS);
}

startSyncProbe();

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

function applyTaskSnapshot(task) {
  if (!task || !task.taskId || !task.threadId) return;
  taskMirror[task.taskId] = task;
  taskToConv[task.taskId] = task.threadId;
  var snapSeq = parseInt(task.snapshotSeq || task.lastSeq, 10) || 0;
  if (snapSeq > 0) {
    taskLastSeq[task.taskId] = Math.max(taskLastSeq[task.taskId] || 0, snapSeq);
  }
  if (!isInflightStatus(task.status)) return;
  var cs = getConvState(task.threadId);
  cs.currentTaskId = task.taskId;
  cs.liveTaskId = task.taskId;
}

/** 一条回合结束了:把这一轮的临时显示态收干净。
 *
 *  唯一的收尾入口 —— turn_complete 触发和后端名单对账都走它,重复调用无副作用。
 *  它不碰 taskMirror:那张表是后端名单的镜像,由 reconcileTasksSnapshot 管。
 */
function finishThreadTurn(cs) {
  if (!cs) return;
  removeTurnWaiting(cs);
  cs.currentStreamBubble = null;
  cs.pendingSend = false;
  cs.outboxFlushing = false;
  cs.editingUserMessageIndex = null;
  cs.thinkStartTime = null;
  cs.thinkEndTime = null;
  cs.liveTaskId = "";
  syncComposerForConv(cs, "turn_finished");
}

/** 后端名单即权威:名单里没有的镜像行 = 这条回合已经结束。
 *
 *  发送键、等待提示、流式指针全从这里收敛,前端不再自己判断回合结束 ——
 *  所以丢一帧 turn_complete 不会卡住,下一次推送或探测就把状态纠回来。
 */
function reconcileTasksSnapshot(tasks) {
  tasks = tasks || [];
  _lastTasksSnapshot = tasks.slice();

  var live = {};
  var busyConv = {};
  for (var i = 0; i < tasks.length; i++) {
    var task = tasks[i];
    if (!task || !task.taskId || !task.threadId) continue;
    if (!isInflightStatus(task.status)) continue;
    live[task.taskId] = task;
    busyConv[task.threadId] = true;
  }

  for (var tid in taskMirror) {
    if (!Object.prototype.hasOwnProperty.call(taskMirror, tid)) continue;
    if (live[tid]) continue;
    var convId = taskToConv[tid];
    delete taskMirror[tid];
    delete taskToConv[tid];
    if (convId && !busyConv[convId]) finishThreadTurn(convCache[convId]);
  }

  for (var j = 0; j < tasks.length; j++) {
    applyTaskSnapshot(tasks[j]);
  }

  for (var convKey in convCache) {
    if (!Object.prototype.hasOwnProperty.call(convCache, convKey)) continue;
    syncComposerForConv(convCache[convKey], "reconcile_tasks_snapshot");
  }
  syncSendButton();
}

function finishBootstrap() {
  state.bootstrapPhase = "ready";
  state.bootstrapping = false;
  reconcileTasksSnapshot(_lastTasksSnapshot);
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

export function handleMessage(msg) {
  if (msg.type === "online_count") {
    return;
  }

  if (msg.type === "pong") {
    handlePong(msg);
    return;
  }

  if (msg.type === "tasks_snapshot") {
    reconcileTasksSnapshot((msg.data && msg.data.tasks) || []);
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

function _takeSentBubble(cs, messageId) {
  if (!cs || !cs.sentBubbles) return null;
  var key = rootMessageId(messageId);
  var row = cs.sentBubbles[key] || null;
  if (row) delete cs.sentBubbles[key];
  return row;
}

function _dropBubbleRow(cs, row) {
  if (!row || !row.parentNode) return;
  var area = row.parentNode;
  area.removeChild(row);
  var known = parseInt(area.dataset.userCount, 10) || 0;
  if (known > 0) area.dataset.userCount = String(known - 1);
  if (cs && cs.pendingSend) {
    cs.pendingSend = false;
    removeTurnWaiting(cs);
  }
}

messageBus.on("message_queued", function (msg) {
  var data = (msg && msg.data) || {};
  var threadId = data.threadId || "";
  resolvePendingAck(data.messageId || "");
  var cs = threadId ? getConvState(threadId) : activeConv();
  if (!cs) return;
  _dropBubbleRow(cs, _takeSentBubble(cs, data.messageId));
  cs.pendingSend = false;
  cs.outboxFlushing = false;
  syncComposerForConv(cs, "message_queued");
  if (!threadId || threadId === state.currentConversationId) syncSendButton();
});

messageBus.on("queue_state", function (msg) {
  var data = (msg && msg.data) || {};
  var threadId = data.threadId || "";
  if (!threadId) return;
  applyQueueState(threadId, data.items || []);
  if (threadId === state.currentConversationId) syncSendButton();
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
    var acceptedRow = _takeSentBubble(cs, msg.data.messageId);
    if (acceptedRow && msg.data.taskId) {
      if (!cs.acceptedBubbleTasks) cs.acceptedBubbleTasks = {};
      cs.acceptedBubbleTasks[String(msg.data.taskId)] = true;
    }
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
      snapCs.planDoc = msg.data.planDoc || null;
      if (snapThreadId === state.currentConversationId) applyPlanDoc(msg.data.planDoc || null);
    }
    if (msg.data && msg.data.sessionUi && typeof msg.data.sessionUi === "object") {
      if (snapThreadId === state.currentConversationId) applySessionUi(msg.data.sessionUi);
      else {
        snapCs.sessionUi = msg.data.sessionUi;
        snapCs.sessionUiReady = true;
      }
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

messageBus.on("session_ui", function (msg) {
  var data = msg.data || {};
  var threadId = data.threadId || "";
  var ui = data.sessionUi;
  if (!threadId || !ui || typeof ui !== "object") return;
  var cs = getConvState(threadId);
  if (threadId === state.currentConversationId) {
    var input = document.getElementById("msgInput");
    applySessionUi(ui, { keepComposer: !!(input && document.activeElement === input) });
    return;
  }
  cs.sessionUi = ui;
  cs.sessionUiReady = true;
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
    var taskKey = String(d.taskId || "");
    var bubbleShown = !!(cs.acceptedBubbleTasks && cs.acceptedBubbleTasks[taskKey]);
    if (bubbleShown) delete cs.acceptedBubbleTasks[taskKey];
    cs.pendingSend = false;
    if (bubbleShown) {
      cs.editingUserMessageIndex = null;
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

/** 回合结束的快捷通知。
 *
 *  这里不改「在不在跑」——那是 tasks_snapshot 的事。它只做两件事:立刻去问一次
 *  权威名单(不用等下一次探测),以及把收尾时该刷的显示顺手刷一下。
 */
function _onTurnComplete(msg) {
  // 先对账再刷新显示:状态收敛不能依赖下面这些副作用是否抛错。
  requestTasksSnapshot();
  var d = msg.data || {};
  var threadId = d.threadId || taskToConv[d.taskId] || "";
  if (!threadId) return;
  syncAssetsThread(threadId);
  var cs = getConvState(threadId);
  if (!cs.historyLoaded) cs.historyLoaded = true;
}

messageBus.on("plan_status", function (msg) {
  applyPlanStatus(msg.data || {});
});

messageBus.on("turn_complete", _onTurnComplete);

/** 出错只画一条错误气泡。
 *
 *  状态收尾统一交给 tasks_snapshot:出错那一轮结束时后端会收回名单,快照回来
 *  自然把按钮和等待提示复位。在这里再动一次状态就又成了第二本账。
 */
function _showStreamError(msg) {
  var d = msg.data || {};
  var threadId = d.threadId || taskToConv[d.taskId] || "";
  if (!threadId) return;
  var cs = getConvState(threadId);
  var b = upsertBlock(cs, {
    blockId: "error:" + (d.taskId || "unknown"),
    kind: "text",
    text: String(d.errorMessage || d.detail || "未知错误"),
  });
  if (b && b.bubbleEl) b.bubbleEl.classList.add("error");
}

messageBus.on("stream_error", _showStreamError);
messageBus.on("error", _showStreamError);

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
