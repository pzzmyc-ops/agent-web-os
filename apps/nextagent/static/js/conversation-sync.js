import { state, getConvState, deleteConv, convCache } from "./state.js";
import { saveCurrentConvId, saveConversationList, saveFolderList } from "./cache.js";
import { setSendBtnSend, setSendBtnStop, enableAutoFollow, syncSendButton, mountChatArea } from "./chat-ui.js";
import { connectAssetWs } from "./assets.js";
import { sendWsMsg } from "./ws.js";
import { discardPendingForThread, refreshPendingAttachmentsUi, switchComposerThread } from "./input.js";
import { findChatInflightTask } from "./composer.js";
import { dismissPlanReview, applyPlanDoc } from "./plan-review.js";
import { applySessionUi } from "./session-ui.js";
import { syncMemoryWindow } from "./memory.js";
import { syncContextWindow } from "./context-window.js";

function swapChatArea(targetConvId) {
  var chatScroll = document.getElementById("chatScroll");
  var currentArea = chatScroll.querySelector(".chat-area");
  if (currentArea) chatScroll.removeChild(currentArea);
  var targetCs = getConvState(targetConvId);
  mountChatArea(targetCs.el);
}

function getTitleById(list, convId) {
  var targetId = convId == null ? "" : String(convId);
  var matched = (list || []).find(function (item) {
    return String(item.id) === targetId;
  });
  return matched ? (matched.title || "新对话") : "新对话";
}

export function applyConversationRemoval(deletingIds, res) {
  var ids = Array.isArray(deletingIds) ? deletingIds : [deletingIds];
  var idSet = {};
  ids.forEach(function (id) {
    idSet[String(id)] = true;
  });

  ids.forEach(function (deletingId) {
    var delCs = convCache[deletingId];
    var taskId = (delCs && delCs.currentTaskId) || findChatInflightTask(deletingId);
    if (taskId) {
      sendWsMsg("task_control", { action: "cancel", taskId: String(taskId) });
    }
    deleteConv(deletingId);
    discardPendingForThread(deletingId);
  });

  var currentWasDeleted = !state.currentConversationId
    || idSet[String(state.currentConversationId)];

  if (currentWasDeleted) dismissPlanReview();
  if (currentWasDeleted && res && res.currentId) {
    state.currentConversationId = res.currentId;
    if (state.currentUserId) saveCurrentConvId(state.currentUserId, res.currentId);
    state.currentConversationTitle = getTitleById(res.conversations, res.currentId);
    import("./conversations.js").then(function (mod) {
      mod.setConversationTitle(state.currentConversationTitle);
    });
    swapChatArea(res.currentId);
    switchComposerThread(res.currentId);
    refreshPendingAttachmentsUi();
    var targetCs = getConvState(res.currentId);
    syncSendButton();
    connectAssetWs(res.currentId);
    if (!targetCs.historyLoaded) {
      import("./history.js").then(function (mod) {
        mod.loadHistory(res.currentId).then(enableAutoFollow);
      });
    } else {
      applySessionUi(targetCs.sessionUi || {});
      applyPlanDoc(targetCs.planDoc || null);
    }
    syncMemoryWindow();
    syncContextWindow();
  } else if (currentWasDeleted) {
    state.currentConversationId = null;
    switchComposerThread("");
    state.currentConversationTitle = "新对话";
    import("./conversations.js").then(function (mod) {
      mod.setConversationTitle(state.currentConversationTitle);
    });
    syncMemoryWindow();
    syncContextWindow();
  }

  import("./conversations.js").then(function (mod) {
    if (res && res.conversations) {
      if (state.currentUserId) {
        saveConversationList(state.currentUserId, res.conversations);
        if (res.folders) saveFolderList(state.currentUserId, res.folders);
      }
      mod.renderConversationList(res.conversations, state.currentConversationId, res.folders);
    } else {
      mod.fetchConversations().then(function (freshRes) {
        if (state.currentUserId) {
          saveConversationList(state.currentUserId, freshRes.conversations);
          saveFolderList(state.currentUserId, freshRes.folders || []);
        }
        mod.renderConversationList(freshRes.conversations, state.currentConversationId, freshRes.folders);
      });
    }
  });
}
