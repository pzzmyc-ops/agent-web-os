const _stateData = {
  ws: null,
  reconnectDelay: 5000,
  heartbeatTimer: null,
  heartbeatCount: 0,
  bootstrapping: true,
  searchEnabled: false,
  mediaPassthroughEnabled: false,
  agentSpeak: true,
  routingModelsById: {},
  pendingReviewTaskId: null,
  selectedSkill: null,
  availableSkills: [],
  selectedAgentProfile: "",
  availableAgentProfiles: [],
  api: null,
  currentConversationId: null,
  currentConversationTitle: null,
  currentUser: null,
  currentUserId: null,
  httpInitDone: false,
  bootstrapPhase: "auth_pending",
  pendingHistorySnapshot: null,
  pendingBootstrapThreadId: null,
  connectionId: "",
  lastPongAt: 0,
  lastPingAt: 0,
  pendingPingMessageId: "",
  pageVisible: true
};

const _stateSubs = {};
const _stateAnySubs = [];
let _stateDebug = false;

export function setStateDebug(on) {
  _stateDebug = !!on;
}

function _notify(key, value, old) {
  if (_stateDebug) {
    console.log("[state] " + key + " =", value, "(was", old, ")");
  }
  const subs = _stateSubs[key];
  if (subs) {
    const snap = subs.slice();
    for (let i = 0; i < snap.length; i++) {
      try {
        snap[i](value, old, key);
      } catch (e) {
        console.error("[state] subscriber error for key=" + key, e);
      }
    }
  }
  if (_stateAnySubs.length) {
    const asnap = _stateAnySubs.slice();
    for (let i = 0; i < asnap.length; i++) {
      try {
        asnap[i](key, value, old);
      } catch (e) {
        console.error("[state] any-subscriber error", e);
      }
    }
  }
}

export const state = new Proxy(_stateData, {
  set(target, key, value) {
    const old = target[key];
    target[key] = value;
    if (old !== value) _notify(key, value, old);
    return true;
  }
});

export function setState(key, value) {
  state[key] = value;
}

export function subscribeState(key, fn) {
  if (!_stateSubs[key]) _stateSubs[key] = [];
  _stateSubs[key].push(fn);
  return function unsubscribe() {
    const arr = _stateSubs[key];
    if (!arr) return;
    const i = arr.indexOf(fn);
    if (i >= 0) arr.splice(i, 1);
  };
}

export function subscribeStateAny(fn) {
  _stateAnySubs.push(fn);
  return function unsubscribe() {
    const i = _stateAnySubs.indexOf(fn);
    if (i >= 0) _stateAnySubs.splice(i, 1);
  };
}

export const convCache = {};
export const taskToConv = {};
export const taskMirror = {};
export const taskLastSeq = {};

export function initConv(convId, el) {
  if (convCache[convId]) {
    convCache[convId].id = convId;
    return convCache[convId];
  }
  if (!el) {
    el = document.createElement("div");
    el.className = "chat-area";
  }
  el.dataset.threadId = String(convId || "");
  convCache[convId] = {
    id: convId,
    el: el,
    checkpointState: { cursor: "", hasLatest: false },
    composerDraft: { text: "", attachments: [] },
    isStreaming: false,
    currentStreamBubble: null,
    currentTaskId: null,
    retainedStreamBubble: null,
    retainedTaskId: null,
    retainedUntil: 0,
    thinkStartTime: null,
    thinkEndTime: null,
    editingUserMessageIndex: null,
    historyLoaded: false,
    historyLoading: false,
    pendingHistoryEvents: null,
    pendingSend: false,
    outboxFlushing: false,
    renderSource: "events",
    liveTaskId: "",
    pendingMessages: [],
    taskBubbles: {},
    planDoc: null,
    sessionUi: null
  };
  return convCache[convId];
}

export function getConvState(convId) {
  var cs = convCache[convId] || initConv(convId);
  if (convId) cs.id = convId;
  return cs;
}

export function activeConv() {
  if (!state.currentConversationId) return null;
  return getConvState(state.currentConversationId);
}

export function deleteConv(convId) {
  if (convCache[convId]) {
    delete convCache[convId];
  }
  for (var tid in taskToConv) {
    if (taskToConv[tid] === convId) {
      delete taskToConv[tid];
    }
  }
}
