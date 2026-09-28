// 角色库侧栏 —— 角色卡 CRUD + 拖拽入对话
import { API_BASE } from "./config.js";
import { naFetch } from "./http.js";
import { state } from "./state.js";
import { sendWsMsg } from "./ws.js";
import { openWindow, closeWindow } from "./modules/window-manager/index.js";
import { messageBus } from "./message-bus.js";

let _roles = [];
let _active = new Set();

export function initRoles() {
  console.log("[roles] initRoles called");
  const toggle = document.getElementById("roleSidebarToggle");
  const sidebar = document.getElementById("roleSidebar");
  const close = document.getElementById("roleSidebarClose");
  if (!toggle || !sidebar || !close) return;

  toggle.addEventListener("click", () => {
    const open = sidebar.classList.contains("open");
    sidebar.classList.toggle("open", !open);
    toggle.setAttribute("aria-expanded", open ? "false" : "true");
    if (!open) { loadRoles(); refreshActive(); }
  });
  close.addEventListener("click", () => {
    sidebar.classList.remove("open");
    toggle.setAttribute("aria-expanded", "false");
  });

  document.getElementById("roleNewBtn")?.addEventListener("click", () => openEditWindow(null));

  // 拖入聊天区
  const cs = document.getElementById("chatScroll");
  if (cs) {
    console.log("[roles] chatScroll found, setting up drag");
    let dc = 0;
    cs.addEventListener("dragenter", e => {
      e.preventDefault(); dc++;
      cs.style.outline = "2px dashed var(--accent,#6366f1)";
      cs.style.outlineOffset = "-4px";
    });
    cs.addEventListener("dragleave", e => {
      dc--;
      if (dc <= 0) { dc = 0; cs.style.outline = ""; }
    });
    cs.addEventListener("dragover", e => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    });
    cs.addEventListener("drop", async e => {
      e.preventDefault(); dc = 0; cs.style.outline = "";
      console.log("[roles] drop fired");
      const raw = e.dataTransfer.getData("application/json");
      console.log("[roles] drop raw:", raw, "activeIds:", [..._active]);
      if (!raw) { console.log("[roles] drop: no raw data"); return; }
      let role;
      try { role = JSON.parse(raw); } catch (_) { console.log("[roles] drop: JSON parse failed"); return; }
      console.log("[roles] drop parsed:", role);
      if (!role.role_id) { console.log("[roles] drop: no role_id"); return; }
      if (_active.has(role.role_id)) { console.log("[roles] drop: already active"); return; }
      const tid = state.currentConversationId;
      console.log("[roles] drop tid:", tid);
      if (!tid) { console.log("[roles] drop: no tid"); return; }
      try {
        console.log("[roles] calling API...");
        const apiRes = await naFetch(API_BASE + "/api/v1/threads/" + tid + "/roles", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role_id: role.role_id }),
        });
        console.log("[roles] API OK, status:", apiRes.status);
        _active.add(role.role_id);
        _sysMsg(role.name + " 已加入聊天");
        renderRoles();
        try { sendWsMsg("add_role", { threadId: tid, role_id: role.role_id }); } catch (e) {}
      } catch (err) { console.warn("[roles] drop API failed:", err); }
    });
  }

  loadRoles();
  setTimeout(function () { refreshActive(); }, 1500);

  // agent 用 role_manage 建/删角色或把角色拉进对话时,后端广播一帧,侧栏跟着刷新 ——
  // 否则要把侧栏关掉再打开才看得到 agent 刚做的事。
  messageBus.on("roles_changed", async function () {
    await refreshActive();
    await loadRoles();
  });
}

// ── 系统消息(微信"拍了拍"风格) ──
function _sysMsg(text) {
  console.log("[roles] system-msg:", text);
  var scroll = document.getElementById("chatScroll");
  if (!scroll) { console.log("[roles] system-msg: chatScroll not found"); return; }
  var area = scroll.querySelector("#chatArea") || scroll.querySelector(".chat-area");
  if (!area) { console.log("[roles] system-msg: chatArea not found in scroll"); return; }
  var d = document.createElement("div");
  d.style.cssText = "text-align:center;padding:6px 0;margin:2px 0;";
  d.innerHTML = '<span style="color:var(--text-muted,#86868b);font-size:11px;">' + esc(text) + '</span>';
  area.appendChild(d);
  scroll.scrollTop = scroll.scrollHeight;
}

// ── REST ──
async function refreshActive() {
  const tid = state.currentConversationId;
  if (!tid) return;
  try {
    const res = await naFetch(API_BASE + "/api/v1/threads/" + tid + "/roles");
    _active = new Set(((await res.json()).roles || []).map(r => r.id));
  } catch (e) {}
}

async function loadRoles() {
  try { _roles = ((await (await naFetch(API_BASE + "/api/v1/roles")).json()).roles || []); renderRoles(); } catch (e) {}
}

export async function refreshSidebarRoles() {
  await refreshActive();
  await loadRoles();
}

// ── 编辑窗口 ──
function openEditWindow(role) {
  const isNew = !role;
  const winId = isNew ? "role-editor-new" : "role-editor-" + role.id;
  closeWindow(winId);
  const f = { name: role ? role.name : "", avatar: role ? (role.avatar || "") : "", prompt: role ? (role.system_prompt || "") : "" };

  openWindow(winId, {
    title: isNew ? "新建角色" : "编辑角色 - " + f.name,
    width: 420, height: 380,
    onMount: function (body) {
      body.innerHTML =
        '<label style="display:block;font-size:12px;color:var(--text-muted,#86868b);margin:8px 0 3px;">名称</label>' +
        '<input id="efName" maxlength="32" value="' + esc(f.name) + '" style="width:100%;padding:8px 10px;border:1px solid var(--border,#e5e5ea);border-radius:8px;font-size:13px;outline:none;">' +
        '<label style="display:block;font-size:12px;color:var(--text-muted,#86868b);margin:8px 0 3px;">头像(1-2字)</label>' +
        '<input id="efAvatar" maxlength="2" placeholder="如: 程序" value="' + esc(f.avatar) + '" style="width:100%;padding:8px 10px;border:1px solid var(--border,#e5e5ea);border-radius:8px;font-size:13px;outline:none;">' +
        '<label style="display:block;font-size:12px;color:var(--text-muted,#86868b);margin:8px 0 3px;">系统提示(system prompt)</label>' +
        '<textarea id="efPrompt" rows="5" style="width:100%;padding:8px 10px;border:1px solid var(--border,#e5e5ea);border-radius:8px;font-size:13px;outline:none;resize:vertical;min-height:80px;">' + esc(f.prompt) + '</textarea>' +
        '<div style="display:flex;gap:8px;margin-top:16px;justify-content:flex-end;">' +
        '<button id="efCancel" style="padding:8px 20px;border-radius:8px;border:1px solid var(--border,#e5e5ea);background:var(--bg-page,#f5f5f7);font-size:13px;cursor:pointer;">取消</button>' +
        '<button id="efSave" style="padding:8px 20px;border-radius:8px;border:none;background:var(--send-bg,#3b82f6);color:#fff;font-size:13px;cursor:pointer;">保存</button></div>';
      body.querySelector("#efCancel").onclick = () => closeWindow(winId);
      body.querySelector("#efSave").onclick = async () => {
        const n = body.querySelector("#efName").value.trim();
        if (!n) return;
        const url = role ? API_BASE + "/api/v1/roles/" + role.id : API_BASE + "/api/v1/roles";
        await naFetch(url, { method: role ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: n, avatar: body.querySelector("#efAvatar").value.trim(), system_prompt: body.querySelector("#efPrompt").value }) });
        closeWindow(winId); loadRoles();
      };
    }
  });
}

// ── 确认删除窗口 ──
function openConfirmDelete(role) {
  const winId = "confirm-del-" + role.id;
  openWindow(winId, {
    title: "删除角色", width: 340, height: 160,
    onMount: function (body) {
      body.style.cssText = "text-align:center;padding:24px 16px;";
      body.innerHTML =
        '<p style="margin-bottom:16px;font-size:14px;">确定删除角色<b>' + esc(role.name) + '</b>吗? 此操作不可撤销。</p>' +
        '<div style="display:flex;gap:8px;margin-top:16px;justify-content:center;">' +
        '<button id="cfNo" style="padding:8px 20px;border-radius:8px;border:1px solid var(--border,#e5e5ea);background:var(--bg-page,#f5f5f7);font-size:13px;cursor:pointer;">取消</button>' +
        '<button id="cfYes" style="padding:8px 20px;border-radius:8px;border:none;background:#dc2626;color:#fff;font-size:13px;cursor:pointer;">删除</button></div>';
      body.querySelector("#cfNo").onclick = () => closeWindow(winId);
      body.querySelector("#cfYes").onclick = async () => {
        await naFetch(API_BASE + "/api/v1/roles/" + role.id, { method: "DELETE" });
        _active.delete(role.id); closeWindow(winId); loadRoles();
      };
    }
  });
}

// ── 渲染 ──
function renderRoles() {
  const list = document.getElementById("roleList");
  if (!list) return;
  list.innerHTML = _roles.map(r => {
    const inChat = _active.has(r.id);
    return '<div class="role-card ' + (inChat ? 'joined' : 'draggable') + '" data-id="' + r.id + '" ' + (inChat ? '' : 'draggable="true"') + '>'
      + (inChat ? '' : '<span class="role-drag-handle">&#9776;</span>')
      + '<span class="role-avatar">' + esc(r.avatar || r.name[0]) + '</span>'
      + '<span class="role-name">' + esc(r.name) + '</span>'
      + (inChat ? '<span class="role-tag">对话中</span>' : '')
      + '<div class="role-actions">'
      + (inChat ? '<button class="btn-remove-chat" data-rid="' + r.id + '">离开</button>' : '')
      + '<button class="btn-edit" data-eid="' + r.id + '">编辑</button>'
      + (inChat ? '' : '<button class="btn-del" data-did="' + r.id + '">删除</button>')
      + '</div>'
      + '</div>';
  }).join("");

  list.querySelectorAll(".role-card.draggable").forEach(card => {
    card.addEventListener("dragstart", e => {
      const r = _roles.find(x => x.id === card.dataset.id);
      if (r) { e.dataTransfer.setData("application/json", JSON.stringify({ role_id: r.id, name: r.name })); e.dataTransfer.effectAllowed = "copy"; }
    });
  });
  list.querySelectorAll(".btn-edit").forEach(b => b.addEventListener("click", e => { e.stopPropagation(); openEditWindow(_roles.find(r => r.id === b.dataset.eid)); }));
  list.querySelectorAll(".btn-del").forEach(b => b.addEventListener("click", e => { e.stopPropagation(); openConfirmDelete(_roles.find(r => r.id === b.dataset.did)); }));
  list.querySelectorAll(".btn-remove-chat").forEach(b => b.addEventListener("click", async e => {
    e.stopPropagation();
    const tid = state.currentConversationId; if (!tid) return;
    await naFetch(API_BASE + "/api/v1/threads/" + tid + "/roles/" + b.dataset.rid, { method: "DELETE" });
    sendWsMsg("remove_role", { threadId: tid, role_id: b.dataset.rid });
    const r = _roles.find(x => x.id === b.dataset.rid); _active.delete(b.dataset.rid); renderRoles();
    if (r) _sysMsg(r.name + " 已离开聊天");
  }));
}

function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
