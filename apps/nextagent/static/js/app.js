import { state, getConvState, initConv, activeConv } from "./state.js";
import { API_BASE } from "./config.js";
import { naFetch } from "./http.js";
import { connect, sendWsMsg, sendReliable, enqueueOutbox, isNetworkFailure } from "./ws.js";
import { handleSendBtn, handleInputKey, autoResize, handleUploadBtn, handleFileSelect, toggleToolDropdown, closeToolDropdown, fetchSkills, toggleModelDropdown, closeModelDropdown, refreshPendingAttachmentsUi, switchComposerThread, persistComposerDraft, bindWindowFileIO, snapshotComposerSession, applyComposerSession, selectSkill } from "./input.js";
import { closeEffortDropdown, fetchModelsCatalog, initModelControls, applySessionModelState, snapshotSessionModel } from "./model-controls.js";
import { setSendBtnSend, enableAutoFollow, initScrollFollowBtn, mountChatArea } from "./chat-ui.js";
import {
  fetchConversations,
  createConversation,
  renderConversationList,
  setConversationTitle,
  focusFirstDingtalkConversation,
  switchConversation,
  markFeishuWebhookBound
} from "./conversations.js";
import { bindFeishuWebhookUi, filterAppConversations } from "./feishu-webhook-app.js";
import { togglePlanMode, stashPlanBoard, isPlanModeOn, setPlanMode } from "./plan-review.js";
import { applyConversationRemoval } from "./conversation-sync.js";
import { bindSessionUi, persistSessionUi, persistSessionUiNow, applySessionUi } from "./session-ui.js";
import { initAssets, clearAssets, connectAssetWs } from "./assets.js";
import { loadWorkspaceEnv } from "./asset-url.js";
import { initRoles } from "./roles.js";
import { initMemory, syncMemoryWindow } from "./memory.js";
import { initContextWindow, syncContextWindow } from "./context-window.js";
import { initRetrievalWindow } from "./retrieval-window.js";
import { loadHistory, reloadThreadHistory } from "./history.js";
import { loadConversationList, loadFolderList, loadCurrentConvId, saveCurrentConvId, saveConversationList, saveFolderList } from "./cache.js";
const appLayout = document.querySelector(".app-layout");
const mobileSidebarToggle = document.getElementById("mobileSidebarToggle");
const sidebarBackdrop = document.getElementById("sidebarBackdrop");
const mobileSidebar = document.getElementById("mobileSidebar");

function isMobileLayout() {
  return window.innerWidth <= 900;
}

function applySidebarState(open) {
  if (!mobileSidebar || !sidebarBackdrop) return;
  if (!isMobileLayout()) {
    mobileSidebar.style.transform = "";
    sidebarBackdrop.style.opacity = "";
    sidebarBackdrop.style.pointerEvents = "";
    return;
  }
  mobileSidebar.style.transform = open ? "translateX(0)" : "translateX(-100%)";
  sidebarBackdrop.style.opacity = open ? "1" : "0";
  sidebarBackdrop.style.pointerEvents = open ? "auto" : "none";
}

function setSidebarOpen(open) {
  if (!appLayout) return;
  appLayout.classList.toggle("sidebar-open", open);
  if (mobileSidebar) {
    mobileSidebar.classList.toggle("is-open", open);
  }
  if (sidebarBackdrop) {
    sidebarBackdrop.classList.toggle("is-open", open);
  }
  if (mobileSidebarToggle) {
    mobileSidebarToggle.setAttribute("aria-expanded", open ? "true" : "false");
  }
  applySidebarState(open);
}

function closeSidebar() {
  if (!isMobileLayout()) return;
  setSidebarOpen(false);
}

function toggleSidebar(e) {
  if (e) {
    e.preventDefault();
    e.stopPropagation();
  }
  const willOpen = !mobileSidebar.classList.contains("is-open");
  setSidebarOpen(willOpen);
}

if (mobileSidebarToggle) {
  mobileSidebarToggle.onclick = toggleSidebar;
  mobileSidebarToggle.ontouchstart = toggleSidebar;
}

if (sidebarBackdrop) {
  sidebarBackdrop.onclick = closeSidebar;
}

window.addEventListener("resize", function () {
  if (!isMobileLayout()) {
    setSidebarOpen(false);
    applySidebarState(false);
    return;
  }
  applySidebarState(mobileSidebar && mobileSidebar.classList.contains("is-open"));
});

state.api = {
  sendWsMsg: sendWsMsg,
  sendReliable: sendReliable,
  enqueueOutbox: enqueueOutbox,
  isNetworkFailure: isNetworkFailure,
  reloadThreadHistory: reloadThreadHistory,
  fetchSkills: fetchSkills
};

function paintMediaPassthroughBtn() {
  var btn = document.getElementById("mediaPassthroughBtn");
  if (btn) btn.classList.toggle("active", !!state.mediaPassthroughEnabled);
}

function paintAgentSpeakBtn() {
  var btn = document.getElementById("agentSpeakBtn");
  if (!btn) return;
  btn.classList.toggle("active", !!state.agentSpeak);
  btn.setAttribute("aria-pressed", state.agentSpeak ? "true" : "false");
}

bindSessionUi({
  collect: function () {
    var model = snapshotSessionModel();
    var composer = snapshotComposerSession();
    return {
      planMode: isPlanModeOn(),
      agentProfile: model.agentProfile,
      thinking: model.thinking,
      reasoningEffort: model.reasoningEffort,
      mediaPassthrough: !!state.mediaPassthroughEnabled,
      agentSpeak: state.agentSpeak !== false,
      selectedSkill: state.selectedSkill || "",
      composerText: composer.composerText,
      attachments: composer.attachments,
    };
  },
  apply: function (ui) {
    setPlanMode(!!ui.planMode);
    applySessionModelState(ui);
    state.mediaPassthroughEnabled = !!ui.mediaPassthrough;
    paintMediaPassthroughBtn();
    state.agentSpeak = ui.agentSpeak !== false;
    paintAgentSpeakBtn();
    selectSkill(ui.selectedSkill || null);
    applyComposerSession(ui.composerText || "", ui.attachments || []);
  },
});
initModelControls();
var mediaPassthroughBtn = document.getElementById("mediaPassthroughBtn");
if (mediaPassthroughBtn) {
  paintMediaPassthroughBtn();
  mediaPassthroughBtn.onclick = function (e) {
    e.stopPropagation();
    state.mediaPassthroughEnabled = !state.mediaPassthroughEnabled;
    paintMediaPassthroughBtn();
    persistSessionUi();
  };
}
var agentSpeakBtn = document.getElementById("agentSpeakBtn");
if (agentSpeakBtn) {
  paintAgentSpeakBtn();
  agentSpeakBtn.onclick = function (e) {
    e.stopPropagation();
    state.agentSpeak = !state.agentSpeak;
    paintAgentSpeakBtn();
    persistSessionUi();
  };
}
document.getElementById("toolBtn").onclick = function (e) {
  e.stopPropagation();
  toggleToolDropdown();
};
document.getElementById("modelBtn").onclick = function (e) {
  e.stopPropagation();
  toggleModelDropdown();
};
document.addEventListener("click", function () {
  closeToolDropdown();
  closeModelDropdown();
  closeEffortDropdown();
});
document.getElementById("sendBtn").onclick = handleSendBtn;
document.getElementById("uploadBtn").onclick = handleUploadBtn;
var planModeBtn = document.getElementById("planModeBtn");
if (planModeBtn) {
  planModeBtn.onclick = function (e) {
    e.stopPropagation();
    togglePlanMode();
  };
}
document.getElementById("fileInput").onchange = handleFileSelect;
var msgInput = document.getElementById("msgInput");
msgInput.onkeydown = handleInputKey;
msgInput.oninput = function () {
  autoResize(msgInput);
  persistComposerDraft();
};
bindWindowFileIO();
bindFeishuWebhookUi(markFeishuWebhookBound);
document.getElementById("newConversationBtn").onclick = function () {
  persistSessionUiNow().then(function () {
    return createConversation();
  }).then(function (res) {
    var chatScroll = document.getElementById("chatScroll");
    var currentArea = chatScroll.querySelector(".chat-area");
    if (currentArea) chatScroll.removeChild(currentArea);

    return stashPlanBoard().then(function () {
      switchComposerThread(res.id);
      state.currentConversationId = res.id;
      state.currentConversationTitle = res.title || "\u65b0\u5bf9\u8bdd";
      if (state.currentUserId) {
        saveCurrentConvId(state.currentUserId, res.id);
        saveConversationList(state.currentUserId, res.conversations);
        saveFolderList(state.currentUserId, res.folders || []);
      }
      sendWsMsg("thread_focus", { threadId: res.id });
      var cs = getConvState(res.id);
      mountChatArea(cs.el);
      applySessionUi({});
      persistSessionUiNow();
      syncMemoryWindow();
      syncContextWindow();

      setSendBtnSend();
      setConversationTitle(state.currentConversationTitle);
      renderConversationList(res.conversations, res.id, res.folders);
      clearAssets();
      connectAssetWs(res.id);
      enableAutoFollow();
      closeSidebar();
    });
  });
};

document.getElementById("conversationList").onclick = function (e) {
  if (e.target.closest(".sidebar-item")) {
    closeSidebar();
  }
};

function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text);
  }
  var ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.left = "-9999px";
  document.body.appendChild(ta);
  ta.select();
  document.execCommand("copy");
  document.body.removeChild(ta);
  return Promise.resolve();
}

document.addEventListener("click", function (e) {
  const downloadBtn = e.target.closest(".tool-code-download");
  const copyBtn = downloadBtn ? null : e.target.closest(".tool-code-copy");
  if (copyBtn) {
    const wrap = copyBtn.closest(".tool-code-card");
    const codeEl = wrap && wrap.querySelector("pre code");
    if (codeEl) {
      copyText(codeEl.textContent).then(function () {
        copyBtn.textContent = "已复制";
        setTimeout(function () { copyBtn.textContent = "复制"; }, 1500);
      });
    }
    return;
  }
  if (downloadBtn) {
    const wrap = downloadBtn.closest(".tool-code-card");
    const codeEl = wrap && wrap.querySelector("pre code");
    const lang = wrap && wrap.querySelector(".tool-code-title");
    if (codeEl) {
      const ext = { python: "py", javascript: "js", typescript: "ts", java: "java", cpp: "cpp", c: "c", go: "go", rust: "rs", sql: "sql" }[lang ? lang.textContent.toLowerCase() : ""] || "txt";
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([codeEl.textContent], { type: "text/plain" }));
      a.download = "code." + ext;
      a.click();
      URL.revokeObjectURL(a.href);
    }
  }
});

setSidebarOpen(false);

initScrollFollowBtn();

function restoreFromCache(user) {
  if (!user || !user.userId) return;
  var userId = user.userId;
  var cachedList = loadConversationList(userId) || [];
  var cachedCurrentId = loadCurrentConvId(userId) || "";
  var visibleCache = filterAppConversations(cachedList);
  if (!visibleCache.length) return;
  if (!cachedCurrentId && visibleCache.length > 0) {
    var activeItem = visibleCache.find(function (item) { return item && item.active; });
    cachedCurrentId = activeItem ? String(activeItem.id || "") : String(visibleCache[0].id || "");
  }
  if (cachedCurrentId && !visibleCache.some(function (c) { return String(c.id) === String(cachedCurrentId); })) {
    cachedCurrentId = String(visibleCache[0].id);
  }
  if (!cachedCurrentId) return;

  state.currentConversationId = cachedCurrentId;
  var existingArea = document.getElementById("chatArea");
  if (existingArea) {
    existingArea.removeAttribute("id");
    initConv(cachedCurrentId, existingArea);
  } else {
    var chatScroll = document.getElementById("chatScroll");
    var cs = getConvState(cachedCurrentId);
    if (chatScroll && cs && cs.el && !chatScroll.contains(cs.el)) {
      mountChatArea(cs.el);
    }
  }

  if (cachedList.length > 0) {
    renderConversationList(cachedList, cachedCurrentId, loadFolderList(userId));
  }
  connectAssetWs(cachedCurrentId);
  switchComposerThread(cachedCurrentId);
}

window.addEventListener("na:threads-deleted", function (event) {
  var detail = event && event.detail;
  if (!detail || !detail.deletedThreads || !detail.deletedThreads.length) return;
  applyConversationRemoval(detail.deletedThreads, detail);
});

loadWorkspaceEnv()
  .then(function () { return naFetch(API_BASE + "/api/v1/auth/me"); })
  .then(function (r) { return r.json(); })
  .then(function (res) {
    if (res.ok && res.user) {
      state.currentUser = res.user;
      state.currentUserId = res.user.userId;
      console.log("[init] 用户身份:", res.user.userId);
      initAssets();
      try { initRoles(); } catch (e) { console.warn("[init] roles init failed:", e); }
      initMemory();
      initContextWindow();
      initRetrievalWindow();
      fetchConversations().then(function (convRes) {
        state.httpInitDone = true;
        console.log("[init] 获取会话列表:", convRes);
        var visible = filterAppConversations(convRes.conversations || []);
        if (!visible.length) {
          return createConversation().then(function (created) {
            return { conversations: created.conversations, folders: created.folders, currentId: created.id };
          });
        }
        var cur = convRes.currentId;
        if (!visible.some(function (c) { return String(c.id) === String(cur); })) {
          cur = visible[0].id;
          return switchConversation(cur).then(function () {
            return { conversations: convRes.conversations, folders: convRes.folders, currentId: cur };
          });
        }
        return { conversations: convRes.conversations, folders: convRes.folders, currentId: cur };
      }).then(function (convRes) {
        if (convRes && convRes.currentId) {
          state.currentConversationId = convRes.currentId;
          console.log("[init] 设置当前会话:", convRes.currentId);
          if (state.currentUserId) saveCurrentConvId(state.currentUserId, convRes.currentId);
          if (state.currentUserId) saveFolderList(state.currentUserId, convRes.folders || []);
          renderConversationList(convRes.conversations, convRes.currentId, convRes.folders);
          if (new URLSearchParams(location.search).get("dingtalk") === "1") {
            focusFirstDingtalkConversation();
          }
          var chatScroll = document.getElementById("chatScroll");
          var existingArea = document.getElementById("chatArea");
          if (existingArea) {
            existingArea.removeAttribute("id");
            initConv(convRes.currentId, existingArea);
          } else {
            var cs = getConvState(convRes.currentId);
            mountChatArea(cs.el);
          }
          connectAssetWs(convRes.currentId);
          switchComposerThread(convRes.currentId);
        } else {
          console.log("[init] 无当前会话，使用缓存恢复");
          restoreFromCache(res.user);
        }
        connect();
      }).catch(function (err) {
        console.error("[init] 获取会话列表失败:", err);
        restoreFromCache(res.user);
        connect();
      });
      fetchSkills();
      fetchModelsCatalog().catch(function (err) {
        console.error("[init] 模型列表加载失败:", err);
      });
    } else {
      throw new Error("/api/v1/auth/me 返回异常: " + JSON.stringify(res));
    }
  })
  .catch(function (err) {
    console.error("[init] 获取用户身份失败:", err);
  });
