import { API_BASE } from "./config.js";
import { resolveFileUrl } from "./asset-url.js";
import { logAssetUrl } from "./asset-diag.js";
import { naFetch } from "./http.js";
import { state, activeConv, getConvState, taskToConv, taskMirror, convCache } from "./state.js";
import { applyHttpInflightBootstrap, findChatInflightTask } from "./composer.js";
import {
  addSystemMsg,
  addUserBubble,
  enableAutoFollow,
  syncCurrentPageThinkToggle,
  syncSendButton,
} from "./chat-ui.js";
import {
  blocksResetTurn,
  upsertBlock,
  renderAgentRecallTrigger,
  applyBlocksSnapshot,
} from "./blocks-renderer.js";
import { replayPendingReconnect } from "./message-handler.js";
import { applyPlanDoc, restorePlanReviewFromBlocks } from "./plan-review.js";
import { applySessionUi, persistSessionUiNow } from "./session-ui.js";
import { addCheckpointEntry, applyCheckpointCursor } from "./checkpoints.js";

function _checkpointStateFrom(res) {
  var raw = (res && res.checkpoints) || {};
  return { cursor: String(raw.cursor || ""), hasLatest: !!raw.hasLatest };
}

var _historyRenderSeqByThread = {};
var _historyRequestSeqByThread = {};
var HISTORY_CHUNK_TURNS = 5;
var HISTORY_SENTINEL_MARGIN_PX = 200;
var _historyWindowObserver = null;

function _isHistoryTurnStart(ev) {
  var t = String((ev && (ev.type || ev.role)) || "");
  return t === "user" || t === "agent_recall";
}

function buildHistoryWindow(events) {
  var turns = [];
  var userOrdinals = new Array(events.length);
  var userCount = 0;
  for (var i = 0; i < events.length; i++) {
    var ev = events[i];
    if (i === 0 || _isHistoryTurnStart(ev)) turns.push(i);
    if (String((ev && (ev.type || ev.role)) || "") === "user") {
      userCount += 1;
      userOrdinals[i] = userCount;
    }
  }
  return {
    events: events,
    turns: turns,
    userOrdinals: userOrdinals,
    userCount: userCount,
    renderedTurn: turns.length,
    sentinel: null,
    busy: false,
  };
}

function _historyScrollEl() {
  var scroll = document.getElementById("chatScroll");
  if (!scroll) throw new Error("chatScroll missing");
  return scroll;
}

function _historySentinelObserver() {
  if (_historyWindowObserver) return _historyWindowObserver;
  var scroll = _historyScrollEl();
  scroll.style.overflowAnchor = "none";
  _historyWindowObserver = new IntersectionObserver(function (entries) {
    for (var i = 0; i < entries.length; i++) {
      if (!entries[i].isIntersecting) continue;
      var cid = String(entries[i].target.dataset.threadId || "");
      var cs = convCache[cid];
      if (cs) _fillHistoryViewport(cs);
    }
  }, { root: scroll, rootMargin: HISTORY_SENTINEL_MARGIN_PX + "px 0px 0px 0px" });
  return _historyWindowObserver;
}

function _ensureHistorySentinel(cs) {
  var win = cs.historyWindow;
  if (!win || win.renderedTurn <= 0) return;
  if (win.sentinel && win.sentinel.parentNode === cs.el) return;
  var el = document.createElement("div");
  el.className = "history-more-sentinel";
  el.dataset.threadId = String(cs.id || "");
  el.textContent = "\u6b63\u5728\u52a0\u8f7d\u66f4\u65e9\u7684\u8bb0\u5f55";
  el.style.cssText = "text-align:center;font-size:12px;color:#999;padding:8px 0;";
  cs.el.insertBefore(el, cs.el.firstChild);
  win.sentinel = el;
  _historySentinelObserver().observe(el);
}

function _removeHistorySentinel(cs) {
  var win = cs && cs.historyWindow;
  if (!win || !win.sentinel) return;
  if (_historyWindowObserver) _historyWindowObserver.unobserve(win.sentinel);
  if (win.sentinel.parentNode) win.sentinel.parentNode.removeChild(win.sentinel);
  win.sentinel = null;
}

function _historySentinelInViewport(cs) {
  var win = cs.historyWindow;
  if (!win || !win.sentinel || !win.sentinel.isConnected) return false;
  var sr = _historyScrollEl().getBoundingClientRect();
  var r = win.sentinel.getBoundingClientRect();
  return r.bottom >= sr.top - HISTORY_SENTINEL_MARGIN_PX && r.top <= sr.bottom;
}

function _renderOlderHistoryChunk(cs) {
  var win = cs.historyWindow;
  if (!win || win.busy || win.renderedTurn <= 0) return;
  if (!cs.el || !cs.el.isConnected) return;
  win.busy = true;
  var scroll = _historyScrollEl();
  var prevHeight = scroll.scrollHeight;
  var prevTop = scroll.scrollTop;
  var fromTurn = Math.max(0, win.renderedTurn - HISTORY_CHUNK_TURNS);
  var startIdx = win.turns[fromTurn];
  var endIdx = win.turns[win.renderedTurn];
  var wrap = document.createElement("div");
  wrap.className = "history-chunk";
  var anchor = win.sentinel && win.sentinel.parentNode === cs.el ? win.sentinel.nextSibling : cs.el.firstChild;
  cs.el.insertBefore(wrap, anchor);
  var st = createHistoryRenderState({ id: cs.id, el: wrap, blocks: {}, currentTaskId: "" });
  st.userOrdinals = win.userOrdinals;
  for (var i = startIdx; i < endIdx; i++) {
    _renderHistoryEventGuarded(st, win.events[i], i, wrap);
  }
  while (wrap.firstChild) cs.el.insertBefore(wrap.firstChild, wrap);
  cs.el.removeChild(wrap);
  win.renderedTurn = fromTurn;
  syncCurrentPageThinkToggle(cs.el);
  applyCheckpointCursor(cs);
  scroll.scrollTop = prevTop + (scroll.scrollHeight - prevHeight);
  if (win.renderedTurn <= 0) _removeHistorySentinel(cs);
  win.busy = false;
}

export function fillHistoryViewport(cs) {
  if (!cs) return;
  _fillHistoryViewport(cs);
}

function _fillHistoryViewport(cs) {
  var win = cs.historyWindow;
  if (!win || win.busy || win.renderedTurn <= 0) return;
  if (!_historySentinelInViewport(cs)) return;
  _renderOlderHistoryChunk(cs);
  if (win.renderedTurn > 0) {
    requestAnimationFrame(function () { _fillHistoryViewport(cs); });
  }
}

export function filterEventsForInflightRender(events, taskId) {
  var tid = String(taskId || "");
  if (!tid) return events || [];
  return (events || []).filter(function (ev) {
    var evTask = String(ev.taskId || "");
    if (evTask !== tid) return true;
    var etype = String(ev.type || ev.role || "");
    if (etype === "user" || etype === "reasoning" || etype === "tool") return true;
    if (etype === "assistant") return !ev.streaming;
    return true;
  });
}

export function resolveLiveChatTaskId(cs) {
  if (!cs) return "";
  var fromMirror = findChatInflightTask(cs.id);
  if (fromMirror) return fromMirror;
  if (!cs.isStreaming) return "";
  return String(cs.currentTaskId || cs.liveTaskId || "");
}

export function commitHistoryRender(threadId, cs, events, reason) {
  if (!cs) return Promise.resolve();
  events = events || cs.pendingHistoryEvents || [];
  var liveTaskId = resolveLiveChatTaskId(cs);
  if (liveTaskId) {
    cs.renderSource = "ws";
    cs.liveTaskId = liveTaskId;
    events = filterEventsForInflightRender(events, liveTaskId);
  } else {
    cs.renderSource = "events";
    cs.liveTaskId = "";
  }
  cs.pendingHistoryEvents = null;
  return applyHistoryEvents(threadId, events, cs, reason);
}

export function applyPendingHistoryForAllConvs() {
  var promises = [];
  for (var cid in convCache) {
    if (!Object.prototype.hasOwnProperty.call(convCache, cid)) continue;
    var cs = convCache[cid];
    if (!cs || !cs.pendingHistoryEvents || cs.historyLoaded) continue;
    promises.push(commitHistoryRender(cid, cs, cs.pendingHistoryEvents, "bootstrap_pending"));
  }
  if (!promises.length) return Promise.resolve();
  return Promise.all(promises);
}

function applyInflightBlocks(cs, taskId, blocks) {
  if (!blocks || !blocks.length) return;
  if (taskId) {
    cs.currentTaskId = taskId;
    cs.liveTaskId = taskId;
    taskToConv[taskId] = cs.id;
    historyEnsureTurn(cs, taskId);
  }
  applyBlocksSnapshot(cs, blocks);
  restorePlanReviewFromBlocks(cs, blocks);
}

export function restoreInflightSnapshot(threadId, cs, res) {
  var inflight = res && Array.isArray(res.inflight) && res.inflight[0] ? res.inflight[0] : null;
  if (!inflight || !inflight.taskId) return Promise.resolve();
  var taskId = String(inflight.taskId);
  var url =
    API_BASE +
    "/api/v1/checkpoints?threadId=" +
    encodeURIComponent(threadId) +
    "&taskId=" +
    encodeURIComponent(taskId) +
    "&from_seq=0";
  return naFetch(url)
    .then(function (r) {
      return r.json();
    })
    .then(function (payload) {
      var chunks = Array.isArray(payload.checkpoints) ? payload.checkpoints : [];
      var latest = chunks.length ? chunks[chunks.length - 1] : null;
      if (!latest) {
        applyInflightBlocks(cs, taskId, inflight.blocks);
        return;
      }
      applyInflightBlocks(cs, taskId, latest.blocks);
    });
}

export function applyHistoryEvents(threadId, events, cs, reason) {
  if (!cs) return Promise.resolve();
  events = events || [];
  blocksResetTurn(cs);
  _removeHistorySentinel(cs);
  cs.historyWindow = null;
  cs.el.innerHTML = "";
  cs.el.dataset.userCount = "0";
  syncCurrentPageThinkToggle(cs.el);
  if (events.length > 0) {
      return renderHistoryEventsAsync(events, cs).then(function (completed) {
        if (!completed) return;
        cs.historyLoaded = true;
        enableAutoFollow();
      });
    }
  cs.historyLoaded = true;
  enableAutoFollow();
  return Promise.resolve();
}

function convertHistoryAttachments(atts) {
  if (!atts || !atts.length) return null;
  return atts.map(function (a, idx) {
    var mediaId = a.media_id || "";
    var mime = a.mime_type || a.mime || "";
    var attObj = {
      name: a.filename || a.name || "",
      filename: a.filename || a.name || "",
      mime: mime,
      mime_type: mime,
      media_id: mediaId,
      signed_url: a.signed_url || "",
      thumb_url: a.thumb_url || "",
    };
    var previewUrl = resolveFileUrl(attObj);
    logAssetUrl("history_attach_convert", {
      mediaId: mediaId,
      mime: mime,
      filename: attObj.filename,
      srcKind: attObj.signed_url || attObj.thumb_url ? "signed" : previewUrl.indexOf("/media/") >= 0 ? "media_proxy" : previewUrl ? "other" : "empty",
      srcLen: previewUrl.length,
      hasDataUrl: false,
      extra: "idx=" + (idx + 1) + " path=" + (a.path || mediaId || ""),
    });
    return {
      name: attObj.name,
      mime: mime,
      media_id: mediaId,
      signed_url: attObj.signed_url,
      thumb_url: attObj.thumb_url,
      previewUrl: previewUrl,
    };
  });
}

function parseToolArgs(raw) {
  if (raw == null) return {};
  if (typeof raw === "object") return raw;
  try {
    return JSON.parse(String(raw));
  } catch (e) {
    return {};
  }
}

function rememberToolCalls(toolCalls, inputMap) {
  for (var i = 0; i < toolCalls.length; i++) {
    var tc = toolCalls[i] || {};
    var tcId = String(tc.id || "");
    if (!tcId) continue;
    var fn = tc.function || {};
    inputMap[tcId] = {
      name: fn.name || tc.name || "tool",
      input: parseToolArgs(fn.arguments),
    };
  }
}

function lookupToolCall(inputMap, toolId) {
  var key = String(toolId || "");
  if (!key || !inputMap[key]) return null;
  return inputMap[key];
}

function createHistoryRenderState(cs) {
  return {
    cs: cs,
    toolCallInputs: {},
    absIndex: 0,
    userOrdinals: null,
  };
}

function historyTaskId(ev) {
  return String((ev && ev.taskId) || "");
}

function historyThinkingBlockId(ev, st) {
  var evId = ev && ev.id != null && String(ev.id) !== "" ? String(ev.id) : "";
  if (evId) return "thinking:" + evId;
  var taskId = historyTaskId(ev) || "orphan";
  return "thinking:" + taskId + ":" + st.absIndex;
}

function historyEmbeddedThinkingBlockId(ev, st) {
  var evId = ev && ev.id != null && String(ev.id) !== "" ? String(ev.id) : "";
  if (evId) return "thinking:embedded:" + evId;
  var taskId = historyTaskId(ev) || "orphan";
  return "thinking:embedded:" + taskId + ":" + st.absIndex;
}

function upsertHistoryThinking(cs, blockId, text) {
  upsertBlock(cs, {
    blockId: blockId,
    kind: "thinking",
    text: text || "",
    status: "done",
  });
}

function resetHistoryTurn(st) {
  blocksResetTurn(st.cs);
  st.toolCallInputs = {};
  st.cs.currentTaskId = "";
}

function _toolStatusFromContent(content) {
  var text = String(content || "");
  if (text.length > 65536) return "done";
  if (text.trim().toLowerCase().indexOf('"error"') >= 0) {
    try {
      var parsed = JSON.parse(text);
      if (parsed && (parsed.ok === false || parsed.error)) return "error";
    } catch (e) {}
  }
  return "done";
}

function _mediaBlockFromHistory(media, toolId) {
  if (!media) return null;
  var mediaTaskId = String(
    media.mediaTaskId || media.media_task_id || media.bgTaskId || media.bg_task_id || toolId || ""
  );
  return {
    content: media.content || media.mediaContent || "",
    textContent: media.textContent || media.text_content || "",
    skill: media.toolName || media.skill_name || "",
    bgTaskId: mediaTaskId,
    mediaTaskId: mediaTaskId,
    urls: Array.isArray(media.urls) ? media.urls : [],
    mediaType: media.mediaType || media.media_type || media.type || "",
  };
}

function historyEnsureTurn(cs, taskId) {
  if (taskId) cs.currentTaskId = taskId;
}

function _renderOneHistoryEvent(st, ev, area) {
  var etype = ev.type || ev.role || "";
  var cs = st.cs;

  if (etype === "user") {
    resetHistoryTurn(st);
    var ordinal = st.userOrdinals ? st.userOrdinals[st.absIndex] : 0;
    addUserBubble(ev.content || "", convertHistoryAttachments(ev.attachments), area, ordinal, {
      checkpointId: ev.checkpointId || "",
    });
    return;
  }

  if (etype === "agent_recall") {
    resetHistoryTurn(st);
    historyEnsureTurn(cs, ev.taskId || "");
    renderAgentRecallTrigger(cs, ev.content || "", ev.source || "", ev.taskId || "");
    return;
  }

  if (etype === "reasoning") {
    var rTask = historyTaskId(ev);
    historyEnsureTurn(cs, rTask);
    upsertHistoryThinking(cs, historyThinkingBlockId(ev, st), ev.content || "");
    return;
  }

  if (etype === "assistant") {
    var toolCalls = ev.toolCalls || [];
    var text = ev.content || "";
    var thinking = ev.thinking || "";
    if (!toolCalls.length && !text && !thinking) return;
    var aTask = historyTaskId(ev);
    historyEnsureTurn(cs, aTask);
    if (thinking) {
      upsertHistoryThinking(cs, historyEmbeddedThinkingBlockId(ev, st), thinking);
    }
    rememberToolCalls(toolCalls, st.toolCallInputs);
    for (var t = 0; t < toolCalls.length; t++) {
      var tc = toolCalls[t];
      var fn = tc.function || {};
      var tcInput = parseToolArgs(fn.arguments);
      var tcName = fn.name || tc.name || "tool";
      upsertBlock(cs, {
        blockId: tc.id,
        kind: "tool",
        name: tcName,
        input: tcInput,
        status: "open",
        mediaSkill: "",
      });
    }
    if (text) {
      upsertBlock(cs, {
        blockId: "text:" + (ev.id || aTask),
        kind: "text",
        // 多角色群聊:发言人名字显示在气泡上方
        name: ev.agentName || "",
        text: text,
        status: "done",
      });
    }
    return;
  }

  if (etype === "program_timer") {
    var pTask = historyTaskId(ev);
    historyEnsureTurn(cs, pTask);
    upsertBlock(cs, {
      blockId: "ptimer:" + (ev.id || pTask),
      kind: "program_timer",
      name: "程序定时",
      text: ev.content || "",
      status: "done",
      completedAt: ev.createdAt || null,
    });
    return;
  }

  if (etype === "recall") {
    var recallTask = historyTaskId(ev);
    historyEnsureTurn(cs, recallTask);
    upsertBlock(cs, {
      blockId: "recall:" + (ev.id || recallTask),
      kind: "recall",
      name: "Agent 召回",
      text: ev.content || "",
      status: "done",
      completedAt: ev.createdAt || null,
    });
    return;
  }

  if (etype === "system") {
    // 交接提示 / 系统提示:留在当前回合的时间轴里,保持发言顺序
    var sysTask = historyTaskId(ev);
    historyEnsureTurn(cs, sysTask);
    upsertBlock(cs, {
      blockId: "notice:" + (ev.id || sysTask),
      kind: "notice",
      text: ev.content || "",
      status: "done",
    });
    return;
  }

  if (etype === "compaction") {
    // 前段全损压缩。摘要正文就存在事件的 content 字段里,原文已经不存在了。
    var cmpTask = historyTaskId(ev);
    historyEnsureTurn(cs, cmpTask);
    var covered = Number(ev.covered) || 0;
    var label = ev.level === "full"
      ? "\u4e0a\u4e0b\u6587\u538b\u7f29 \u00b7 \u5f3a\u529b\u538b\u7f29"
      : "\u4e0a\u4e0b\u6587\u538b\u7f29 \u00b7 \u5168\u635f\u6458\u8981";
    upsertBlock(cs, {
      blockId: "compaction:" + (ev.id || cmpTask),
      kind: "compaction",
      name: covered ? label + "\uff08\u539f " + covered + " \u6761\u8bb0\u5f55\uff09" : label,
      text: ev.content || "",
      status: "done",
    });
    if (ev.checkpointId) {
      // 被压掉那段里第一条消息的检查点:恢复到它 = 恢复到这份摘要覆盖的所有改动之前
      addCheckpointEntry(area, ev.checkpointId, "\u6062\u590d\u5230\u6b64\u6458\u8981\u8986\u76d6\u7684\u6539\u52a8\u4e4b\u524d");
    }
    return;
  }

  if (etype === "tool") {
    historyEnsureTurn(cs, historyTaskId(ev));
    var toolId = ev.toolCallId || "";
    if (!toolId) return;
    var media = ev.media;
    var remembered = lookupToolCall(st.toolCallInputs, toolId);
    var toolName = ev.name || (remembered && remembered.name) || "tool";
    var input = remembered ? remembered.input : {};
    if (media && (media.skill_name || media.params)) {
      input = {
        skill_name: media.skill_name || media.toolName || input.skill_name || "",
        params: media.params || input.params || {},
      };
    }
    var blk = {
      blockId: toolId,
      kind: "tool",
      name: toolName,
      input: input,
      status: _toolStatusFromContent(ev.content),
      durationMs: ev.durationMs != null ? ev.durationMs : null,
      completedAt: ev.completedAt != null ? ev.completedAt : null,
    };
    if (toolName === "clarify") {
      var clarifyRaw = String(ev.content || "");
      var clarifyAnswer = clarifyRaw;
      var clarifyErrored = false;
      if (clarifyRaw.trim().indexOf("{") === 0) {
        try {
          var clarifyParsed = JSON.parse(clarifyRaw);
          if (clarifyParsed && typeof clarifyParsed === "object") {
            if (clarifyParsed.ok === false || clarifyParsed.error) {
              clarifyErrored = true;
              clarifyAnswer = "";
            } else if (typeof clarifyParsed.answer === "string") {
              clarifyAnswer = clarifyParsed.answer;
            }
          }
        } catch (e) {}
      }
      blk.clarify = {
        question: input.question || "",
        choices: Array.isArray(input.choices) ? input.choices : [],
        answer: clarifyAnswer,
      };
      blk.status = clarifyErrored ? "error" : "done";
    } else if (media) {
      blk.media = _mediaBlockFromHistory(media, toolId);
    } else {
      blk.result = ev.content || "";
    }
    upsertBlock(cs, blk);
  }
}

function _finalizeHistoryRender(st, area) {
  syncCurrentPageThinkToggle(area);
  applyCheckpointCursor(st.cs);
  enableAutoFollow();
}

function _renderHistoryFailure(st, ev, index, err) {
  // 一条事件渲染失败,不能连累后面几百条,但也绝不能装作没发生 —— 就地摆一张红字卡,
  // 写清楚是第几条、什么事件、什么错,剩下的照常渲染。
  var id = (ev && ev.id) || "";
  var etype = (ev && (ev.type || ev.role)) || "?";
  upsertBlock(st.cs, {
    blockId: "renderfail:" + index,
    kind: "render_error",
    text: "第 " + (index + 1) + " 条历史渲染失败（id=" + id + " type=" + etype + "）："
      + ((err && err.message) || String(err)),
    status: "done",
  });
}

function _renderHistoryEventGuarded(st, ev, absIndex, area) {
  st.absIndex = absIndex;
  try {
    _renderOneHistoryEvent(st, ev, area);
  } catch (err) {
    try {
      _renderHistoryFailure(st, ev, absIndex, err);
    } catch (cardErr) {
      console.error("history render failure card failed", cardErr, err);
    }
  }
}

function renderHistoryEventsAsync(events, cs) {
  if (!cs) throw new Error("renderHistoryEventsAsync requires conversation state");
  var renderKey = String(cs.id || "");
  var seq = (_historyRenderSeqByThread[renderKey] || 0) + 1;
  _historyRenderSeqByThread[renderKey] = seq;
  var area = cs.el;
  _removeHistorySentinel(cs);
  area.innerHTML = "";
  area.dataset.userCount = "0";
  blocksResetTurn(cs);
  events = events || [];
  var win = buildHistoryWindow(events);
  cs.historyWindow = win;
  var st = createHistoryRenderState(cs);
  st.userOrdinals = win.userOrdinals;
  var startTurn = Math.max(0, win.turns.length - HISTORY_CHUNK_TURNS);

  return new Promise(function (resolve) {
    var index = win.turns.length ? win.turns[startTurn] : 0;
    function step() {
      if (seq !== _historyRenderSeqByThread[renderKey]) {
        resolve(false);
        return;
      }
      if (index >= events.length) {
        win.renderedTurn = startTurn;
        area.dataset.userCount = String(win.userCount);
        _finalizeHistoryRender(st, area);
        _ensureHistorySentinel(cs);
        _fillHistoryViewport(cs);
        resolve(true);
        return;
      }
      _renderHistoryEventGuarded(st, events[index], index, area);
      index += 1;
      setTimeout(step, 0);
    }
    step();
  });
}

export function reloadThreadHistory(threadId, reason) {
  var targetId = threadId || state.currentConversationId || "";
  if (!targetId) return Promise.resolve();
  var cs = getConvState(targetId);
  if (!cs) return Promise.resolve();
  var requestSeq = (_historyRequestSeqByThread[targetId] || 0) + 1;
  _historyRequestSeqByThread[targetId] = requestSeq;
  cs.historyLoading = true;
  var canPersistUi = targetId === state.currentConversationId && cs.sessionUi != null;
  var ready = canPersistUi ? persistSessionUiNow() : Promise.resolve();
  return ready.then(function () {
    return naFetch(API_BASE + "/api/v1/history?threadId=" + encodeURIComponent(targetId))
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (_historyRequestSeqByThread[targetId] !== requestSeq) return;
        applyHttpInflightBootstrap(targetId, cs, res);
        var events = Array.isArray(res.events) ? res.events : [];
        cs.renderSource = "events";
        cs.planDoc = res.planDoc || null;
        cs.sessionUi = res.sessionUi || {};
        cs.checkpointState = _checkpointStateFrom(res);
        if (targetId === state.currentConversationId) {
          applySessionUi(res.sessionUi || {});
          applyPlanDoc(res.planDoc || null);
        }
        return commitHistoryRender(targetId, cs, events, reason || "reload_thread_history").then(function () {
          return restoreInflightSnapshot(targetId, cs, res);
        }).then(function () {
          if (state.bootstrapPhase !== "history_pending") {
            replayPendingReconnect();
          }
          return res;
        });
      });
  }).finally(function () {
    if (_historyRequestSeqByThread[targetId] === requestSeq) {
      cs.historyLoading = false;
    }
  });
}

export function loadHistory(threadId) {
  var targetId = threadId || state.currentConversationId || "";
  var cs = targetId ? getConvState(targetId) : activeConv();
  if (!cs) return Promise.resolve();
  if (cs.historyLoaded) return Promise.resolve();
  var requestSeq = (_historyRequestSeqByThread[targetId] || 0) + 1;
  _historyRequestSeqByThread[targetId] = requestSeq;
  cs.historyLoading = true;
  return naFetch(API_BASE + "/api/v1/history?threadId=" + encodeURIComponent(targetId))
    .then(function (r) { return r.json(); })
    .then(function (res) {
      if (_historyRequestSeqByThread[targetId] !== requestSeq) return;
      applyHttpInflightBootstrap(targetId, cs, res);
      if (targetId === state.currentConversationId) {
        syncSendButton();
      }
      var events = Array.isArray(res.events) ? res.events : [];
      cs.pendingHistoryEvents = events;
      cs.historyLoading = false;
      cs.planDoc = res.planDoc || null;
      cs.sessionUi = res.sessionUi || {};
      cs.checkpointState = _checkpointStateFrom(res);
      if (targetId === state.currentConversationId) {
        applySessionUi(res.sessionUi || {});
        applyPlanDoc(res.planDoc || null);
      }
      if (state.bootstrapPhase === "history_pending") {
        return;
      }
      return commitHistoryRender(targetId, cs, events, "load_history").then(function () {
        return restoreInflightSnapshot(targetId, cs, res);
      }).then(function () {
        replayPendingReconnect();
      });
    })
    .catch(function (e) {
      console.error("[history] 加载历史失败:", e);
      cs.historyLoaded = false;
      cs.historyLoading = false;
      cs.pendingHistoryEvents = null;
      replayPendingReconnect();
      throw e;
    })
    .finally(function () {
      if (_historyRequestSeqByThread[targetId] === requestSeq) {
        cs.historyLoading = false;
      }
    });
}
