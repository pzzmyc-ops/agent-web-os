export function isTodoToolName(name) {
  return String(name || "").trim().toLowerCase() === "todo";
}

export function todosFromPayload(data) {
  if (!data || typeof data !== "object") return null;
  if (!Array.isArray(data.todos) || data.todos.length === 0) return null;
  return data.todos;
}

export function parseTodoListFromToolResult(resultPreview) {
  if (resultPreview == null || resultPreview === "") return null;
  var data = resultPreview;
  if (typeof data === "string") {
    try {
      data = JSON.parse(data);
    } catch (e) {
      return null;
    }
  }
  return todosFromPayload(data);
}

function todoStatusNormalized(st) {
  return String(st != null ? st : "pending").trim().toLowerCase().replace(/\s+/g, "_") || "pending";
}

function isTodoStatusCompleted(st) {
  var s = todoStatusNormalized(st);
  return s === "completed" || s === "done";
}

export function buildTodoListElement(todos) {
  var wrap = document.createElement("div");
  wrap.className = "chat-todos-panel";
  var total = todos.length;
  var completed = 0;
  for (var i = 0; i < todos.length; i++) {
    if (isTodoStatusCompleted(todos[i].status)) completed += 1;
  }
  var head = document.createElement("button");
  head.type = "button";
  head.className = "chat-todos-panel-head";
  var chev = document.createElement("span");
  chev.className = "chat-todos-chevron";
  chev.setAttribute("aria-hidden", "true");
  var progress = document.createElement("span");
  progress.className = "chat-todos-progress";
  progress.textContent = completed + " of " + total + " To-dos Completed";
  head.appendChild(chev);
  head.appendChild(progress);
  var body = document.createElement("div");
  body.className = "chat-todos-panel-body";
  var list = document.createElement("ul");
  list.className = "chat-todos-list";
  for (var j = 0; j < todos.length; j++) {
    var item = todos[j];
    var id = String(item.id || "").trim();
    var contentRaw = item.content != null ? item.content : "";
    var content = String(contentRaw !== "" ? contentRaw : id || "");
    var done = isTodoStatusCompleted(item.status);
    var li = document.createElement("li");
    li.className = "chat-todo-row" + (done ? " chat-todo-row-done" : "");
    if (id) li.dataset.todoId = id;
    var icon = document.createElement("span");
    icon.className = "chat-todo-icon" + (done ? " chat-todo-icon-done" : " chat-todo-icon-pending");
    icon.setAttribute("aria-hidden", "true");
    var text = document.createElement("span");
    text.className = "chat-todo-text";
    text.textContent = content;
    li.appendChild(icon);
    li.appendChild(text);
    list.appendChild(li);
  }
  body.appendChild(list);
  wrap.appendChild(head);
  wrap.appendChild(body);
  var collapsed = false;
  head.onclick = function (e) {
    e.preventDefault();
    e.stopPropagation();
    collapsed = !collapsed;
    body.style.display = collapsed ? "none" : "";
    wrap.classList.toggle("chat-todos-collapsed", collapsed);
  };
  return wrap;
}

export function mountTodoToolResult(resultEl, resultPreview) {
  var todos = parseTodoListFromToolResult(resultPreview);
  if (!todos) return false;
  resultEl.textContent = "";
  resultEl.classList.add("tool-step-result-todos");
  resultEl.appendChild(buildTodoListElement(todos));
  return true;
}

export function renderTodoToolInput(toolEl, inputObj) {
  var bodyEl = toolEl.querySelector(".tool-step-body");
  var pre = toolEl.querySelector(".tool-step-input");
  var mount = toolEl.querySelector(".tool-step-todo");
  if (!pre || !mount) return;
  var payload = inputObj && typeof inputObj === "object" ? inputObj : {};
  var todos = todosFromPayload(payload);
  if (todos) {
    pre.hidden = true;
    mount.style.display = "block";
    mount.innerHTML = "";
    mount.appendChild(buildTodoListElement(todos));
    if (bodyEl) {
      bodyEl.hidden = false;
      bodyEl.style.display = "";
      var card = bodyEl.closest(".tool-step-card");
      if (card) card.classList.add("is-open");
    }
  } else {
    mount.style.display = "none";
    mount.innerHTML = "";
    pre.hidden = true;
    pre.textContent = JSON.stringify(payload, null, 2);
  }
}

export function applyTodoToolStart(toolEl, toolName, inputObj) {
  if (!isTodoToolName(toolName)) return false;
  renderTodoToolInput(toolEl, inputObj || {});
  return true;
}

export function todoToolDisplayLabel(toolName) {
  return isTodoToolName(toolName) ? "Todos" : toolName;
}
