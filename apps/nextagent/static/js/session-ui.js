import { state, getConvState } from "./state.js";
import { API_BASE } from "./config.js";
import { naFetch } from "./http.js";

var _hooks = null;
var _timer = 0;
var _applying = false;

export function bindSessionUi(hooks) {
  if (!hooks || typeof hooks.collect !== "function" || typeof hooks.apply !== "function") {
    throw new Error("session ui hooks required");
  }
  _hooks = hooks;
}

export function applySessionUi(ui) {
  if (!_hooks) throw new Error("session ui not bound");
  var data = ui && typeof ui === "object" ? ui : {};
  _applying = true;
  try {
    _hooks.apply(data);
    var tid = state.currentConversationId;
    if (tid) {
      var cs = getConvState(tid);
      if (cs) cs.sessionUi = data;
    }
  } finally {
    _applying = false;
  }
}

export function persistSessionUi() {
  if (_applying) return;
  if (_timer) clearTimeout(_timer);
  _timer = setTimeout(function () {
    persistSessionUiNow();
  }, 400);
}

export function persistSessionUiNow() {
  if (_timer) {
    clearTimeout(_timer);
    _timer = 0;
  }
  if (_applying) return Promise.resolve();
  var tid = state.currentConversationId;
  if (!tid) return Promise.resolve();
  if (!_hooks) throw new Error("session ui not bound");
  var ui = _hooks.collect();
  var cs = getConvState(tid);
  if (cs) cs.sessionUi = ui;
  return naFetch(API_BASE + "/api/v1/session-ui", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ threadId: tid, sessionUi: ui }),
  }).then(function (r) {
    if (!r.ok) throw new Error("session-ui persist failed: " + r.status);
    return r.json();
  });
}
