import { state, activeConv, taskMirror, taskToConv, getConvState } from "./state.js";
import { addUserBubble, addSystemMsg, getChatAreaEl, enableAutoFollow, syncCurrentPageThinkToggle, setSendBtnSend, setSendBtnUploading, syncSendButton, markUserBubbleFailed, markUserBubbleQueued, markUserBubbleSuperseded, sendFailureText } from "./chat-ui.js";
import { showTurnWaiting, removeTurnWaiting } from "./blocks-renderer.js";
import { sendWsMsg, sendReliable, enqueueOutbox, isNetworkFailure } from "./ws.js";
import { syncComposerForConv } from "./composer.js";
import { API_BASE } from "./config.js";
import { joinPath, resolveFileUrl, resolveWorkspacePath } from "./asset-url.js";
import { naFetch } from "./http.js";
import { uploadAndRegisterAsset } from "./upload.js";
import { logAssetUrl } from "./asset-diag.js";
import { getCurrentPath as getAssetBrowsingPath, resolveUploadParentPath, resolveChatUploadParentPath, applyNodeFromApi, refreshAfterAssetMutation } from "./modules/asset-system/asset-store.js";
import { closeEffortDropdown, getSendModelParams, getModelDropdownProfiles, selectAgentProfile } from "./model-controls.js";
import { abandonPlan, isPlanModeOn } from "./plan-review.js";
import { persistSessionUi } from "./session-ui.js";
import { discardRevertedRows } from "./checkpoints.js";

export var NEXTAGENT_ASSET_REFS_DRAG_TYPE = "application/x-nextagent-asset-refs";
var FM_PATHS_DRAG_TYPE = "application/x-fm-paths";

var pendingAttachments = [];
var _isSending = false;
var _composerBoundThreadId = "";

export function persistComposerDraft() {
  persistSessionUi();
}

export function switchComposerThread(nextThreadId) {
  _composerBoundThreadId = String(nextThreadId || "").trim();
}

function _clearComposerDraft() {
  var input = document.getElementById("msgInput");
  if (input) {
    input.value = "";
    autoResize(input);
  }
  clearPendingAttachments();
}

function cancelTask(taskId, source) {
  var cs = activeConv();
  var sent = taskId ? String(taskId) : "";
  if (!sent) return;
  if (taskMirror[sent]) {
    taskMirror[sent].cancelRequested = true;
  }
  if (cs) {
    cs.pendingSend = false;
    syncComposerForConv(cs, "user_cancel");
    syncSendButton();
  }
  sendWsMsg("task_control", {
    action: "cancel",
    taskId: sent,
    threadId: cs && cs.id ? cs.id : state.currentConversationId || "",
  });
  abandonPlan();
}

function formatFileSize(bytes) {
  if (bytes < 1024) return bytes + "B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + "KB";
  return (bytes / (1024 * 1024)).toFixed(1) + "MB";
}

function isImageMime(mime) {
  return /^image\//i.test(mime || "");
}

function _inferMimeFromFilename(filename, assetType) {
  var n = String(filename || "").toLowerCase();
  var dot = n.lastIndexOf(".");
  var ext = dot >= 0 ? n.slice(dot) : "";
  var map = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
  };
  if (map[ext]) return map[ext];
  if (assetType === "image") return "image/png";
  if (assetType === "video") return "video/mp4";
  if (assetType === "audio") return "audio/mpeg";
  return "application/octet-stream";
}

function getFileIcon(mime) {
  if (/^audio\//.test(mime)) return "\u{1F3B5}";
  if (/^video\//.test(mime)) return "\u{1F3AC}";
  if (/pdf/.test(mime)) return "\u{1F4C4}";
  if (/spreadsheet|excel|csv/.test(mime)) return "\u{1F4CA}";
  if (/presentation|powerpoint/.test(mime)) return "\u{1F4CA}";
  if (/word|document|rtf|odt/.test(mime)) return "\u{1F4DD}";
  return "\u{1F4CE}";
}

var PROGRESS_CIRCUMFERENCE = 2 * Math.PI * 14;

function _hasUploading() {
  return pendingAttachments.some(function (a) { return a._uploading; });
}

function _hasError() {
  return pendingAttachments.some(function (a) { return !!a._error; });
}

function _syncSendBtn() {
  var cs = activeConv();
  if (cs && cs.isStreaming) return;
  if (cs && cs.pendingSend) {
    syncSendButton();
    return;
  }
  if (_isSending) return;
  if (_hasUploading()) {
    setSendBtnUploading();
  } else {
    setSendBtnSend();
  }
}

function _uploadParentPath() {
  return resolveChatUploadParentPath();
}

function _bubbleAttachmentFromPending(att) {
  var r = att._uploadResult;
  var mediaId = r ? r.media_id : "";
  var previewUrl = "";
  if (isImageMime(att.mime)) {
    var src = att.previewUrl || att.dataUrl || "";
    if (src.indexOf("data:") !== 0 || src.length <= 512000) previewUrl = src;
    else if (mediaId) previewUrl = resolveFileUrl({ media_id: mediaId, mime_type: att.mime, filename: att.name });
  } else if (mediaId) {
    previewUrl = resolveFileUrl({ media_id: mediaId, mime_type: att.mime, filename: att.name });
  } else if (att.previewUrl && att.previewUrl.indexOf("data:") !== 0) {
    previewUrl = att.previewUrl;
  }
  return {
    name: att.name,
    mime: att.mime,
    size: att.size,
    previewUrl: previewUrl,
    media_id: mediaId,
  };
}

function addAttachment(file, relativePath) {
  var threadId = state.currentConversationId;
  if (!threadId) {
    addSystemMsg("请先创建或选择对话");
    return;
  }

  var rel = String(relativePath || file.webkitRelativePath || file.name || "clipboard.bin").replace(/\\/g, "/");
  var baseName = rel.split("/").pop() || file.name || "clipboard.bin";
  var subdir = rel.split("/").slice(0, -1).filter(Boolean).join("/");
  var uploadParentPath = _uploadParentPath();
  if (subdir) {
    uploadParentPath = joinPath(uploadParentPath, subdir);
  }

  var att = {
    file: file,
    name: baseName,
    size: file.size,
    mime: file.type || "application/octet-stream",
    dataUrl: null,
    _uploading: true,
    _progress: 0,
    _uploadResult: null,
    _error: null,
    _uploadHandle: null,
  };
  pendingAttachments.push(att);
  persistComposerDraft();

  if (isImageMime(att.mime)) {
    var reader = new FileReader();
    reader.onload = function (e) {
      att.dataUrl = e.target.result;
      renderAttachmentPreviews();
    };
    reader.readAsDataURL(file);
  }

  var handle = uploadAndRegisterAsset(file, {
    parentPath: uploadParentPath,
    onProgress: function (ratio) {
      att._progress = ratio;
      _updateAttachmentProgressDom(att);
    },
  });
  att._uploadHandle = handle;

  handle
    .then(function (result) {
      att._uploading = false;
      att._progress = 1;
      att._uploadResult = result;
      if (result && result.node) applyNodeFromApi(result.node);
      var paths = [];
      if (result && result.node && result.node.path) paths.push(result.node.path);
      paths.push(uploadParentPath);
      refreshAfterAssetMutation(threadId, paths, uploadParentPath);
      renderAttachmentPreviews();
      _syncSendBtn();
      persistComposerDraft();
    })
    .catch(function (err) {
      if (err.message === "cancelled") {
        var pos = pendingAttachments.indexOf(att);
        if (pos !== -1) pendingAttachments.splice(pos, 1);
      } else {
        att._uploading = false;
        att._error = err.message;
      }
      renderAttachmentPreviews();
      _syncSendBtn();
      persistComposerDraft();
    });

  renderAttachmentPreviews();
  _syncSendBtn();
}

export function addPendingRefsFromExistingMedia(refs) {
  if (!refs || !refs.length) return;
  var threadId = state.currentConversationId;
  if (!threadId) {
    addSystemMsg("请先创建或选择对话");
    return;
  }
  for (var i = 0; i < refs.length; i++) {
    var r = refs[i];
    var mediaId = String(r.media_id || "").trim();
    var path = String(r.path || r.id || "").trim();
    var attachmentRef = resolveWorkspacePath({ path: path, id: path, media_id: mediaId }) || path || mediaId;
    if (!attachmentRef) continue;
    var filename = String(r.filename || r.name || "file").trim() || "file";
    var mime = String(r.mime_type || "").trim() || _inferMimeFromFilename(filename, r.asset_type);
    var size = typeof r.size === "number" && r.size >= 0 ? r.size : 0;
    var previewNode = {
      media_id: attachmentRef,
      path: path,
      signed_url: r.signed_url || "",
      thumb_url: r.thumb_url || "",
      previewUrl: r.previewUrl || r.url || "",
    };
    var att = {
      file: null,
      name: filename,
      size: size,
      mime: mime,
      dataUrl: null,
      previewUrl: isImageMime(mime) ? resolveFileUrl(previewNode) : "",
      _fromAsset: true,
      _uploading: false,
      _progress: 1,
      _uploadResult: {
        media_id: attachmentRef,
        filename: filename,
        mime_type: mime,
        size: size,
      },
      _error: null,
      _uploadHandle: null,
    };
    pendingAttachments.push(att);
  }
  renderAttachmentPreviews();
  _syncSendBtn();
  persistComposerDraft();
  var msgInput = document.getElementById("msgInput");
  if (msgInput) msgInput.focus();
}

function removeAttachment(index) {
  var att = pendingAttachments[index];
  if (att && att._uploadHandle && att._uploading) {
    att._uploadHandle.cancel();
  }
  pendingAttachments.splice(index, 1);
  renderAttachmentPreviews();
  _syncSendBtn();
  persistComposerDraft();
}

function _updateAttachmentProgressDom(att) {
  var idx = pendingAttachments.indexOf(att);
  if (idx === -1) return;
  var container = document.getElementById("attachmentPreview");
  if (!container) return;
  var items = container.querySelectorAll(".attachment-item");
  if (idx >= items.length) return;
  var item = items[idx];
  var fill = item.querySelector(".att-progress-fill");
  if (fill) {
    fill.style.strokeDashoffset = PROGRESS_CIRCUMFERENCE * (1 - att._progress);
  }
  var sizeEl = item.querySelector(".attachment-size");
  if (sizeEl && att._uploading) {
    sizeEl.textContent = Math.round(att._progress * 100) + "%";
  }
}

function renderAttachmentPreviews() {
  var container = document.getElementById("attachmentPreview");
  if (!container) return;
  container.innerHTML = "";

  if (pendingAttachments.length === 0) {
    container.style.display = "none";
    return;
  }
  container.style.display = "flex";

  var imgCounter = 0;
  for (var i = 0; i < pendingAttachments.length; i++) {
    if (isImageMime(pendingAttachments[i].mime)) imgCounter++;
  }
  var imgSeq = 0;
  for (var i = 0; i < pendingAttachments.length; i++) {
    (function (idx) {
      var att = pendingAttachments[idx];
      var item = document.createElement("div");
      item.className = "attachment-item";

      if (isImageMime(att.mime)) {
        imgSeq++;
        var thumb = document.createElement("img");
        thumb.className = "attachment-thumb";
        thumb.src = att.previewUrl || att.dataUrl || "";
        item.appendChild(thumb);
        if (imgCounter > 1) {
          var badge = document.createElement("span");
          badge.className = "img-index-badge";
          badge.textContent = imgSeq;
          item.appendChild(badge);
        }
      } else {
        var icon = document.createElement("div");
        icon.className = "attachment-file-icon";
        icon.textContent = getFileIcon(att.mime);
        item.appendChild(icon);
      }

      var info = document.createElement("div");
      info.className = "attachment-info";
      var nameEl = document.createElement("div");
      nameEl.className = "attachment-name";
      nameEl.textContent = att.name;
      nameEl.title = att.name;
      var sizeEl = document.createElement("div");
      sizeEl.className = "attachment-size";
      if (att._uploading) {
        sizeEl.textContent = Math.round(att._progress * 100) + "%";
      } else if (att._error) {
        sizeEl.textContent = "失败";
        sizeEl.classList.add("attachment-size-error");
      } else {
        sizeEl.textContent = formatFileSize(att.size);
      }
      info.appendChild(nameEl);
      info.appendChild(sizeEl);
      item.appendChild(info);

      if (att._uploading) {
        item.classList.add("uploading");
        var overlay = document.createElement("div");
        overlay.className = "attachment-upload-overlay";
        overlay.innerHTML =
          '<svg class="att-progress-ring" viewBox="0 0 36 36">' +
          '<circle class="att-progress-bg" cx="18" cy="18" r="14"/>' +
          '<circle class="att-progress-fill" cx="18" cy="18" r="14" style="stroke-dashoffset:' +
          (PROGRESS_CIRCUMFERENCE * (1 - att._progress)) + '"/>' +
          '</svg>';
        item.appendChild(overlay);
      }

      if (att._error) {
        item.classList.add("upload-error");
      }

      var delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className = "attachment-del";
      delBtn.textContent = "\u00D7";
      delBtn.title = att._uploading ? "取消上传" : "删除";
      delBtn.onclick = function (e) {
        e.stopPropagation();
        removeAttachment(idx);
      };
      item.appendChild(delBtn);

      container.appendChild(item);
    })(i);
  }
}

export function getPendingAttachments() {
  return pendingAttachments.slice();
}

export function snapshotComposerSession() {
  var input = document.getElementById("msgInput");
  var attachments = [];
  for (var i = 0; i < pendingAttachments.length; i++) {
    var att = pendingAttachments[i];
    if (att._uploading || att._error) continue;
    var r = att._uploadResult;
    if (!r) continue;
    var path = r.node && r.node.path ? String(r.node.path) : "";
    var mediaId = String(r.media_id || "").trim();
    if (!mediaId && !path) continue;
    attachments.push({
      media_id: mediaId || path,
      path: path,
      filename: att.name || r.filename || "",
      mime_type: att.mime || r.mime_type || "",
      size: typeof att.size === "number" ? att.size : 0,
      signed_url: att.previewUrl || "",
      previewUrl: att.previewUrl || att.dataUrl || "",
    });
  }
  return {
    composerText: input ? String(input.value || "") : "",
    attachments: attachments,
  };
}

export function applyComposerSession(text, attachments) {
  for (var i = 0; i < pendingAttachments.length; i++) {
    var att = pendingAttachments[i];
    if (att && att._uploadHandle && att._uploading) {
      att._uploadHandle.cancel();
    }
  }
  pendingAttachments.length = 0;
  var input = document.getElementById("msgInput");
  if (input) {
    input.value = text || "";
    autoResize(input);
  }
  if (attachments && attachments.length) {
    restoreComposerAttachments(attachments);
  }
  renderAttachmentPreviews();
  _syncSendBtn();
}

function restoreComposerAttachments(refs) {
  for (var i = 0; i < refs.length; i++) {
    var r = refs[i];
    var mediaId = String(r.media_id || "").trim();
    var path = String(r.path || r.id || "").trim();
    var attachmentRef = resolveWorkspacePath({ path: path, id: path, media_id: mediaId }) || path || mediaId;
    if (!attachmentRef) continue;
    var filename = String(r.filename || r.name || "file").trim() || "file";
    var mime = String(r.mime_type || "").trim() || _inferMimeFromFilename(filename, r.asset_type);
    var size = typeof r.size === "number" && r.size >= 0 ? r.size : 0;
    var previewNode = {
      media_id: attachmentRef,
      path: path,
      signed_url: r.signed_url || "",
      thumb_url: r.thumb_url || "",
      previewUrl: r.previewUrl || r.url || "",
    };
    pendingAttachments.push({
      file: null,
      name: filename,
      size: size,
      mime: mime,
      dataUrl: null,
      previewUrl: isImageMime(mime) ? resolveFileUrl(previewNode) : "",
      _fromAsset: true,
      _uploading: false,
      _progress: 1,
      _uploadResult: {
        media_id: attachmentRef,
        filename: filename,
        mime_type: mime,
        size: size,
      },
      _error: null,
      _uploadHandle: null,
    });
  }
}

export function clearPendingAttachments() {
  for (var i = 0; i < pendingAttachments.length; i++) {
    var att = pendingAttachments[i];
    if (att._uploadHandle && att._uploading) {
      att._uploadHandle.cancel();
    }
  }
  pendingAttachments.length = 0;
  renderAttachmentPreviews();
  _syncSendBtn();
}

export function discardPendingForThread(threadId) {
}

export function refreshPendingAttachmentsUi() {
  renderAttachmentPreviews();
  _syncSendBtn();
}

function handleTextSendFailure(threadId, bubbleRow, msgData, reason) {
  var cs = getConvState(threadId);
  if (cs) {
    removeTurnWaiting(cs);
    cs.pendingSend = false;
    cs.outboxFlushing = false;
    syncComposerForConv(cs, "send_failed");
  }
  syncSendButton();
  if (isNetworkFailure(reason)) {
    markUserBubbleQueued(bubbleRow);
    enqueueOutbox(threadId, function () {
      resendQueuedText(threadId, msgData);
    }, function () {
      markUserBubbleSuperseded(bubbleRow);
    });
    return;
  }
  markUserBubbleFailed(bubbleRow, reason, function () {
    retryTextSend(threadId, bubbleRow, msgData);
  });
}

function resendQueuedText(threadId, msgData) {
  var cs = getConvState(threadId);
  cs.outboxFlushing = true;
  addSystemMsg("网络已恢复，正在重新发送消息", cs.el);
  showTurnWaiting(cs, "outbox_flush");
  syncComposerForConv(cs, "outbox_flush");
  syncSendButton();
  sendReliable("text", msgData, {
    onFail: function (reason) {
      handleQueuedTextFailure(threadId, msgData, reason);
    }
  });
}

function handleQueuedTextFailure(threadId, msgData, reason) {
  var cs = getConvState(threadId);
  removeTurnWaiting(cs);
  cs.outboxFlushing = false;
  syncComposerForConv(cs, "outbox_resend_failed");
  syncSendButton();
  if (isNetworkFailure(reason)) {
    enqueueOutbox(threadId, function () {
      resendQueuedText(threadId, msgData);
    });
    addSystemMsg("网络仍未恢复，消息继续排队等待发送", cs.el);
    return;
  }
  addSystemMsg("消息发送失败：" + sendFailureText(reason), cs.el);
}

function retryTextSend(threadId, bubbleRow, msgData) {
  var cs = getConvState(threadId);
  if (cs) {
    cs.pendingSend = true;
    showTurnWaiting(cs, "send_retry");
    syncComposerForConv(cs, "send_retry");
    syncSendButton();
  }
  sendReliable("text", msgData, {
    onFail: function (reason) {
      handleTextSendFailure(threadId, bubbleRow, msgData, reason);
    }
  });
}

export function sendPlainText(text) {
  var raw = String(text || "").trim();
  if (!raw) throw new Error("sendPlainText empty");
  if (_isSending) throw new Error("sendPlainText busy");
  if (_hasUploading()) throw new Error("sendPlainText uploading");
  var cs = activeConv();
  if (cs && (cs.isStreaming || cs.pendingSend)) {
    throw new Error("sendPlainText turn active");
  }
  _isSending = true;
  try {
    var msgData = {
      content: raw,
      search: state.searchEnabled,
      mediaPassthrough: !!state.mediaPassthroughEnabled,
      threadId: state.currentConversationId,
      asset_browsing_path: getAssetBrowsingPath() || resolveUploadParentPath(),
    };
    Object.assign(msgData, getSendModelParams());
    msgData.planMode = isPlanModeOn();
    enableAutoFollow();
    discardRevertedRows(cs);
    var bubbleRow = addUserBubble(raw, []);
    if (cs) {
      cs.pendingSend = true;
      showTurnWaiting(cs, "send_message");
      syncComposerForConv(cs, "send_message");
      syncSendButton();
    }
    var sendThreadId = msgData.threadId;
    sendReliable("text", msgData, {
      onFail: function (reason) {
        handleTextSendFailure(sendThreadId, bubbleRow, msgData, reason);
      }
    });
  } finally {
    _isSending = false;
    _syncSendBtn();
  }
}

export function sendText() {
  var gate = document.getElementById("feishuWebhookGate");
  if (gate && !gate.hidden) return;
  if (_isSending) return;
  if (_hasUploading()) return;
  var cs = activeConv();
  var input = document.getElementById("msgInput");
  var text = input.value.trim();
  var hasAttachments = pendingAttachments.length > 0;
  if (!text && !hasAttachments) return;
  if (cs && (cs.isStreaming || cs.pendingSend)) {
    return;
  }

  if (_hasError()) {
    addSystemMsg("部分附件上传失败,请删除后重试");
    return;
  }

  _isSending = true;
  try {
  var attachments = [];
  var bubbleAttachments = [];

  for (var i = 0; i < pendingAttachments.length; i++) {
    var att = pendingAttachments[i];
    var r = att._uploadResult;
    if (r) {
      var attPath = resolveWorkspacePath(r.node || { path: r.path, media_id: r.media_id }) || r.media_id;
      attachments.push({
        media_id: r.media_id,
        path: attPath,
        filename: att.name || r.filename,
        mime_type: r.mime_type,
        size: r.size,
      });
    }
    bubbleAttachments.push(_bubbleAttachmentFromPending(att));
  }

  var msgData = {
    content: text,
    search: state.searchEnabled,
    mediaPassthrough: !!state.mediaPassthroughEnabled,
    threadId: state.currentConversationId,
    asset_browsing_path: getAssetBrowsingPath() || resolveUploadParentPath(),
  };
  Object.assign(msgData, getSendModelParams());
  msgData.planMode = isPlanModeOn();
  if (state.selectedSkill) {
    msgData.skill = state.selectedSkill;
  }
  if (attachments.length > 0) {
    msgData.attachments = attachments.map(function (a) {
      return { media_id: a.media_id, path: a.path, filename: a.filename, mime_type: a.mime_type };
    });
    for (var ai = 0; ai < bubbleAttachments.length; ai++) {
      var ba = bubbleAttachments[ai];
      logAssetUrl("send_user_attachments", {
        mediaId: ba.media_id || "",
        mime: ba.mime || "",
        filename: ba.name || "",
        srcKind: ba.dataUrl ? "data_url" : ba.previewUrl ? "preview_url" : "empty",
        srcLen: (ba.previewUrl || ba.dataUrl || "").length,
        hasDataUrl: !!ba.dataUrl,
        extra: "idx=" + (ai + 1),
      });
    }
  }
  enableAutoFollow();
  discardRevertedRows(cs);
  var bubbleRow = addUserBubble(text, bubbleAttachments);
  if (cs) {
    cs.pendingSend = true;
    showTurnWaiting(cs, "send_message");
    syncComposerForConv(cs, "send_message");
    syncSendButton();
  }

  input.value = "";
  autoResize(input);
  clearPendingAttachments();
  _clearComposerDraft();
  if (state.selectedSkill) {
    selectSkill(null);
  }

  var sendThreadId = msgData.threadId;
  sendReliable("text", msgData, {
    onFail: function (reason) {
      handleTextSendFailure(sendThreadId, bubbleRow, msgData, reason);
    }
  });
  } finally {
    _isSending = false;
    _syncSendBtn();
  }
}

export function handleInputKey(e) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    if (_isSending || _hasUploading()) return;
    var cs = activeConv();
    if (cs && cs.isStreaming) {
      cancelTask(cs.currentTaskId, "main_stop_enter");
      return;
    }
    if (cs && cs.pendingSend) return;
    sendText();
  }
}

function _inputMaxHeight(el) {
  var maxH = parseInt(window.getComputedStyle(el).maxHeight, 10);
  if (maxH > 0) return maxH;
  return Math.min(400, Math.round(window.innerHeight * 0.45));
}

function _inputMinHeight(el) {
  var minH = parseInt(window.getComputedStyle(el).minHeight, 10);
  if (minH > 0) return minH;
  return 52;
}

export function autoResize(el) {
  if (!el) return;
  var maxH = _inputMaxHeight(el);
  var minH = _inputMinHeight(el);
  el.style.height = "0px";
  var scrollH = el.scrollHeight;
  var nextH = Math.max(minH, Math.min(scrollH, maxH));
  el.style.height = nextH + "px";
  el.style.overflowY = scrollH > maxH ? "auto" : "hidden";
}

export function handleSendBtn() {
  if (_isSending || _hasUploading()) return;
  var cs = activeConv();
  if (cs && cs.isStreaming) {
    cancelTask(cs.currentTaskId, "main_stop_btn");
    return;
  }
  if (cs && cs.pendingSend) return;
  sendText();
}

function _dtTypes(dt) {
  return Array.from((dt && dt.types) || []);
}

function _isAttachDrag(e) {
  var types = _dtTypes(e.dataTransfer);
  return types.indexOf("Files") >= 0
    || types.indexOf(NEXTAGENT_ASSET_REFS_DRAG_TYPE) >= 0
    || types.indexOf(FM_PATHS_DRAG_TYPE) >= 0;
}

function _hasFilePayload(dt) {
  if (!dt) return false;
  if (dt.files && dt.files.length) return true;
  var items = dt.items ? Array.from(dt.items) : [];
  for (var i = 0; i < items.length; i++) {
    if (items[i].kind === "file") return true;
  }
  return false;
}

function _clearWindowDragOver() {
  document.body.classList.remove("window-drag-over");
  var inputBox = document.querySelector(".input-box");
  if (inputBox) inputBox.classList.remove("drag-over");
}

function _readDirEntries(dirEntry) {
  return new Promise(function (resolve, reject) {
    var reader = dirEntry.createReader();
    var all = [];
    function next() {
      reader.readEntries(function (batch) {
        if (!batch.length) {
          resolve(all);
          return;
        }
        all.push.apply(all, batch);
        next();
      }, reject);
    }
    next();
  });
}

function _walkEntry(entry, prefix) {
  var rel = prefix ? prefix + "/" + entry.name : entry.name;
  if (entry.isFile) {
    return new Promise(function (resolve, reject) {
      entry.file(function (file) {
        resolve([{ file: file, relativePath: rel }]);
      }, reject);
    });
  }
  if (!entry.isDirectory) {
    throw new Error("不支持的拖入项: " + entry.name);
  }
  return _readDirEntries(entry).then(function (children) {
    var acc = Promise.resolve([]);
    children.forEach(function (child) {
      acc = acc.then(function (prev) {
        return _walkEntry(child, rel).then(function (part) {
          return prev.concat(part);
        });
      });
    });
    return acc;
  });
}

function _fmPathsToRefs(raw) {
  var parsed = JSON.parse(raw);
  if (!Array.isArray(parsed) || !parsed.length) return [];
  var refs = [];
  for (var i = 0; i < parsed.length; i++) {
    var p = String(parsed[i] || "").trim();
    if (!p) continue;
    var name = p.replace(/\\/g, "/").split("/").pop() || p;
    refs.push({ path: p, filename: name });
  }
  return refs;
}

function _ingestDataTransfer(dt) {
  if (!dt) return Promise.resolve();
  var types = _dtTypes(dt);
  if (types.indexOf(NEXTAGENT_ASSET_REFS_DRAG_TYPE) >= 0) {
    var raw = dt.getData(NEXTAGENT_ASSET_REFS_DRAG_TYPE);
    if (raw) {
      var parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.length) {
        addPendingRefsFromExistingMedia(parsed);
      }
    }
    return Promise.resolve();
  }
  if (types.indexOf(FM_PATHS_DRAG_TYPE) >= 0) {
    var fmRaw = dt.getData(FM_PATHS_DRAG_TYPE);
    if (fmRaw) {
      var fmRefs = _fmPathsToRefs(fmRaw);
      if (fmRefs.length) addPendingRefsFromExistingMedia(fmRefs);
    }
    return Promise.resolve();
  }
  var items = dt.items ? Array.from(dt.items) : [];
  var entries = [];
  for (var i = 0; i < items.length; i++) {
    if (items[i].kind !== "file") continue;
    if (typeof items[i].webkitGetAsEntry !== "function") continue;
    var entry = items[i].webkitGetAsEntry();
    if (entry) entries.push(entry);
  }
  if (!entries.length) {
    var files = dt.files ? Array.from(dt.files) : [];
    for (var j = 0; j < files.length; j++) addAttachment(files[j]);
    return Promise.resolve();
  }
  var acc = Promise.resolve([]);
  entries.forEach(function (ent) {
    acc = acc.then(function (prev) {
      return _walkEntry(ent, "").then(function (part) {
        return prev.concat(part);
      });
    });
  });
  return acc.then(function (list) {
    for (var k = 0; k < list.length; k++) {
      addAttachment(list[k].file, list[k].relativePath);
    }
  });
}

export function handlePaste(e) {
  var dt = e.clipboardData;
  if (!_hasFilePayload(dt)) return;
  e.preventDefault();
  _ingestDataTransfer(dt);
}

export function handleFileDrop(e) {
  e.preventDefault();
  e.stopPropagation();
  _clearWindowDragOver();
  _ingestDataTransfer(e.dataTransfer);
}

export function handleDragOver(e) {
  if (!_isAttachDrag(e)) return;
  e.preventDefault();
  e.stopPropagation();
  var dt = e.dataTransfer;
  if (dt) dt.dropEffect = "copy";
  document.body.classList.add("window-drag-over");
  var inputBox = document.querySelector(".input-box");
  if (inputBox) inputBox.classList.add("drag-over");
}

export function handleDragLeave(e) {
  e.preventDefault();
  e.stopPropagation();
}

export function bindWindowFileIO() {
  var depth = 0;
  document.addEventListener("dragenter", function (e) {
    if (!_isAttachDrag(e)) return;
    e.preventDefault();
    depth += 1;
    document.body.classList.add("window-drag-over");
  });
  document.addEventListener("dragover", handleDragOver);
  document.addEventListener("dragleave", function (e) {
    if (!_isAttachDrag(e)) return;
    depth -= 1;
    if (depth <= 0) {
      depth = 0;
      _clearWindowDragOver();
    }
  });
  document.addEventListener("drop", function (e) {
    if (!_isAttachDrag(e)) return;
    depth = 0;
    handleFileDrop(e);
  });
  document.addEventListener("paste", function (e) {
    if (e.target && e.target.closest && e.target.closest("input:not(#fileInput), textarea:not(#msgInput)")) {
      return;
    }
    handlePaste(e);
  });
}

export function handleUploadBtn() {
  var fileInput = document.getElementById("fileInput");
  if (fileInput) fileInput.click();
}

export function handleFileSelect(e) {
  var files = e.target.files;
  if (!files) return;
  for (var i = 0; i < files.length; i++) {
    addAttachment(files[i]);
  }
  e.target.value = "";
}

export function clearContext() {
  sendWsMsg("clear_context", { threadId: state.currentConversationId });
  var area = getChatAreaEl();
  area.innerHTML = "";
  syncCurrentPageThinkToggle(area);
}

export function selectSkill(skillId) {
  state.selectedSkill = skillId;
  var btn = document.getElementById("toolBtn");
  var badge = document.getElementById("toolBadge");
  if (btn && badge) {
    if (skillId) {
      var t = state.availableSkills.find(function (t) { return t.id === skillId; });
      badge.textContent = t ? t.name : skillId;
      badge.style.display = "inline";
      btn.classList.add("active");
    } else {
      badge.textContent = "";
      badge.style.display = "none";
      btn.classList.remove("active");
    }
  }
  closeToolDropdown();
  persistSessionUi();
}

export function toggleToolDropdown() {
  var existing = document.getElementById("toolDropdown");
  if (existing) {
    closeToolDropdown();
  } else {
    openToolDropdown();
  }
}

export function closeToolDropdown() {
  var dropdown = document.getElementById("toolDropdown");
  if (dropdown) dropdown.remove();
}

function openToolDropdown() {
  closeToolDropdown();
  closeEffortDropdown();
  var btn = document.getElementById("toolBtn");
  if (!btn) return;

  var rect = btn.getBoundingClientRect();
  var dropdown = document.createElement("div");
  dropdown.id = "toolDropdown";
  dropdown.className = "tool-dropdown";

  if (state.selectedSkill) {
    var clearItem = document.createElement("div");
    clearItem.className = "tool-dropdown-item tool-dropdown-clear";
    clearItem.textContent = "取消选择 (由 AI 决定)";
    clearItem.onclick = function (e) {
      e.stopPropagation();
      selectSkill(null);
    };
    dropdown.appendChild(clearItem);
  }

  for (var i = 0; i < state.availableSkills.length; i++) {
    (function (skill) {
      var item = document.createElement("div");
      item.className = "tool-dropdown-item";
      if (state.selectedSkill === skill.id) item.classList.add("selected");
      item.textContent = skill.name;
      item.onclick = function (e) {
        e.stopPropagation();
        selectSkill(skill.id);
      };
      dropdown.appendChild(item);
    })(state.availableSkills[i]);
  }

  document.body.appendChild(dropdown);
  dropdown.style.left = rect.left + "px";
  dropdown.style.top = (rect.top - dropdown.offsetHeight - 6) + "px";
}

export async function fetchSkills() {
  try {
    var resp = await naFetch(API_BASE + "/api/v1/skills");
    var data = await resp.json();
    state.availableSkills = data.skills || data.tools || [];
    if (state.selectedSkill) {
      var exists = state.availableSkills.some(function (item) { return item.id === state.selectedSkill; });
      if (!exists) {
        selectSkill(null);
      }
    }
  } catch (e) {
    if (e && e.message === "AUTH_REQUIRED") {
      return;
    }
    state.availableSkills = [];
    if (state.selectedSkill) {
      selectSkill(null);
    }
  }
}

export function toggleModelDropdown() {
  var existing = document.getElementById("modelDropdown");
  if (existing) {
    closeModelDropdown();
  } else {
    openModelDropdown();
  }
}

export function closeModelDropdown() {
  var dropdown = document.getElementById("modelDropdown");
  if (dropdown) dropdown.remove();
}

function openModelDropdown() {
  closeModelDropdown();
  closeEffortDropdown();
  var btn = document.getElementById("modelBtn");
  if (!btn) return;

  var rect = btn.getBoundingClientRect();
  var dropdown = document.createElement("div");
  dropdown.id = "modelDropdown";
  dropdown.className = "tool-dropdown";

  var profiles = getModelDropdownProfiles();
  for (var i = 0; i < profiles.length; i++) {
    (function (profile) {
      var item = document.createElement("div");
      item.className = "tool-dropdown-item";
      if (state.selectedAgentProfile === profile.id) item.classList.add("selected");
      item.textContent = profile.displayName || profile.id;
      item.onclick = function (e) {
        e.stopPropagation();
        selectAgentProfile(profile.id);
      };
      dropdown.appendChild(item);
    })(profiles[i]);
  }

  document.body.appendChild(dropdown);
  dropdown.style.left = rect.left + "px";
  dropdown.style.top = (rect.top - dropdown.offsetHeight - 6) + "px";
}

window.addEventListener("nextagent-add-chat-attachments", function (ev) {
  var d = ev && ev.detail;
  var refs = d && d.refs;
  if (refs && refs.length) addPendingRefsFromExistingMedia(refs);
});
