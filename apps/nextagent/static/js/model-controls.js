import { state } from "./state.js";
import { API_BASE } from "./config.js";
import { naFetch } from "./http.js";
import { closeModelDropdown, closeToolDropdown } from "./input.js";
import { persistSessionUi } from "./session-ui.js";

var catalogState = {
  byId: {},
  ids: [],
  effortValue: "medium",
  effortOptions: [],
};
var _lockThinking = null;
var _lockEffort = null;

function getThinkingBtn() {
  return document.getElementById("thinkingBtn");
}

function getEffortBtn() {
  return document.getElementById("effortBtn");
}

function getEffortBadge() {
  return document.getElementById("effortBadge");
}

function isThinkingOn() {
  var btn = getThinkingBtn();
  return !!(btn && btn.classList.contains("active"));
}

export function closeEffortDropdown() {
  var dropdown = document.getElementById("effortDropdown");
  if (dropdown) dropdown.remove();
}

function syncEffortBadge() {
  var badge = getEffortBadge();
  if (badge) badge.textContent = catalogState.effortValue;
}

function openEffortDropdown() {
  closeEffortDropdown();
  closeModelDropdown();
  closeToolDropdown();
  var btn = getEffortBtn();
  if (!btn || btn.disabled) return;
  var rect = btn.getBoundingClientRect();
  var dropdown = document.createElement("div");
  dropdown.id = "effortDropdown";
  dropdown.className = "tool-dropdown";
  var opts = catalogState.effortOptions.length
    ? catalogState.effortOptions
    : [
        { value: "low", label: "low" },
        { value: "medium", label: "medium" },
        { value: "high", label: "high" },
      ];
  for (var i = 0; i < opts.length; i++) {
    (function (opt) {
      var item = document.createElement("div");
      item.className = "tool-dropdown-item";
      if (catalogState.effortValue === opt.value) item.classList.add("selected");
      item.textContent = opt.label || opt.value;
      item.onclick = function (e) {
        e.stopPropagation();
        catalogState.effortValue = opt.value;
        _lockEffort = opt.value;
        syncEffortBadge();
        closeEffortDropdown();
        persistSessionUi();
      };
      dropdown.appendChild(item);
    })(opts[i]);
  }
  document.body.appendChild(dropdown);
  dropdown.style.left = rect.left + "px";
  dropdown.style.top = (rect.top - dropdown.offsetHeight - 6) + "px";
}

function toggleEffortDropdown() {
  var existing = document.getElementById("effortDropdown");
  if (existing) {
    closeEffortDropdown();
  } else {
    openEffortDropdown();
  }
}

function applyModelReasoningChrome() {
  var thinkingBtn = getThinkingBtn();
  var effortBtn = getEffortBtn();
  var id = String(state.selectedAgentProfile || "").trim();
  var row = id ? catalogState.byId[id] : null;
  if (!row) {
    if (thinkingBtn) thinkingBtn.style.display = "none";
    if (effortBtn) effortBtn.style.display = "none";
    closeEffortDropdown();
    return;
  }
  var params = Array.isArray(row.ui && row.ui.params) ? row.ui.params : [];
  var thinkingDef = params.find(function (p) { return p && p.submit_key === "thinking"; });
  var showThinking = !!thinkingDef;
  if (thinkingBtn) thinkingBtn.style.display = showThinking ? "" : "none";
  if (effortBtn) effortBtn.style.display = showThinking ? "" : "none";
  if (!showThinking) {
    closeEffortDropdown();
    return;
  }
  var effortDef = params.find(function (p) { return p && p.submit_key === "reasoning_effort"; });
  var rawOpts = effortDef && Array.isArray(effortDef.options) ? effortDef.options : [];
  catalogState.effortOptions = rawOpts.length
    ? rawOpts
        .map(function (o) {
          return {
            value: String(o.value != null ? o.value : "").trim().toLowerCase(),
            label: String(o.label != null ? o.label : o.value != null ? o.value : "").trim()
              || String(o.value != null ? o.value : "").trim(),
          };
        })
        .filter(function (o) { return o.value; })
    : [
        { value: "low", label: "low" },
        { value: "medium", label: "medium" },
        { value: "high", label: "high" },
      ];
  var allowed = {};
  catalogState.effortOptions.forEach(function (o) { allowed[o.value] = true; });
  var defEff = "medium";
  if (effortDef && effortDef.default != null) {
    var d = String(effortDef.default).trim().toLowerCase();
    defEff = allowed[d] ? d : (catalogState.effortOptions[0] ? catalogState.effortOptions[0].value : "medium");
  }
  var cur = String(_lockEffort || catalogState.effortValue || "").trim().toLowerCase();
  if (!allowed[cur]) {
    catalogState.effortValue = allowed[defEff] ? defEff : (catalogState.effortOptions[0] ? catalogState.effortOptions[0].value : "medium");
  } else {
    catalogState.effortValue = cur;
  }
  var thinkingOn = _lockThinking !== null ? !!_lockThinking : thinkingDef.default !== false;
  if (thinkingBtn) thinkingBtn.classList.toggle("active", thinkingOn);
  if (effortBtn) effortBtn.disabled = !thinkingOn;
  syncEffortBadge();
}

export function updateModelBadge() {
  var badge = document.getElementById("modelBadge");
  if (!badge) return;
  var id = String(state.selectedAgentProfile || "").trim();
  var row = id ? catalogState.byId[id] : null;
  if (row) {
    badge.textContent = row.displayName || row.id;
    badge.style.display = "inline";
  } else if (id) {
    badge.textContent = id;
    badge.style.display = "inline";
  } else {
    badge.textContent = "";
    badge.style.display = "none";
  }
}

export function selectAgentProfile(profileId) {
  state.selectedAgentProfile = profileId;
  _lockThinking = isThinkingOn();
  _lockEffort = catalogState.effortValue;
  updateModelBadge();
  applyModelReasoningChrome();
  closeModelDropdown();
  closeEffortDropdown();
  persistSessionUi();
}

export function applySessionModelState(ui) {
  var profile = String((ui && ui.agentProfile) || "").trim();
  if (profile) state.selectedAgentProfile = profile;
  if (ui && typeof ui.thinking === "boolean") {
    _lockThinking = ui.thinking;
  } else {
    _lockThinking = null;
  }
  if (ui && ui.reasoningEffort) {
    _lockEffort = String(ui.reasoningEffort).trim().toLowerCase();
  } else {
    _lockEffort = null;
  }
  updateModelBadge();
  applyModelReasoningChrome();
}

export function snapshotSessionModel() {
  return {
    agentProfile: String(state.selectedAgentProfile || ""),
    thinking: isThinkingOn(),
    reasoningEffort: catalogState.effortValue,
  };
}

export function getModelDropdownProfiles() {
  return catalogState.ids.map(function (id) {
    var row = catalogState.byId[id];
    return {
      id: id,
      displayName: (row && (row.displayName || row.id)) || id,
    };
  });
}

export function getSendModelParams() {
  var model = String(state.selectedAgentProfile || "").trim();
  if (!model) return {};
  var out = { agentProfile: model };
  var thinkingBtn = getThinkingBtn();
  if (thinkingBtn && thinkingBtn.style.display !== "none") {
    out.thinking = isThinkingOn();
    if (out.thinking) {
      out.reasoning_effort = catalogState.effortValue;
    }
  }
  return out;
}

export async function fetchModelsCatalog() {
  var data = await naFetch(API_BASE + "/api/chat/models")
    .then(function (r) { return r.json(); });
  var rows = Array.isArray(data.models) ? data.models : [];
  catalogState.byId = {};
  catalogState.ids = [];
  rows.forEach(function (row) {
    var pid = String(row.id || "").trim();
    if (!pid) return;
    catalogState.byId[pid] = row;
    catalogState.ids.push(pid);
  });
  state.routingModelsById = catalogState.byId;
  state.availableAgentProfiles = catalogState.ids.map(function (id) {
    var row = catalogState.byId[id];
    return { id: id, displayName: (row && row.displayName) || id };
  });
  var hasSelected = catalogState.ids.indexOf(state.selectedAgentProfile) >= 0;
  if (!hasSelected) {
    if (catalogState.ids.length) {
      state.selectedAgentProfile = catalogState.ids[0];
    } else {
      state.selectedAgentProfile = "";
    }
  }
  updateModelBadge();
  applyModelReasoningChrome();
}

export function initModelControls() {
  var thinkingBtn = getThinkingBtn();
  var effortBtn = getEffortBtn();
  if (thinkingBtn) {
    thinkingBtn.onclick = function (e) {
      e.stopPropagation();
      thinkingBtn.classList.toggle("active");
      var on = isThinkingOn();
      _lockThinking = on;
      if (effortBtn) effortBtn.disabled = !on;
      if (!on) closeEffortDropdown();
      persistSessionUi();
    };
  }
  if (effortBtn) {
    effortBtn.onclick = function (e) {
      e.stopPropagation();
      if (effortBtn.disabled) return;
      toggleEffortDropdown();
    };
  }
  syncEffortBadge();
}
