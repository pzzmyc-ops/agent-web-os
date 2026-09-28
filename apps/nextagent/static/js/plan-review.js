import { formatContent } from "./format.js";
import { state, getConvState } from "./state.js";
import { API_BASE } from "./config.js";
import { naFetch } from "./http.js";
import { openWindow, closeWindow, getWindow, getWindowBody, updateWindowTitle, restoreWindow } from "./modules/window-manager/index.js";
import { persistSessionUi } from "./session-ui.js";
import { messageBus } from "./message-bus.js";

var WIN_ID = "plan-board";
var _planOn = false;
var _stashing = false;
var _reviewBlockId = "";
var _editingPlan = false;
var _planDraft = "";
var _focusTodoIndex = -1;
var _dirty = false;
var _doc = { plan: "", todos: [], phase: "", planId: "", userModified: false };

function planBtn() {
  return document.getElementById("planModeBtn");
}

function paintBtn() {
  var btn = planBtn();
  if (!btn) return;
  btn.classList.toggle("active", _planOn);
  btn.setAttribute("aria-pressed", _planOn ? "true" : "false");
  btn.title = _planOn ? "plan mode 已开启，按下关闭" : "开启计划模式";
  btn.disabled = false;
}

function statusLabel(st) {
  if (st === "completed") return "做完了";
  if (st === "in_progress") return "正在做";
  return "还没开始";
}

function cacheCurrentDoc() {
  var id = state.currentConversationId;
  if (!id) return;
  var cs = getConvState(id);
  if (!cs) return;
  if (!_doc.plan) {
    cs.planDoc = null;
    return;
  }
  cs.planDoc = {
    plan: _doc.plan,
    todos: _doc.todos,
    phase: _doc.phase,
    planId: _doc.planId || "",
    reviewBlockId: _reviewBlockId || "",
    userModified: !!_doc.userModified,
  };
}

function nextStatus(st) {
  if (st === "pending") return "in_progress";
  if (st === "in_progress") return "completed";
  return "pending";
}

function persistUserDoc() {
  if (!_doc.planId) throw new Error("plan persist missing planId");
  if (!state.currentConversationId) throw new Error("plan persist missing threadId");
  var threadId = state.currentConversationId;
  var text = String(_doc.plan || "").trim();
  if (!/^#\s+\S/.test(text)) throw new Error("plan must start with a # heading");
  return naFetch(API_BASE + "/api/v1/plan-doc", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      threadId: threadId,
      planId: _doc.planId,
      plan: text,
      todos: _doc.todos,
    }),
  }).then(function (r) {
    if (!r.ok) throw new Error("plan persist failed: " + r.status);
    return r.json();
  }).then(function (res) {
    if (state.currentConversationId !== threadId) return res;
    _doc.plan = text;
    _doc.userModified = true;
    _dirty = false;
    cacheCurrentDoc();
    return res;
  });
}

function commitPlanDraft() {
  if (!_editingPlan) return;
  var body = getWindowBody(WIN_ID);
  if (body) {
    var ta = body.querySelector(".plan-board-plan-editor");
    if (ta) _planDraft = ta.value;
  }
  var next = String(_planDraft || "");
  if (next === _doc.plan) return;
  _doc.plan = next;
  _dirty = true;
}

function uniqueTodoContent(base) {
  var name = base || "新待办";
  var used = {};
  var items = _doc.todos || [];
  for (var i = 0; i < items.length; i++) {
    used[String(items[i].content || "")] = true;
  }
  if (!used[name]) return name;
  var n = 2;
  while (used[name + " " + n]) n++;
  return name + " " + n;
}

function addTodo() {
  if (!_doc.planId) throw new Error("plan persist missing planId");
  if (!Array.isArray(_doc.todos)) _doc.todos = [];
  _doc.todos.push({ content: uniqueTodoContent("新待办"), status: "pending" });
  _focusTodoIndex = _doc.todos.length - 1;
  _dirty = true;
  persistIfDirty().then(function () { paintBody(); });
}

function resetEditState() {
  _editingPlan = false;
  _planDraft = "";
  _focusTodoIndex = -1;
  _dirty = false;
}

function persistIfDirty() {
  if (!_dirty) return Promise.resolve();
  return persistUserDoc();
}

function planIsFinished() {
  if (_doc.phase !== "active") return false;
  var items = _doc.todos || [];
  if (!items.length) return false;
  for (var i = 0; i < items.length; i++) {
    if (String(items[i].status || "") !== "completed") return false;
  }
  return true;
}

function hidePlanWindow() {
  if (getWindow(WIN_ID)) closeWindow(WIN_ID);
}

function hidePlanWindowIfFinished() {
  if (!planIsFinished()) return false;
  hidePlanWindow();
  return true;
}

function revealWindow() {
  ensureWindow();
  restoreWindow(WIN_ID);
}

function sendDecision(decision, feedback) {
  if (!_reviewBlockId && !_doc.planId) throw new Error("plan review has no planId");
  if (!state.api || !state.api.sendWsMsg) throw new Error("ws not ready");
  state.api.sendWsMsg("plan_review_respond", {
    toolCallId: _reviewBlockId || "",
    planId: _doc.planId || "",
    decision: decision,
    feedback: feedback || "",
    threadId: state.currentConversationId || "",
  });
}

function waitPlanDecisionIdle() {
  return new Promise(function (resolve) {
    messageBus.once("plan_decision_idle", function (msg) {
      resolve(msg);
    });
  });
}

function decidePlan(decision) {
  commitPlanDraft();
  return persistIfDirty().then(function () {
    var blockId = _reviewBlockId;
    var planId = _doc.planId;
    if (!blockId && !planId) throw new Error("plan review has no planId");
    if (!state.api || !state.api.sendWsMsg) throw new Error("ws not ready");
    _reviewBlockId = "";
    if (decision === "approve") {
      _doc.phase = "active";
      setPlanMode(false);
      persistSessionUi();
      cacheCurrentDoc();
      paintBody();
    } else {
      _doc.phase = "";
      cacheCurrentDoc();
      hidePlanWindow();
    }
    var idle = waitPlanDecisionIdle();
    state.api.sendWsMsg("plan_review_respond", {
      toolCallId: blockId || "",
      planId: planId || "",
      decision: decision,
      threadId: state.currentConversationId || "",
    });
    return idle.then(function (msg) {
      var text = String((msg && msg.data && msg.data.followup) || "").trim();
      if (!text) throw new Error("plan_decision_idle missing followup");
      var cs = getConvState(state.currentConversationId);
      if (cs) {
        cs.isStreaming = false;
        cs.pendingSend = false;
      }
      return import("./input.js").then(function (mod) {
        mod.sendPlainText(text);
      });
    });
  });
}

function renderTodos(todos) {
  var list = document.createElement("ul");
  list.className = "plan-board-todos";
  var items = Array.isArray(todos) ? todos : [];
  for (var i = 0; i < items.length; i++) {
    (function (index) {
      var item = items[index] || {};
      var st = String(item.status || "pending");
      var li = document.createElement("li");
      li.className = "plan-board-todo plan-board-todo-" + st;
      var mark = document.createElement("button");
      mark.type = "button";
      mark.className = "plan-board-todo-mark";
      mark.title = statusLabel(st);
      mark.onclick = function () {
        _doc.todos[index].status = nextStatus(String(_doc.todos[index].status || "pending"));
        _dirty = true;
        persistIfDirty().then(function () {
          if (!hidePlanWindowIfFinished()) paintBody();
        });
      };
      var input = document.createElement("input");
      input.type = "text";
      input.className = "plan-board-todo-input";
      input.value = String(item.content || "");
      input.onblur = function () {
        var next = String(input.value || "").trim();
        if (!next) {
          input.value = String(_doc.todos[index].content || "");
          return;
        }
        if (next === _doc.todos[index].content) return;
        _doc.todos[index].content = next;
        _dirty = true;
        persistIfDirty();
      };
      input.onkeydown = function (e) {
        if (e.key === "Enter") {
          e.preventDefault();
          input.blur();
        }
      };
      if (index === _focusTodoIndex) {
        _focusTodoIndex = -1;
        setTimeout(function () {
          input.focus();
          input.select();
        }, 0);
      }
      var lab = document.createElement("span");
      lab.className = "plan-board-todo-status";
      lab.textContent = statusLabel(st);
      var del = document.createElement("button");
      del.type = "button";
      del.className = "plan-board-todo-del";
      del.innerHTML = "&#10005;";
      del.title = "删除待办";
      del.onclick = function () {
        _doc.todos.splice(index, 1);
        _dirty = true;
        persistIfDirty().then(function () {
          if (!hidePlanWindowIfFinished()) paintBody();
        });
      };
      li.appendChild(mark);
      li.appendChild(input);
      li.appendChild(lab);
      li.appendChild(del);
      list.appendChild(li);
    })(i);
  }
  return list;
}

function paintBody() {
  var body = getWindowBody(WIN_ID);
  if (!body) return;
  body.innerHTML = "";
  var wrap = document.createElement("div");
  wrap.className = "plan-board";
  var main = document.createElement("div");
  main.className = "plan-board-main";
  var phase = _doc.phase || "";
  if (phase === "review") {
    var strip = document.createElement("div");
    strip.className = "plan-board-strip";
    strip.textContent = "计划待审";
    main.appendChild(strip);
  } else if (phase === "active") {
    var live = document.createElement("div");
    live.className = "plan-board-strip plan-board-strip-active";
    live.textContent = "执行中";
    main.appendChild(live);
  }
  var docHead = document.createElement("div");
  docHead.className = "plan-board-section-head";
  var docTitle = document.createElement("div");
  docTitle.className = "plan-board-section-title";
  docTitle.textContent = "计划";
  docHead.appendChild(docTitle);
  if (_doc.planId) {
    var editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "plan-board-text-btn";
    editBtn.textContent = _editingPlan ? "完成" : "编辑";
    editBtn.onclick = function () {
      if (_editingPlan) {
        commitPlanDraft();
        persistIfDirty().then(function () {
          _editingPlan = false;
          paintBody();
        });
        return;
      }
      _editingPlan = true;
      _planDraft = _doc.plan;
      paintBody();
    };
    docHead.appendChild(editBtn);
  }
  main.appendChild(docHead);
  if (_editingPlan) {
    var ta = document.createElement("textarea");
    ta.className = "plan-board-plan-editor";
    ta.value = _planDraft;
    ta.oninput = function () {
      _planDraft = ta.value;
    };
    main.appendChild(ta);
    setTimeout(function () { ta.focus(); }, 0);
  } else {
    var doc = document.createElement("div");
    doc.className = "plan-board-doc";
    doc.innerHTML = _doc.plan ? formatContent(_doc.plan) : "<p class=\"plan-board-empty\">还没有正式方案</p>";
    main.appendChild(doc);
  }
  wrap.appendChild(main);
  var todosPane = document.createElement("div");
  todosPane.className = "plan-board-todos-pane";
  var todoHead = document.createElement("div");
  todoHead.className = "plan-board-section-head";
  var todoTitle = document.createElement("div");
  todoTitle.className = "plan-board-section-title";
  todoTitle.textContent = "待办";
  todoHead.appendChild(todoTitle);
  if (_doc.planId) {
    var addBtn = document.createElement("button");
    addBtn.type = "button";
    addBtn.className = "plan-board-text-btn";
    addBtn.textContent = "添加";
    addBtn.onclick = addTodo;
    todoHead.appendChild(addBtn);
  }
  todosPane.appendChild(todoHead);
  if (_doc.todos && _doc.todos.length) {
    todosPane.appendChild(renderTodos(_doc.todos));
  } else {
    var empty = document.createElement("p");
    empty.className = "plan-board-empty";
    empty.textContent = "还没有待办";
    todosPane.appendChild(empty);
  }
  wrap.appendChild(todosPane);
  body.appendChild(wrap);
  paintFooter();
}

function paintFooter() {
  var win = getWindow(WIN_ID);
  if (!win) return;
  var footer = win.el.querySelector(".plan-board-footer");
  if (!footer) {
    footer = document.createElement("div");
    footer.className = "wm-footer plan-board-footer";
    var body = win.el.querySelector(".wm-body");
    if (!body) throw new Error("plan-board window missing body");
    body.after(footer);
  }
  footer.innerHTML = "";
  if (_doc.phase !== "review") {
    footer.hidden = true;
    return;
  }
  footer.hidden = false;
  var decline = document.createElement("button");
  decline.type = "button";
  decline.className = "plan-review-btn plan-review-decline";
  decline.textContent = "拒绝";
  decline.onclick = function () {
    decidePlan("decline");
  };
  var approve = document.createElement("button");
  approve.type = "button";
  approve.className = "plan-review-btn plan-review-approve";
  approve.textContent = "确认执行";
  approve.onclick = function () {
    decidePlan("approve");
  };
  footer.appendChild(decline);
  footer.appendChild(approve);
}

function ensureWindow() {
  var existing = getWindow(WIN_ID);
  if (existing) {
    paintBody();
    return existing;
  }
  var w = Math.min(420, Math.max(320, Math.floor(window.innerWidth * 0.32)));
  var h = Math.min(window.innerHeight - 24, 720);
  return openWindow(WIN_ID, {
    type: "plan-board",
    title: "plan",
    width: w,
    height: h,
    minWidth: 300,
    minHeight: 280,
    left: Math.max(8, window.innerWidth - w - 16),
    top: 12,
    closable: false,
    minimizable: true,
    onMount: function () {
      paintBody();
    },
    onClose: function () {
      if (_stashing) return;
      commitPlanDraft();
      persistIfDirty();
    },
  });
}

export function isPlanModeOn() {
  return _planOn;
}

export function setPlanMode(on) {
  _planOn = !!on;
  paintBtn();
}

export function applyPlanDoc(doc) {
  resetEditState();
  if (!doc || !doc.plan) {
    _doc = { plan: "", todos: [], phase: "", planId: "", userModified: false };
    _reviewBlockId = "";
    cacheCurrentDoc();
    hidePlanWindow();
    return;
  }
  _doc = {
    plan: String(doc.plan || ""),
    todos: Array.isArray(doc.todos) ? doc.todos : [],
    phase: String(doc.phase || "active"),
    planId: String(doc.planId || ""),
    userModified: !!doc.userModified,
  };
  _reviewBlockId = _doc.phase === "review" ? String(doc.reviewBlockId || "") : "";
  if (_doc.phase === "review") setPlanMode(true);
  cacheCurrentDoc();
  if (planIsFinished()) {
    hidePlanWindow();
    return;
  }
  revealWindow();
  updateWindowTitle(WIN_ID, "plan");
  paintBody();
}

export function applyTodoList(todos) {
  if (!Array.isArray(todos) || !todos.length) return;
  _doc.todos = todos;
  cacheCurrentDoc();
  if (planIsFinished()) {
    hidePlanWindow();
    return;
  }
  if (_doc.plan) ensureWindow();
  paintBody();
}

export function stashPlanBoard() {
  commitPlanDraft();
  var pending = persistIfDirty();
  cacheCurrentDoc();
  _stashing = true;
  _reviewBlockId = "";
  resetEditState();
  _doc = { plan: "", todos: [], phase: "", planId: "", userModified: false };
  _planOn = false;
  paintBtn();
  hidePlanWindow();
  _stashing = false;
  return pending;
}

export function dismissPlanReview() {
  if (_reviewBlockId) sendDecision("dismiss");
  abandonPlan();
}

export function abandonPlan() {
  var id = state.currentConversationId;
  if (id) {
    var cs = getConvState(id);
    if (cs) cs.planDoc = null;
  }
  _reviewBlockId = "";
  resetEditState();
  _doc = { plan: "", todos: [], phase: "", planId: "", userModified: false };
  cacheCurrentDoc();
  setPlanMode(false);
  hidePlanWindow();
  persistSessionUi();
}

export function showPlanReviewForCall(plan, blockId, todos, planId) {
  var text = String(plan || "").trim();
  var id = String(blockId || "");
  if (!text) throw new Error("plan review missing plan");
  if (!id) throw new Error("plan review missing blockId");
  if (_reviewBlockId === id) return;
  setPlanMode(true);
  _reviewBlockId = id;
  resetEditState();
  _doc = {
    plan: text,
    todos: Array.isArray(todos) ? todos : [],
    phase: "review",
    planId: String(planId || _doc.planId || ""),
    userModified: false,
  };
  cacheCurrentDoc();
  revealWindow();
  updateWindowTitle(WIN_ID, "plan");
  paintBody();
}

export function restorePlanReviewFromBlocks(cs, blocks) {
  if (!cs || cs.id !== state.currentConversationId) return;
  if (!Array.isArray(blocks)) return;
  for (var i = 0; i < blocks.length; i++) {
    var b = blocks[i];
    if (!b || !b.planReview || !b.planReview.plan) continue;
    if (String(b.status || "open") !== "open") continue;
    showPlanReviewForCall(b.planReview.plan, b.blockId, b.planReview.todos, b.planReview.planId);
  }
}

export function togglePlanMode() {
  if (_planOn) {
    if (_reviewBlockId) sendDecision("dismiss");
    _reviewBlockId = "";
    setPlanMode(false);
    persistSessionUi();
    if (_doc.phase !== "active") hidePlanWindow();
    return;
  }
  setPlanMode(true);
  if (_doc.plan && !planIsFinished()) revealWindow();
  persistSessionUi();
}

export function applyPlanStatus(payload) {
  if (!payload) return;
  if (!payload.endCalled || !payload.showCalled) return;
  var tid = String(payload.threadId || "");
  if (tid && tid !== state.currentConversationId) return;
  if (_reviewBlockId || _doc.phase === "review") return;
  if (payload.end) {
    setPlanMode(false);
    persistSessionUi();
    hidePlanWindow();
    return;
  }
  if (payload.show) {
    if (_doc.plan && !planIsFinished()) revealWindow();
    return;
  }
  hidePlanWindow();
}
