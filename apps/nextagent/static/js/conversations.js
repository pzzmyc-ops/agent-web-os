import { API_BASE } from "./config.js";
import { naFetch } from "./http.js";
import { state, getConvState, initConv, deleteConv, convCache } from "./state.js";
import { saveCurrentConvId, saveConversationList, saveFolderList } from "./cache.js";
import { setSendBtnSend, setSendBtnStop, syncCurrentPageThinkToggle, enableAutoFollow, syncSendButton, mountChatArea } from "./chat-ui.js";
import { reloadThreadHistory, fillHistoryViewport } from "./history.js";
import { syncAssetsThread, clearAssets, connectAssetWs } from "./assets.js";
import { refreshSidebarRoles } from "./roles.js";
import { syncMemoryWindow } from "./memory.js";
import { syncContextWindow } from "./context-window.js";
import { sendWsMsg } from "./ws.js";
import { applyConversationRemoval } from "./conversation-sync.js";
import { refreshPendingAttachmentsUi, switchComposerThread } from "./input.js";
import { findChatInflightTask } from "./composer.js";
import { stashPlanBoard, applyPlanDoc, setPlanMode } from "./plan-review.js";
import { persistSessionUiNow, applySessionUi } from "./session-ui.js";
import { removeTurnWaiting } from "./blocks-renderer.js";
import { openWindow, closeWindow, getWindow } from "./modules/window-manager/index.js";
import { currentApp, filterAppConversations, filterAppFolders, isFeishuWebhookApp, syncFeishuWebhookChrome } from "./feishu-webhook-app.js";

var PENCIL_ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg>';

export function fetchConversations() {
  return naFetch(API_BASE + "/api/v1/conversations")
    .then(function (r) { return r.json(); });
}

export function createConversation() {
  var opts = { method: "POST" };
  var body = {};
  if (_activeFolderId) body.folderId = _activeFolderId;
  if (isFeishuWebhookApp()) body.app = "feishu_webhook";
  if (Object.keys(body).length) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(body);
  }
  return naFetch(API_BASE + "/api/v1/conversations", opts)
    .then(function (r) { return r.json(); });
}

export function switchConversation(threadId) {
  return naFetch(API_BASE + "/api/v1/conversations/switch?threadId=" + encodeURIComponent(threadId), { method: "POST" })
    .then(function (r) { return r.json(); });
}

export function deleteConversation(threadId) {
  return naFetch(API_BASE + "/api/v1/conversations/" + encodeURIComponent(threadId), { method: "DELETE" })
    .then(function (r) {
      const ct = r.headers.get("content-type") || "";
      if (ct.includes("application/json")) return r.json();
      if (!r.ok) return { ok: false, error: "删除失败: " + r.status };
      return { ok: true };
    });
}

export function initFirstConversation(convId, options) {
  options = options || {};
  state.currentConversationId = convId;
  var chatScroll = document.getElementById("chatScroll");
  var existingArea = document.getElementById("chatArea");
  var allAreas = chatScroll.querySelectorAll(".chat-area");
  for (var i = 0; i < allAreas.length; i++) {
    if (allAreas[i] !== existingArea) {
      chatScroll.removeChild(allAreas[i]);
    }
  }
  if (existingArea) {
    existingArea.removeAttribute("id");
    initConv(convId, existingArea);
  } else {
    getConvState(convId);
    mountChatArea(getConvState(convId).el);
  }
  if (!options.skipAssetConnect) {
    connectAssetWs(convId);
  }
  switchComposerThread(convId);
  refreshPendingAttachmentsUi();
}

function swapChatArea(targetConvId) {
  var chatScroll = document.getElementById("chatScroll");
  var currentArea = chatScroll.querySelector(".chat-area");
  if (currentArea) chatScroll.removeChild(currentArea);
  var targetCs = getConvState(targetConvId);
  mountChatArea(targetCs.el);
}

function getTitleById(list, convId) {
  const targetId = convId == null ? "" : String(convId);
  const matched = (list || []).find(function (item) {
    return String(item.id) === targetId;
  });
  return matched ? (matched.title || "新对话") : "新对话";
}

var _listCache = [];
var _folderCache = [];
var _currentId = "";
var _searchQuery = "";
var _selectMode = false;
var _selected = {};
var _activeFolderId = "";
var _collapsed = {};
var _lastSelectedId = "";
var _visibleIds = [];

function relativeTime(ms) {
  var ts = Number(ms);
  if (!ts) return "";
  var diff = Date.now() - ts;
  if (diff < 0) diff = 0;
  var minute = 60 * 1000;
  var hour = 60 * minute;
  var day = 24 * hour;
  if (diff < minute) return "刚刚";
  if (diff < hour) return Math.floor(diff / minute) + "分钟";
  if (diff < day) return Math.floor(diff / hour) + "小时";
  if (diff < 30 * day) return Math.floor(diff / day) + "天";
  if (diff < 365 * day) return Math.floor(diff / (30 * day)) + "个月";
  return Math.floor(diff / (365 * day)) + "年";
}

function openConversation(c) {
  if (String(c.id) === String(state.currentConversationId)) return;
  persistSessionUiNow().then(function () {
    return stashPlanBoard();
  }).then(function () {
    switchComposerThread(c.id);
    state.currentConversationId = c.id;
    state.currentConversationTitle = c.title || "新对话";
    _currentId = c.id;
    if (state.currentUserId) saveCurrentConvId(state.currentUserId, c.id);
    setConversationTitle(state.currentConversationTitle);
    syncDingtalkChrome(c);
    syncFeishuWebhookChrome(c);
    sendWsMsg("thread_focus", { threadId: c.id });

    swapChatArea(c.id);
    var targetCs = getConvState(c.id);
    applySessionUi(targetCs.sessionUi || {});
    applyPlanDoc(targetCs.planDoc || null);
    syncMemoryWindow();
    syncContextWindow();
    syncDingtalkChrome(c);
    syncFeishuWebhookChrome(c);
    if (!findChatInflightTask(c.id)) {
      removeTurnWaiting(targetCs);
    }
    syncCurrentPageThinkToggle(targetCs.el);
    syncSendButton();

    var el = document.getElementById("conversationList");
    if (el) {
      el.querySelectorAll(".sidebar-item").forEach(function (item) {
        item.classList.toggle("active", String(item.dataset.threadId) === String(c.id));
      });
    }

    return switchConversation(c.id).then(function () {
      refreshSidebarRoles();
      connectAssetWs(c.id);
      if (targetCs.historyLoaded && !targetCs.historyLoading) {
        enableAutoFollow();
        fillHistoryViewport(targetCs);
        return;
      }
      return reloadThreadHistory(c.id, "conversation_switch");
    }).then(function () {
      enableAutoFollow();
    });
  });
}

function renameConversationRow(c) {
  askFolderName("重命名对话", c.title || "", "确定", function (name) {
    naFetch(API_BASE + "/api/v1/conversations/" + encodeURIComponent(c.id), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: name }),
    }).then(function (r) { return r.json(); }).then(function (res) {
      applyFolderPayload(res);
      updateConversationTitle(c.id, name);
    });
  }, "对话名称");
}

function deleteConversationRow(c) {
  var deletingId = c.id;
  var delCs = convCache[deletingId];
  var taskId = (delCs && delCs.currentTaskId) || findChatInflightTask(deletingId);
  if (taskId) {
    sendWsMsg("task_control", { action: "cancel", taskId: String(taskId) });
  }
  deleteConversation(deletingId).then(function (res) {
    if (res.ok) {
      applyConversationRemoval(deletingId, res);
    } else {
      alert(res.error || "删除失败");
    }
  });
}

function folderIdOf(c) {
  return String(c.folderId || "");
}

function persistSidebarLists(conversations, folders) {
  if (!state.currentUserId) return;
  if (conversations) saveConversationList(state.currentUserId, conversations);
  if (folders) saveFolderList(state.currentUserId, folders);
}

function applyFolderPayload(res) {
  _listCache = res.conversations || _listCache;
  _folderCache = res.folders || _folderCache;
  persistSidebarLists(_listCache, _folderCache);
  paintConversationList();
}

function moveConversationToFolder(threadId, folderId) {
  return naFetch(API_BASE + "/api/v1/conversations/" + encodeURIComponent(threadId) + "/folder", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folderId: folderId || "" }),
  }).then(function (r) { return r.json(); }).then(applyFolderPayload);
}

function parseDragThreadIds(raw) {
  if (!raw) return [];
  var obj = JSON.parse(raw);
  if (!obj || !obj.threadIds) throw new Error("拖动数据缺少 threadIds");
  return obj.threadIds.map(String);
}

function moveConversationsToFolder(threadIds, folderId) {
  var dest = String(folderId || "");
  var ids = threadIds.filter(function (id) {
    var cur = convById(id);
    return !(cur && folderIdOf(cur) === dest);
  });
  if (!ids.length) return;
  var chain = Promise.resolve();
  ids.forEach(function (tid) {
    chain = chain.then(function () {
      return moveConversationToFolder(tid, dest);
    });
  });
  return chain;
}

function bindFolderDrop(el, folderId) {
  el.ondragover = function (e) {
    e.preventDefault();
    el.classList.add("is-drop");
  };
  el.ondragleave = function () {
    el.classList.remove("is-drop");
  };
  el.ondrop = function (e) {
    e.preventDefault();
    e.stopPropagation();
    el.classList.remove("is-drop");
    var ids = parseDragThreadIds(e.dataTransfer.getData("text/plain"));
    if (!ids.length) return;
    moveConversationsToFolder(ids, folderId);
  };
}

function makeConvRow(c) {
  var wrap = document.createElement("div");
  wrap.className = "sidebar-item-wrap";

  var btn = document.createElement("button");
  btn.type = "button";
  btn.className = "sidebar-item" + (String(c.id) === String(_currentId) ? " active" : "");
  btn.dataset.threadId = c.id;
  btn.draggable = true;

  var check = document.createElement("span");
  check.className = "sidebar-item-check";
  btn.appendChild(check);
  if (_selected[String(c.id)]) btn.classList.add("is-checked");

  var title = document.createElement("span");
  title.className = "sidebar-item-title";
  title.textContent = c.title || "新对话";
  btn.appendChild(title);
  if (c.group_chat) {
    var tag = document.createElement("span");
    var channel = String(c.channel || "");
    if (!c.source_label) throw new Error("绑定缺少来源");
    if (channel === "dingtalk") tag.className = "sidebar-dingtalk-tag";
    else if (channel === "feishu") tag.className = "sidebar-feishu-tag";
    else throw new Error("未知通道: " + channel);
    tag.textContent = c.source_label;
    btn.appendChild(tag);
  } else if (String(c.app || "") === "feishu_webhook") {
    var hookTag = document.createElement("span");
    hookTag.className = "sidebar-feishu-tag";
    hookTag.textContent = "Webhook";
    btn.appendChild(hookTag);
  }

  var timeLabel = relativeTime(c.updatedAt);
  if (timeLabel) {
    var time = document.createElement("span");
    time.className = "sidebar-item-time";
    time.textContent = timeLabel;
    btn.appendChild(time);
  }

  btn.onclick = function (e) {
    var ctrl = e.ctrlKey || e.metaKey;
    var shift = e.shiftKey;
    if (_selectMode || ctrl || shift) {
      var was = _selectMode;
      if (!_selectMode) {
        _selectMode = true;
        _selected = {};
        setSearchOpen(false);
        syncSelectChrome();
      }
      if (shift) {
        selectRange(c.id);
        return;
      }
      toggleSelected(c.id);
      _lastSelectedId = String(c.id);
      if (!was) paintConversationList();
      return;
    }
    _activeFolderId = folderIdOf(c);
    openConversation(c);
  };
  btn.ondragstart = function (e) {
    var id = String(c.id);
    var ids = selectedIds();
    if (_selectMode && ids.length && ids.indexOf(id) >= 0) {
      e.dataTransfer.setData("text/plain", JSON.stringify({ threadIds: ids }));
    } else {
      e.dataTransfer.setData("text/plain", JSON.stringify({ threadIds: [id] }));
    }
    e.dataTransfer.effectAllowed = "move";
  };

  var renBtn = document.createElement("button");
  renBtn.type = "button";
  renBtn.className = "sidebar-item-ren";
  renBtn.title = "重命名";
  renBtn.innerHTML = PENCIL_ICON;
  renBtn.onclick = function (e) {
    e.stopPropagation();
    renameConversationRow(c);
  };

  var delBtn = document.createElement("button");
  delBtn.type = "button";
  delBtn.className = "sidebar-item-del";
  delBtn.innerHTML = "&#10005;";
  delBtn.title = "删除对话";
  delBtn.onclick = function (e) {
    e.stopPropagation();
    askConfirm("删除对话", "确定要删除这个对话吗？", "删除", function () {
      deleteConversationRow(c);
    });
  };

  wrap.appendChild(btn);
  wrap.appendChild(renBtn);
  wrap.appendChild(delBtn);
  return wrap;
}

function askConfirm(title, message, okLabel, onOk) {
  var dlgId = "conversation-confirm";
  if (getWindow(dlgId)) closeWindow(dlgId);
  openWindow(dlgId, {
    type: "dialog",
    title: title,
    width: 360,
    height: 150,
    minWidth: 280,
    minHeight: 130,
    onMount: function (body) {
      body.style.cssText = "display:flex;flex-direction:column;gap:12px;padding:16px;";
      var label = document.createElement("div");
      label.style.cssText = "font-size:14px;color:var(--text,#333);";
      label.textContent = message;
      body.appendChild(label);
      var btns = document.createElement("div");
      btns.style.cssText = "display:flex;justify-content:flex-end;gap:8px;";
      var cancelBtn = document.createElement("button");
      cancelBtn.className = "ae-dialog-btn";
      cancelBtn.textContent = "取消";
      cancelBtn.onclick = function () { closeWindow(dlgId); };
      var okBtn = document.createElement("button");
      okBtn.className = "ae-dialog-btn ae-dialog-btn-danger";
      okBtn.textContent = okLabel;
      okBtn.onclick = function () {
        closeWindow(dlgId);
        onOk();
      };
      btns.appendChild(cancelBtn);
      btns.appendChild(okBtn);
      body.appendChild(btns);
      setTimeout(function () { okBtn.focus(); }, 50);
    },
  });
}

function askFolderName(title, initial, okLabel, onOk, placeholder) {
  var dlgId = "conversation-folder-name";
  if (getWindow(dlgId)) closeWindow(dlgId);
  openWindow(dlgId, {
    type: "dialog",
    title: title,
    width: 360,
    height: 160,
    minWidth: 280,
    minHeight: 140,
    onMount: function (body) {
      body.style.cssText = "display:flex;flex-direction:column;gap:12px;padding:16px;";
      var input = document.createElement("input");
      input.type = "text";
      input.placeholder = placeholder || "名称";
      input.className = "ae-dialog-input";
      input.value = initial || "";
      input.style.cssText = "width:100%;padding:6px 10px;font-size:14px;border:1px solid var(--border,#ddd);border-radius:4px;box-sizing:border-box;outline:none;";
      body.appendChild(input);
      var btns = document.createElement("div");
      btns.style.cssText = "display:flex;justify-content:flex-end;gap:8px;";
      var cancelBtn = document.createElement("button");
      cancelBtn.className = "ae-dialog-btn";
      cancelBtn.textContent = "取消";
      cancelBtn.onclick = function () { closeWindow(dlgId); };
      var okBtn = document.createElement("button");
      okBtn.className = "ae-dialog-btn ae-dialog-btn-primary";
      okBtn.textContent = okLabel;
      function submit() {
        var val = String(input.value || "").replace(/^\s+/, "").replace(/\s+$/, "");
        if (!val) return;
        closeWindow(dlgId);
        onOk(val);
      }
      okBtn.onclick = submit;
      input.onkeydown = function (e) {
        if (e.key === "Enter") submit();
      };
      btns.appendChild(cancelBtn);
      btns.appendChild(okBtn);
      body.appendChild(btns);
      setTimeout(function () {
        input.focus();
        input.select();
      }, 50);
    },
  });
}

function renameFolderRow(f) {
  askFolderName("重命名文件夹", f.name || "", "确定", function (name) {
    naFetch(API_BASE + "/api/v1/conversation-folders/" + encodeURIComponent(f.id), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name }),
    }).then(function (r) { return r.json(); }).then(applyFolderPayload);
  }, "文件夹名称");
}

function deleteFolderRow(f) {
  if (!confirm("删除文件夹后，里面的对话会回到未分组。确定删除？")) return;
  naFetch(API_BASE + "/api/v1/conversation-folders/" + encodeURIComponent(f.id), { method: "DELETE" })
    .then(function (r) { return r.json(); })
    .then(function (res) {
      if (String(_activeFolderId) === String(f.id)) _activeFolderId = "";
      applyFolderPayload(res);
    });
}

function makeFolderBlock(f, kids, forceOpen) {
  var box = document.createElement("div");
  box.className = "sidebar-folder";
  var collapsed = !forceOpen && !!_collapsed[String(f.id)];
  if (collapsed) box.classList.add("is-collapsed");

  var head = document.createElement("div");
  head.className = "sidebar-folder-head" + (String(f.id) === String(_activeFolderId) ? " is-active" : "");

  var caret = document.createElement("span");
  caret.className = "sidebar-folder-caret";
  caret.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6l4 4 4-4"/></svg>';
  head.appendChild(caret);

  var name = document.createElement("span");
  name.className = "sidebar-folder-name";
  name.textContent = f.name || "文件夹";
  head.appendChild(name);

  var ren = document.createElement("button");
  ren.type = "button";
  ren.className = "sidebar-folder-ren";
  ren.title = "重命名";
  ren.innerHTML = PENCIL_ICON;
  ren.onclick = function (e) {
    e.stopPropagation();
    renameFolderRow(f);
  };
  head.appendChild(ren);

  var del = document.createElement("button");
  del.type = "button";
  del.className = "sidebar-folder-del";
  del.title = "删除文件夹";
  del.innerHTML = "&#10005;";
  del.onclick = function (e) {
    e.stopPropagation();
    deleteFolderRow(f);
  };
  head.appendChild(del);

  head.onclick = function () {
    _collapsed[String(f.id)] = !collapsed;
    if (!_selectMode) _activeFolderId = f.id;
    paintConversationList();
  };
  bindFolderDrop(head, f.id);

  var body = document.createElement("div");
  body.className = "sidebar-folder-body";
  kids.forEach(function (c) {
    body.appendChild(makeConvRow(c));
  });
  bindFolderDrop(body, f.id);

  box.appendChild(head);
  box.appendChild(body);
  return box;
}

function createFolderRow() {
  askFolderName("新建文件夹", "", "创建", function (name) {
    naFetch(API_BASE + "/api/v1/conversation-folders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name, app: currentApp() }),
    }).then(function (r) { return r.json(); }).then(function (res) {
      if (res.folder) _activeFolderId = res.folder.id;
      applyFolderPayload(res);
    });
  }, "文件夹名称");
}

function paintConversationList() {
  var el = document.getElementById("conversationList");
  if (!el) return;
  el.innerHTML = "";
  var q = _searchQuery.replace(/^\s+/, "").replace(/\s+$/, "").toLowerCase();
  var matchIds = {};
  var scoped = filterAppConversations(_listCache);
  var rows = q
    ? scoped.filter(function (c) {
      var hit = String(c.title || "新对话").toLowerCase().indexOf(q) !== -1;
      if (hit) matchIds[String(c.id)] = true;
      return hit;
    })
    : scoped.slice();
  var folders = filterAppFolders(_folderCache);
  if (q) {
    folders = folders.filter(function (f) {
      if (String(f.name || "").toLowerCase().indexOf(q) !== -1) return true;
      return scoped.some(function (c) {
        return folderIdOf(c) === String(f.id) && matchIds[String(c.id)];
      });
    });
    folders.forEach(function (f) {
      if (String(f.name || "").toLowerCase().indexOf(q) === -1) return;
      scoped.forEach(function (c) {
        if (folderIdOf(c) !== String(f.id) || matchIds[String(c.id)]) return;
        rows.push(c);
        matchIds[String(c.id)] = true;
      });
    });
  }
  if (q && !rows.length && !folders.length) {
    _visibleIds = [];
    var empty = document.createElement("div");
    empty.className = "sidebar-search-empty";
    empty.textContent = "无匹配会话";
    el.appendChild(empty);
    return;
  }
  var alive = {};
  folders.forEach(function (f) { alive[String(f.id)] = true; });
  if (_activeFolderId && !alive[String(_activeFolderId)]) _activeFolderId = "";
  var visible = [];
  folders.forEach(function (f) {
    var kids = rows.filter(function (c) { return folderIdOf(c) === String(f.id); });
    kids.forEach(function (c) { visible.push(String(c.id)); });
    el.appendChild(makeFolderBlock(f, kids, !!q));
  });
  var loose = rows.filter(function (c) { return !alive[folderIdOf(c)]; });
  loose.forEach(function (c) { visible.push(String(c.id)); });
  _visibleIds = visible;
  if (folders.length) {
    var un = document.createElement("div");
    un.className = "sidebar-ungrouped";
    var lab = document.createElement("div");
    lab.className = "sidebar-ungrouped-label" + (!_activeFolderId ? " is-active" : "");
    lab.textContent = "未分组";
    lab.onclick = function () {
      _activeFolderId = "";
      paintConversationList();
    };
    un.appendChild(lab);
    loose.forEach(function (c) { un.appendChild(makeConvRow(c)); });
    bindFolderDrop(un, "");
    el.appendChild(un);
  } else {
    loose.forEach(function (c) { el.appendChild(makeConvRow(c)); });
  }
}

function selectedCount() {
  var n = 0;
  Object.keys(_selected).forEach(function (k) {
    if (_selected[k]) n += 1;
  });
  return n;
}

function selectedIds() {
  return Object.keys(_selected).filter(function (k) {
    return _selected[k];
  });
}

function syncSelectChrome() {
  var header = document.getElementById("sidebarSearchHeader");
  var countEl = document.getElementById("sidebarSelectCount");
  var actions = document.getElementById("sidebarSelectActions");
  var del = document.getElementById("sidebarSelectDelete");
  if (!header || !countEl || !actions || !del) throw new Error("sidebar select chrome missing");
  header.classList.toggle("is-selecting", _selectMode);
  countEl.textContent = "已选 " + selectedCount();
  del.disabled = selectedCount() === 0;
}

function selectRange(toId) {
  var ids = _visibleIds;
  var end = ids.indexOf(String(toId));
  if (end < 0) throw new Error("对话不在当前列表");
  var start = ids.indexOf(String(_lastSelectedId || ""));
  if (start < 0) start = end;
  if (start > end) {
    var tmp = start;
    start = end;
    end = tmp;
  }
  for (var i = start; i <= end; i++) _selected[ids[i]] = true;
  _lastSelectedId = String(toId);
  syncSelectChrome();
  paintConversationList();
}

function setSelectMode(on) {
  _selectMode = !!on;
  _selected = {};
  _lastSelectedId = "";
  if (_selectMode) setSearchOpen(false);
  syncSelectChrome();
  paintConversationList();
}

function toggleSelected(id) {
  var key = String(id);
  if (_selected[key]) delete _selected[key];
  else _selected[key] = true;
  syncSelectChrome();
  var btn = document.querySelector('.sidebar-item[data-thread-id="' + key + '"]');
  if (btn) btn.classList.toggle("is-checked", !!_selected[key]);
}

function deleteSelectedRows() {
  var ids = selectedIds();
  if (!ids.length) return;
  if (ids.length >= _listCache.length) {
    alert("至少保留一个对话");
    return;
  }
  askConfirm("删除对话", "确定删除选中的 " + ids.length + " 个对话？", "删除", function () {
    var lastRes = null;
    var deleted = [];
    var chain = Promise.resolve();
    ids.forEach(function (id) {
      chain = chain.then(function () {
        return deleteConversation(id).then(function (res) {
          if (!res.ok) throw new Error(res.error || "删除失败");
          deleted.push(id);
          lastRes = res;
        });
      });
    });
    chain.then(function () {
      _selectMode = false;
      _selected = {};
      syncSelectChrome();
      applyConversationRemoval(deleted, lastRes);
    });
  });
}

function setSearchOpen(open) {
  var header = document.getElementById("sidebarSearchHeader");
  var input = document.getElementById("sidebarSearchInput");
  var clear = document.getElementById("sidebarSearchClear");
  if (!header || !input || !clear) throw new Error("sidebar search chrome missing");
  if (open && _selectMode) {
    _selectMode = false;
    _selected = {};
    syncSelectChrome();
    paintConversationList();
  }
  header.classList.toggle("is-open", open);
  clear.hidden = !_searchQuery;
  if (open) {
    input.focus();
  } else {
    input.blur();
  }
}

function applySearchQuery(raw) {
  _searchQuery = String(raw || "");
  var clear = document.getElementById("sidebarSearchClear");
  if (clear) clear.hidden = !_searchQuery;
  paintConversationList();
}

function bindSidebarSearch() {
  var header = document.getElementById("sidebarSearchHeader");
  var btn = document.getElementById("sidebarSearchBtn");
  var input = document.getElementById("sidebarSearchInput");
  var clear = document.getElementById("sidebarSearchClear");
  if (!header || !btn || !input || !clear) throw new Error("sidebar search chrome missing");
  btn.onclick = function (e) {
    e.stopPropagation();
    setSearchOpen(true);
  };
  input.oninput = function () {
    applySearchQuery(input.value);
  };
  input.onkeydown = function (e) {
    if (e.key === "Escape") {
      input.value = "";
      applySearchQuery("");
      setSearchOpen(false);
    }
  };
  clear.onclick = function (e) {
    e.stopPropagation();
    input.value = "";
    applySearchQuery("");
    setSearchOpen(false);
  };
  document.addEventListener("mousedown", function (e) {
    if (!header.classList.contains("is-open")) return;
    if (header.contains(e.target)) return;
    if (_searchQuery) return;
    setSearchOpen(false);
  });
}

function bindSidebarSelect() {
  var btn = document.getElementById("sidebarSelectBtn");
  var cancel = document.getElementById("sidebarSelectCancel");
  var del = document.getElementById("sidebarSelectDelete");
  if (!btn || !cancel || !del) throw new Error("sidebar select chrome missing");
  btn.onclick = function (e) {
    e.stopPropagation();
    setSelectMode(true);
  };
  cancel.onclick = function (e) {
    e.stopPropagation();
    setSelectMode(false);
  };
  del.onclick = function (e) {
    e.stopPropagation();
    deleteSelectedRows();
  };
}

function bindSidebarFolder() {
  var btn = document.getElementById("sidebarFolderBtn");
  if (!btn) throw new Error("sidebar folder chrome missing");
  btn.onclick = function (e) {
    e.stopPropagation();
    createFolderRow();
  };
}

bindSidebarSearch();
bindSidebarSelect();
bindSidebarFolder();

export function renderConversationList(list, currentId, folders) {
  _listCache = list || [];
  _currentId = currentId;
  if (arguments.length >= 3) {
    _folderCache = folders || [];
  }
  var currentTitle = getTitleById(_listCache, currentId);
  paintConversationList();
  state.currentConversationTitle = currentTitle;
  setConversationTitle(currentTitle);
  syncDingtalkChrome(convById(currentId));
  syncFeishuWebhookChrome(convById(currentId));
}

export function markFeishuWebhookBound(threadId) {
  for (var i = 0; i < _listCache.length; i++) {
    if (String(_listCache[i].id) === String(threadId)) {
      _listCache[i].feishuWebhookBound = true;
      break;
    }
  }
  syncFeishuWebhookChrome(convById(threadId));
}

export function convById(threadId) {
  for (var i = 0; i < _listCache.length; i++) {
    if (String(_listCache[i].id) === String(threadId)) return _listCache[i];
  }
  return null;
}

export function focusFirstDingtalkConversation() {
  for (var i = 0; i < _listCache.length; i++) {
    if (_listCache[i].dingtalk) {
      openConversation(_listCache[i]);
      return;
    }
  }
}

function syncDingtalkChrome(c) {
  var on = !!(c && c.group_chat);
  var btn = document.getElementById("planModeBtn");
  if (btn) btn.style.display = on ? "none" : "";
  var speakBtn = document.getElementById("agentSpeakBtn");
  if (speakBtn) speakBtn.style.display = on ? "" : "none";
  var banner = document.getElementById("dingtalkBanner");
  if (banner) {
    banner.hidden = !on;
    if (on) {
      var channel = String(c.channel || "");
      if (!c.source_label) throw new Error("绑定缺少来源");
      if (channel === "dingtalk") banner.className = "dingtalk-banner";
      else if (channel === "feishu") banner.className = "feishu-banner";
      else throw new Error("未知通道: " + channel);
      banner.textContent = c.source_label + "会话 · " + (c.group_name || c.title || "");
    } else {
      banner.className = "dingtalk-banner";
      banner.textContent = "";
    }
  }
  if (on) setPlanMode(false);
}

export function setConversationTitle(title) {
  const titleEl = document.getElementById("mobileChatTitle");
  if (titleEl) {
    titleEl.textContent = title || "新对话";
  }
}

export function updateConversationTitle(threadId, title) {
  var next = title || "新对话";
  for (var i = 0; i < _listCache.length; i++) {
    if (String(_listCache[i].id) === String(threadId)) {
      _listCache[i].title = next;
      _listCache[i].updatedAt = Date.now();
      break;
    }
  }
  var el = document.getElementById("conversationList");
  if (el) {
    var items = el.querySelectorAll(".sidebar-item");
    items.forEach(function (btn) {
      if (String(btn.dataset.threadId) !== String(threadId)) return;
      var titleEl = btn.querySelector(".sidebar-item-title");
      if (titleEl) titleEl.textContent = next;
      var timeEl = btn.querySelector(".sidebar-item-time");
      var label = relativeTime(Date.now());
      if (timeEl) {
        timeEl.textContent = label;
      } else if (label) {
        var time = document.createElement("span");
        time.className = "sidebar-item-time";
        time.textContent = label;
        btn.appendChild(time);
      }
    });
  }
  if (String(state.currentConversationId) === String(threadId)) {
    state.currentConversationTitle = next;
    setConversationTitle(state.currentConversationTitle);
  }
}
