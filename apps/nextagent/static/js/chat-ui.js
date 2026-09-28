import { state, activeConv, getConvState } from "./state.js";
import { syncComposerForConv } from "./composer.js";
import { formatContent, initImageLoaders } from "./format.js";
import { stampWorkspaceUrlsInRoot } from "./asset-url.js";
import { logAssetHtml, logAssetUrl, probeAssetImg, snapshotImgSrc } from "./asset-diag.js";
import { bindImage, bindImages } from "./media-loader.js";
import { resolveBlobUrl } from "./asset-catalog.js";
import { API_BASE } from "./config.js";
import { openPreview } from "./modules/asset-system/preview-window.js";
import { downloadAsset, uploadAndRegisterAsset } from "./upload.js";
import { resolveUploadParentPath, resolveChatUploadParentPath, applyNodeFromApi, refreshAfterAssetMutation } from "./modules/asset-system/asset-store.js";
import { previewNodeFromMediaSrc, resolveFileUrl, toWorkspaceReadUrl, workspacePathFromHttpUrl } from "./asset-url.js";
import { getSendModelParams } from "./model-controls.js";
import { isPlanModeOn } from "./plan-review.js";
import { blocksResetTurn, showTurnWaiting, removeTurnWaiting } from "./blocks-renderer.js";
import { createRestoreBtn, clearCheckpointCursorState } from "./checkpoints.js";

function formatThinkContent(text) {
  if (typeof text !== "string" || !text) return "";
  text = text.replace(/\[async_task:[^\]]+\]/g, "");
  text = text.replace(/\[media_(?:task|pending|done):[^\]]+\]/g, "");
  const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const escaped = String(text).replace(/[&<>"']/g, function (c) { return map[c]; });
  return escaped.replace(/(https?:\/\/[^\s\]`'<>]+)/g, function (m) {
    return '<a href="' + m + '" target="_blank" rel="noopener">' + m + '</a>';
  });
}

export function mountChatArea(el) {
  var scroll = document.getElementById("chatScroll");
  if (!scroll || !el) return;
  var seat = document.getElementById("composerSeat");
  if (el.parentNode === scroll) {
    if (seat && el.nextSibling !== seat) scroll.insertBefore(el, seat);
    return;
  }
  if (seat) scroll.insertBefore(el, seat);
  else scroll.appendChild(el);
}

function getChatArea() {
  const cs = activeConv();
  if (cs) return cs.el;
  let el = document.querySelector("#chatScroll .chat-area");
  if (!el) {
    el = document.createElement("div");
    el.className = "chat-area";
    mountChatArea(el);
  }
  return el;
}

export function addSystemMsg(text, targetArea) {
  const area = targetArea || getChatArea();
  const div = document.createElement("div");
  div.className = "system-msg";
  div.textContent = text;
  area.appendChild(div);
  if (!targetArea) scrollToBottom();
}

function copyText(text) {
  return navigator.clipboard.writeText(text);
}

function getAiBubbleCopyText(row) {
  if (!row) return "";
  var bubble = row.querySelector(".bubble");
  if (!bubble) return "";
  var textTarget = bubble.querySelector(".media-text-area");
  if (textTarget) {
    return textTarget.dataset.raw || textTarget.textContent || "";
  }
  return bubble.dataset.raw || bubble.textContent || "";
}

export function refreshAiBubbleCopyState(row) {
  if (!row || !row.classList.contains("ai")) return;
  var bubble = row.querySelector(".bubble");
  var btn = row.querySelector(".bubble-copy-btn");
  if (!bubble || !btn) return;
  var text = getAiBubbleCopyText(row).trim();
  var loading = bubble.classList.contains("is-loading") || bubble.classList.contains("is-media-loading");
  btn.disabled = loading || !text;
}

function createAiCopyBtn(row) {
  var btn = document.createElement("button");
  btn.type = "button";
  btn.className = "bubble-copy-btn";
  btn.textContent = "复制";
  btn.onclick = function (e) {
    e.stopPropagation();
    if (btn.disabled) return;
    var text = getAiBubbleCopyText(row);
    copyText(text).then(function () {
      btn.textContent = "已复制";
      clearTimeout(btn._copiedTimer);
      btn._copiedTimer = setTimeout(function () {
        btn.textContent = "复制";
      }, 1500);
    });
  };
  return btn;
}

function getAiBubbleShell(row) {
  return row ? row.querySelector(".ai-bubble-shell") : null;
}

function createEditBtn(row, submitLabel, onSubmit) {
  const editBtn = document.createElement("button");
  editBtn.className = "action-btn";
  editBtn.title = "编辑";
  editBtn.innerHTML = "&#9998;";
  editBtn.onclick = function () {
    startEdit(row, submitLabel, onSubmit);
  };
  return editBtn;
}

function _filenameFromHref(href) {
  try {
    var u = new URL(href, window.location.origin);
    var qPath = u.searchParams.get("path") || "";
    var base = qPath || u.pathname || href;
    var parts = String(base).split("/");
    var name = parts[parts.length - 1] || "download";
    try {
      name = decodeURIComponent(name);
    } catch (e) {}
    return name || "download";
  } catch (e2) {
    var segs = String(href || "").split("/");
    return segs[segs.length - 1] || "download";
  }
}

function buildFileDownloadHtml(safeHref, filename) {
  var name = String(filename || "download")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
  var href = String(safeHref || "").replace(/"/g, "&quot;");
  return (
    '<div class="chat-file-wrap" data-file-url="' +
    href +
    '" data-file-name="' +
    name +
    '">' +
    '<div class="chat-file-name">' +
    name +
    '</div>' +
    '<button type="button" class="chat-file-download-btn">下载文件</button>' +
    "</div>"
  );
}

function convertDownloadMarkdown(raw) {
  return String(raw || "").replace(
    /\[download:([^\]]*)\]\(([^)]+)\)/gi,
    function (_m, name, href) {
      var mediaHref = toWorkspaceReadUrl(href) || href;
      var safeHref = String(mediaHref || "").replace(/"/g, "&quot;");
      return buildFileDownloadHtml(safeHref, name || _filenameFromHref(mediaHref));
    }
  );
}

function decodeHtmlEntities(s) {
  var str = String(s || "");
  if (str.indexOf("&lt;") < 0 && str.indexOf("&amp;") < 0) return str;
  var ta = document.createElement("textarea");
  ta.innerHTML = str;
  return ta.value;
}

function _isInlineMediaUrl(url) {
  var href = String(url || "");
  return (
    /\.(png|jpe?g|gif|webp|bmp|svg)(\?|$)/i.test(href) ||
    /\.(mp4|webm|mov|avi|mkv)(\?|$)/i.test(href) ||
    /\.(mp3|wav|ogg|flac|m4a|aac)(\?|$)/i.test(href)
  );
}

function resolveFileMediaTarget(mediaOrContent) {
  var media = mediaOrContent;
  var raw = "";
  var urls = [];
  var rtype = "";
  if (media && typeof media === "object" && !Array.isArray(media)) {
    urls = Array.isArray(media.urls) ? media.urls : [];
    rtype = String(media.mediaType || media.media_type || media.type || "").toLowerCase();
    raw = String(media.content || media.mediaContent || media.error || "");
  } else {
    raw = String(mediaOrContent || "");
  }
  raw = decodeHtmlEntities(raw).trim();
  if (rtype === "image" || rtype === "video" || rtype === "audio" || rtype === "text") return null;
  var url = "";
  var name = "";
  var forcedFile = rtype === "file" || rtype === "download";
  if (raw) {
    try {
      var meta = JSON.parse(raw);
      if (meta && (meta.kind === "file" || meta.type === "file")) {
        url = String(meta.url || "").trim();
        if (!url && meta.path) url = toWorkspaceReadUrl(String(meta.path)) || String(meta.path);
        name = String(meta.name || "").trim();
        if (!name && meta.path) name = String(meta.path).split("/").pop() || "";
        forcedFile = true;
      }
    } catch (e) {}
  }
  if (!url) {
    var attrUrl = raw.match(/data-file-url=["']([^"']+)["']/i);
    if (attrUrl) {
      url = attrUrl[1];
      forcedFile = true;
    }
  }
  if (!name) {
    var attrName = raw.match(/data-file-name=["']([^"']+)["']/i);
    if (attrName) name = attrName[1];
  }
  if (!url && /\[download:/i.test(raw)) {
    var dl = raw.match(/\[download:([^\]]*)\]\(([^)]+)\)/i);
    if (dl) {
      name = name || String(dl[1] || "").trim();
      url = String(dl[2] || "").trim();
      forcedFile = true;
    }
  }
  if (!url && forcedFile) {
    url = String(urls[0] || "").trim();
  }
  if (!url && forcedFile) {
    var readUrl = raw.match(/(\/workspace\/read\?path=[^\s"'<>]+)/i);
    if (readUrl) url = readUrl[1];
  }
  if (!url && raw.indexOf("chat-file-wrap") >= 0) {
    var readUrl2 = raw.match(/(\/workspace\/read\?path=[^\s"'<>]+)/i);
    if (readUrl2) {
      url = readUrl2[1];
      forcedFile = true;
    }
  }
  if (!forcedFile) return null;
  if (!url) return null;
  if (_isInlineMediaUrl(url) && rtype !== "file" && rtype !== "download" && raw.indexOf("chat-file-wrap") < 0) {
    return null;
  }
  url = toWorkspaceReadUrl(url) || url;
  name = name || _filenameFromHref(url) || "download";
  return { url: url, name: name };
}

function mountFileDownloadCard(contentEl, url, filename) {
  while (contentEl.firstChild) contentEl.removeChild(contentEl.firstChild);
  var wrap = document.createElement("div");
  wrap.className = "chat-file-wrap";
  wrap.setAttribute("data-file-url", url);
  wrap.setAttribute("data-file-name", filename);
  var nameEl = document.createElement("div");
  nameEl.className = "chat-file-name";
  nameEl.textContent = filename;
  var btn = document.createElement("button");
  btn.type = "button";
  btn.className = "chat-file-download-btn";
  btn.textContent = "下载文件";
  wrap.appendChild(nameEl);
  wrap.appendChild(btn);
  contentEl.appendChild(wrap);
}

function triggerChatFileDownload(wrap) {
  if (!wrap) return;
  var url = String(wrap.getAttribute("data-file-url") || "").trim();
  var name = String(wrap.getAttribute("data-file-name") || "download").trim() || "download";
  var path = workspacePathFromHttpUrl(url) || url;
  downloadAsset({ media_id: path, path: path, name: name, asset_type: "file" });
}

if (typeof document !== "undefined") {
  document.addEventListener("click", function (e) {
    var btn = e.target && e.target.closest ? e.target.closest(".chat-file-download-btn") : null;
    if (!btn) return;
    e.preventDefault();
    triggerChatFileDownload(btn.closest(".chat-file-wrap"));
  });
}

export function isFileMediaPayload(media) {
  if (!media) return false;
  if (typeof media === "string") return !!resolveFileMediaTarget(media);
  if (typeof media !== "object") return false;
  var rtype = String(media.mediaType || media.media_type || media.type || "").toLowerCase();
  if (rtype === "image" || rtype === "video" || rtype === "audio" || rtype === "text") return false;
  if (rtype === "file" || rtype === "download") return true;
  return !!resolveFileMediaTarget(media);
}

export function finishFileMediaBubble(el, media, bgTaskId, textContent) {
  if (!el) return false;
  var target = resolveFileMediaTarget(media);
  if (!target) return false;
  var imageArea = bgTaskId ? el.querySelector('.media-image-area[data-bg-task-id="' + bgTaskId + '"]') : null;
  if (!imageArea) imageArea = el.querySelector(".media-image-area");
  if (!imageArea) {
    ensureToolStepMedia(el, bgTaskId || "file", "render_media");
    imageArea = bgTaskId ? el.querySelector('.media-image-area[data-bg-task-id="' + bgTaskId + '"]') : null;
    if (!imageArea) imageArea = el.querySelector(".media-image-area");
  }
  if (!imageArea) return false;
  var contentEl = imageArea.querySelector(".media-image-content");
  if (!contentEl) {
    contentEl = document.createElement("div");
    contentEl.className = "media-image-content";
    imageArea.insertBefore(contentEl, imageArea.querySelector(".media-overlay"));
  }
  mountFileDownloadCard(contentEl, target.url, target.name);
  imageArea.classList.add("has-file");
  imageArea.classList.remove("has-image");
  imageArea.style.removeProperty("--media-bg-src");
  contentEl.dataset.mediaReadyHtml = "file:" + target.url;
  if (textContent) {
    var textArea = el.querySelector(".media-text-area");
    if (textArea) {
      textArea.dataset.raw = textContent;
      textArea.innerHTML = formatContent(textContent);
      initImageLoaders(textArea);
    }
  }
  refreshAiBubbleCopyState(el);
  return true;
}

function findRenderTextProse(toolSeg, bgTaskId) {
  var key = bgTaskId ? String(bgTaskId) : "";
  var sib = toolSeg.nextElementSibling;
  while (sib) {
    if (!sib.classList.contains("render-text-prose")) break;
    if (!key || sib.dataset.bgTaskId === key) return sib;
    sib = sib.nextElementSibling;
  }
  return null;
}

export function finishTextMediaBubble(el, text, bgTaskId) {
  if (!el) return false;
  var toolRow = el.classList && el.classList.contains("tool-step")
    ? el
    : (el.closest ? el.closest(".tool-step") : null);
  if (!toolRow) toolRow = el;
  var toolSeg = toolRow.closest ? toolRow.closest(".chat-stream-tool") : null;
  if (!toolSeg) toolSeg = toolRow.parentNode;
  if (!toolSeg || !toolSeg.parentNode) return false;
  var body = toolRow.querySelector(".tool-step-body");
  if (body) {
    var oldBoxes = body.querySelectorAll(".media-text-box");
    for (var i = 0; i < oldBoxes.length; i++) oldBoxes[i].remove();
    var slot = body.querySelector(".tool-step-media");
    if (slot && !slot.querySelector(".media-image-area") && !slot.querySelector(".media-image-content")) {
      slot.remove();
    }
    body.hidden = true;
  }
  var card = toolRow.querySelector(".tool-step-card");
  if (card) card.classList.remove("is-open");
  toolRow.classList.remove("tool-step-has-media");
  var key = bgTaskId ? String(bgTaskId) : "";
  var prose = findRenderTextProse(toolSeg, key);
  if (!prose) {
    prose = document.createElement("div");
    prose.className = "chat-stream-assistant render-text-prose";
    if (key) prose.dataset.bgTaskId = key;
    var bubble = document.createElement("div");
    bubble.className = "bubble ai-bubble hermes-assistant-bubble";
    prose.appendChild(bubble);
    toolSeg.parentNode.insertBefore(prose, toolSeg.nextSibling);
  }
  var textBody = prose.querySelector(".hermes-assistant-bubble");
  if (!textBody) return false;
  textBody.dataset.raw = text || "";
  textBody.innerHTML = formatContent(text || "", { document: true });
  initImageLoaders(textBody);
  var msgRow = toolRow.closest ? toolRow.closest(".msg-row.ai") : null;
  if (msgRow) refreshAiBubbleCopyState(msgRow);
  return true;
}

export function buildMediaHtmlFromUrls(urls, mediaType) {
  var parts = [];
  var rtype = String(mediaType || "").toLowerCase();
  for (var i = 0; i < urls.length; i++) {
    var rawHref = String(urls[i] || "").trim();
    if (!rawHref) continue;
    var href = toWorkspaceReadUrl(rawHref) || rawHref;
    var safeHref = href.replace(/"/g, "&quot;");
    var isAudio =
      rtype === "audio" ||
      /\.(mp3|wav|ogg|flac|m4a|aac)(\?|$)/i.test(href);
    var isImage =
      rtype === "image" ||
      (!rtype && /\.(png|jpe?g|gif|webp|bmp|svg)(\?|$)/i.test(href));
    var isVideo =
      rtype === "video" ||
      (!rtype && /\.(mp4|webm|mov|avi|mkv)(\?|$)/i.test(href));
    if (isAudio) {
      parts.push(
        '<div class="chat-media-wrap"><audio controls preload="metadata" class="chat-audio"><source src="' +
          safeHref +
          '"></audio></div>'
      );
    } else if (isImage) {
      parts.push('<div class="chat-image-wrap"><img src="' + safeHref + '" alt=""></div>');
    } else if (isVideo) {
      parts.push(
        '<div class="chat-media-wrap"><video controls preload="metadata" class="chat-video"><source src="' +
          safeHref +
          '" type="video/mp4"></video></div>'
      );
    } else {
      parts.push(buildFileDownloadHtml(safeHref, _filenameFromHref(href)));
    }
  }
  return parts.join("");
}

export function resolveMediaContentHtml(media) {
  var urls = Array.isArray(media && media.urls) ? media.urls : [];
  var cleaned = [];
  for (var i = 0; i < urls.length; i++) {
    var item = String(urls[i] || "").trim();
    if (item) cleaned.push(item);
  }
  var rtype = String(
    (media && (media.mediaType || media.media_type || media.type)) || ""
  ).toLowerCase();
  if (
    cleaned.length > 1 ||
    (cleaned.length === 1 && (rtype === "file" || rtype === "download"))
  ) {
    return buildMediaHtmlFromUrls(cleaned, rtype || "file");
  }
  return String((media && (media.content || media.mediaContent)) || "");
}

export function extractMediaOnlyHtml(contentHtml) {
  logAssetHtml("extract_input", contentHtml);
  var raw = convertDownloadMarkdown(String(contentHtml || ""));
  if (raw.indexOf("chat-file-wrap") >= 0) {
    return raw;
  }
  if (raw.indexOf("```") < 0) {
    if (
      raw.indexOf("chat-media-wrap") >= 0 ||
      raw.indexOf("chat-image-wrap") >= 0
    ) {
      var prebuilt = document.createElement("div");
      prebuilt.innerHTML = raw;
      stampWorkspaceUrlsInRoot(prebuilt);
      var prebuiltNodes = Array.from(
        prebuilt.querySelectorAll(".chat-image-wrap, .chat-media-wrap, .chat-file-wrap")
      );
      if (prebuiltNodes.length) {
        return prebuiltNodes.map(function (node) { return node.outerHTML; }).join("");
      }
    }
    var mdImgRe = /!\[[^\]]*\]\(([^)]+)\)/g;
    var mdImgHrefs = [];
    var mdMatch;
    while ((mdMatch = mdImgRe.exec(raw)) !== null) {
      if (mdMatch[1]) mdImgHrefs.push(mdMatch[1].trim());
    }
    if (mdImgHrefs.length > 0) {
      var mdHtmlParts = [];
      for (var mi = 0; mi < mdImgHrefs.length; mi++) {
        var mdHref = toWorkspaceReadUrl(mdImgHrefs[mi]) || mdImgHrefs[mi];
        mdHtmlParts.push('<div class="chat-image-wrap"><img src="' + mdHref.replace(/"/g, "&quot;") + '" alt=""></div>');
      }
      return mdHtmlParts.join("");
    }
  }
  var mdMediaRe = /\[(video|audio|media|download|file)(?::([^\]]*))?\]\(([^)]+)\)/gi;
  var mdMediaItems = [];
  var mdMediaMatch;
  while ((mdMediaMatch = mdMediaRe.exec(raw)) !== null) {
    if (mdMediaMatch[3]) {
      mdMediaItems.push({
        label: String(mdMediaMatch[1] || "").toLowerCase(),
        name: String(mdMediaMatch[2] || "").trim(),
        href: mdMediaMatch[3].trim(),
      });
    }
  }
  if (mdMediaItems.length > 0 && raw.indexOf("```") < 0) {
    var mdMediaHtmlParts = [];
    for (var mj = 0; mj < mdMediaItems.length; mj++) {
      var mediaItem = mdMediaItems[mj];
      var mediaHref = toWorkspaceReadUrl(mediaItem.href) || mediaItem.href;
      var safeMediaHref = mediaHref.replace(/"/g, "&quot;");
      var isAudioMedia =
        mediaItem.label === "audio" ||
        /\.(mp3|wav|ogg|flac|m4a|aac)(\?|$)/i.test(mediaHref);
      var isVideoMedia =
        mediaItem.label === "video" ||
        /\.(mp4|webm|mov|avi|mkv)(\?|$)/i.test(mediaHref);
      var isFileMedia =
        mediaItem.label === "download" ||
        mediaItem.label === "file" ||
        (mediaItem.label === "media" && !isVideoMedia);
      if (isAudioMedia) {
        mdMediaHtmlParts.push(
          '<div class="chat-media-wrap"><audio controls preload="metadata" class="chat-audio"><source src="' +
            safeMediaHref +
            '"></audio></div>'
        );
      } else if (isFileMedia) {
        mdMediaHtmlParts.push(
          buildFileDownloadHtml(safeMediaHref, mediaItem.name || _filenameFromHref(mediaHref))
        );
      } else {
        mdMediaHtmlParts.push(
          '<div class="chat-media-wrap"><video controls preload="metadata" class="chat-video"><source src="' +
            safeMediaHref +
            '" type="video/mp4"></video></div>'
        );
      }
    }
    return mdMediaHtmlParts.join("");
  }
  var holder = document.createElement("div");
  holder.innerHTML = formatContent(raw);
  stampWorkspaceUrlsInRoot(holder);
  var firstImg = holder.querySelector("img");
  if (firstImg) snapshotImgSrc(firstImg, "extract_after_stamp");
  var mediaNodes = Array.from(
    holder.querySelectorAll(".chat-image-wrap, .chat-media-wrap, .chat-file-wrap")
  );
  if (!mediaNodes.length) {
    mediaNodes = Array.from(holder.querySelectorAll("img, video, audio"));
  }
  if (!mediaNodes.length) return "";
  var htmlParts = [];
  for (var i = 0; i < mediaNodes.length; i++) {
    var mediaNode = mediaNodes[i];
    if (
      mediaNode.classList &&
      (mediaNode.classList.contains("chat-image-wrap") ||
        mediaNode.classList.contains("chat-media-wrap") ||
        mediaNode.classList.contains("chat-file-wrap"))
    ) {
      htmlParts.push(mediaNode.outerHTML);
      continue;
    }
    var wrap = document.createElement("div");
    var tag = mediaNode.tagName.toLowerCase();
    wrap.className = tag === "video" || tag === "audio" ? "chat-media-wrap" : "chat-image-wrap";
    wrap.appendChild(mediaNode.cloneNode(true));
    htmlParts.push(wrap.outerHTML);
  }
  var joined = htmlParts.join("");
  logAssetHtml("extract_outerHTML", joined);
  return joined;
}

function isMobileEditMode() {
  return window.innerWidth <= 900;
}

let _thinkVisibilityCollapsed = false;

function getVisibleThinkBlocks(targetArea) {
  const area = targetArea || getChatArea();
  return Array.from(area.querySelectorAll(".think-block")).filter(function (block) {
    return block.style.display !== "none";
  });
}

function setThinkBlockCollapsed(block, collapsed) {
  if (!block) return;
  block.classList.toggle("collapsed", !!collapsed);
}

function applyThinkVisibilityPreference(block) {
  setThinkBlockCollapsed(block, _thinkVisibilityCollapsed);
}

function bindThinkBlockHeader(block) {
  if (!block) return;
  const header = block.querySelector(".think-header");
  if (!header) return;
  header.onclick = function () {
    block.classList.toggle("collapsed");
    syncCurrentPageThinkToggle();
  };
}

function prepareThinkBlock(block) {
  if (!block) return block;
  bindThinkBlockHeader(block);
  applyThinkVisibilityPreference(block);
  return block;
}

export function syncCurrentPageThinkToggle(targetArea) {
  const btn = document.getElementById("thinkVisibilityToggle");
  if (!btn) return;
  const blocks = getVisibleThinkBlocks(targetArea);
  if (!blocks.length) {
    _thinkVisibilityCollapsed = false;
    btn.disabled = true;
    btn.textContent = "折叠思考";
    btn.title = "当前页面没有思考过程";
    return;
  }
  const allCollapsed = blocks.every(function (block) {
    return block.classList.contains("collapsed");
  });
  _thinkVisibilityCollapsed = allCollapsed;
  btn.disabled = false;
  btn.textContent = allCollapsed ? "展开思考" : "折叠思考";
  btn.title = allCollapsed ? "一键展开当前页面思考过程" : "一键折叠当前页面思考过程";
}

export function toggleCurrentPageThinkBlocks() {
  const blocks = getVisibleThinkBlocks();
  if (!blocks.length) {
    syncCurrentPageThinkToggle();
    return;
  }
  const shouldCollapse = !blocks.every(function (block) {
    return block.classList.contains("collapsed");
  });
  _thinkVisibilityCollapsed = shouldCollapse;
  blocks.forEach(function (block) {
    setThinkBlockCollapsed(block, shouldCollapse);
  });
  syncCurrentPageThinkToggle();
}

export function initThinkVisibilityToggle() {
  const btn = document.getElementById("thinkVisibilityToggle");
  if (!btn) return;
  btn.onclick = toggleCurrentPageThinkBlocks;
  syncCurrentPageThinkToggle();
}

function shouldIgnoreBubbleTap(e) {
  const target = e.target;
  if (!(target instanceof Element)) return false;
  return !!target.closest("a, button, textarea, input, pre, code, .tool-code-card, .think-header");
}

function buildEditAttachmentArea(initialAttachments, fileInputId) {
  var editAtts = (initialAttachments || []).slice();
  var container = document.createElement("div");
  container.className = "edit-att-area";
  var _uploadingCount = 0;
  var _onStateChange = null;

  container.isUploading = function () { return _uploadingCount > 0; };
  container.setOnStateChange = function (fn) { _onStateChange = fn; };
  container.getAttachments = function () {
    return editAtts.filter(function (a) { return !a._uploading; });
  };

  function _notifyState() {
    if (_onStateChange) _onStateChange(_uploadingCount > 0);
  }

  function render() {
    container.innerHTML = "";
    var imgCounter = 0;
    for (var k = 0; k < editAtts.length; k++) {
      if (!editAtts[k]._uploading && /^image\//i.test(editAtts[k].mime || editAtts[k].mime_type || "")) imgCounter++;
    }
    var imgSeq = 0;
    for (var i = 0; i < editAtts.length; i++) {
      (function (idx) {
        var att = editAtts[idx];
        var mime = att.mime || att.mime_type || "";
        var item = document.createElement("div");
        item.className = "edit-att-item";

        if (att._uploading) {
          item.classList.add("uploading");
          var spinnerWrap = document.createElement("div");
          spinnerWrap.className = "edit-att-file-icon";
          spinnerWrap.innerHTML = '<span class="edit-att-spinner"></span>';
          item.appendChild(spinnerWrap);
          var nm = document.createElement("div");
          nm.className = "edit-att-name";
          nm.textContent = att.name || "uploading...";
          item.appendChild(nm);
          container.appendChild(item);
          return;
        }

        if (/^image\//i.test(mime)) {
          imgSeq++;
          var img = document.createElement("img");
          img.className = "edit-att-thumb";
          img.src = resolveFileUrl(att);
          item.appendChild(img);
          if (imgCounter > 1) {
            var badge = document.createElement("span");
            badge.className = "img-index-badge";
            badge.textContent = imgSeq;
            item.appendChild(badge);
          }
        } else {
          var icon = document.createElement("div");
          icon.className = "edit-att-file-icon";
          if (/pdf/.test(mime)) icon.textContent = "\u{1F4C4}";
          else if (/audio/.test(mime)) icon.textContent = "\u{1F3B5}";
          else if (/video/.test(mime)) icon.textContent = "\u{1F3AC}";
          else icon.textContent = "\u{1F4CE}";
          item.appendChild(icon);
          var nm2 = document.createElement("div");
          nm2.className = "edit-att-name";
          nm2.textContent = att.name || att.filename || "file";
          item.appendChild(nm2);
        }
        var del = document.createElement("button");
        del.type = "button";
        del.className = "edit-att-del";
        del.textContent = "\u00D7";
        del.onclick = function (e) {
          e.stopPropagation();
          editAtts.splice(idx, 1);
          render();
        };
        item.appendChild(del);
        container.appendChild(item);
      })(i);
    }
    var addBtn = document.createElement("div");
    addBtn.className = "edit-att-add";
    addBtn.textContent = "+";
    addBtn.title = "\u6dfb\u52a0\u6587\u4ef6";
    var fi = document.createElement("input");
    fi.type = "file";
    fi.multiple = true;
    fi.hidden = true;
    fi.accept = "*/*";
    fi.onchange = function (e) {
      var files = e.target.files;
      if (!files) return;
      for (var j = 0; j < files.length; j++) {
        (function (file) {
          var placeholder = {
            _uploading: true,
            name: file.name,
            mime: file.type || "",
          };
          editAtts.push(placeholder);
          _uploadingCount++;
          _notifyState();
          render();

          var threadId = state.currentConversationId;
          var uploadParentPath = resolveChatUploadParentPath();
          uploadAndRegisterAsset(file, {
            parentPath: uploadParentPath,
          })
            .then(function (data) {
              var pos = editAtts.indexOf(placeholder);
              if (pos !== -1) {
                editAtts[pos] = {
                  media_id: data.media_id,
                  name: data.filename || file.name,
                  mime: data.mime_type || file.type,
                  previewUrl: resolveFileUrl(data.node || data.media_id),
                };
              }
              _uploadingCount--;
              _notifyState();
              render();
              if (data && data.node) applyNodeFromApi(data.node);
              refreshAfterAssetMutation(threadId, [data.node && data.node.path, uploadParentPath].filter(Boolean), uploadParentPath);
            })
            .catch(function () {
              var pos = editAtts.indexOf(placeholder);
              if (pos !== -1) editAtts.splice(pos, 1);
              _uploadingCount--;
              _notifyState();
              render();
            });
        })(files[j]);
      }
      fi.value = "";
    };
    addBtn.appendChild(fi);
    addBtn.onclick = function () { fi.click(); };
    container.appendChild(addBtn);
  }

  render();
  return container;
}

function closeEditModal() {
  const overlay = document.querySelector(".edit-modal-overlay");
  if (overlay) overlay.remove();
  document.body.classList.remove("edit-modal-open");
}

function openEditModal(row, submitLabel, onSubmit) {
  const cs = activeConv();
  if (cs && (cs.isStreaming || cs.pendingSend)) return;
  closeEditModal();
  const bubble = row.querySelector(".bubble");
  if (!bubble) return;
  var isUser = row.classList.contains("user");
  var originalContent = isUser ? getUserBubbleText(row) : (bubble.dataset.raw || bubble.textContent);
  var originalAtts = isUser ? getUserBubbleAttachments(row) : [];
  const overlay = document.createElement("div");
  overlay.className = "edit-modal-overlay";
  const panel = document.createElement("div");
  panel.className = "edit-modal";
  const header = document.createElement("div");
  header.className = "edit-modal-header";
  header.textContent = "\u4fee\u6539\u8f93\u5165";
  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "edit-modal-close";
  closeBtn.textContent = "\u00D7";
  closeBtn.onclick = closeEditModal;
  header.appendChild(closeBtn);
  var attArea = null;
  if (isUser) {
    attArea = buildEditAttachmentArea(originalAtts);
  }
  const ta = document.createElement("textarea");
  ta.className = "edit-modal-textarea";
  ta.value = originalContent;
  const controls = document.createElement("div");
  controls.className = "edit-modal-controls";
  const cancelBtn = document.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "cancel-edit-btn";
  cancelBtn.textContent = "\u53d6\u6d88";
  cancelBtn.onclick = closeEditModal;
  const saveBtn = document.createElement("button");
  saveBtn.type = "button";
  saveBtn.className = "submit-edit-btn";
  saveBtn.textContent = submitLabel;
  if (attArea) {
    attArea.setOnStateChange(function (uploading) {
      saveBtn.disabled = uploading;
      saveBtn.textContent = uploading ? "上传中..." : submitLabel;
    });
  }
  saveBtn.onclick = function () {
    if (attArea && attArea.isUploading()) return;
    const newContent = ta.value.trim();
    var editedAtts = attArea ? attArea.getAttachments() : [];
    if (!newContent && editedAtts.length === 0) return;
    if (row.classList.contains("ai")) {
      bubble.dataset.raw = newContent;
      bubble.innerHTML = formatContent(newContent);
      initImageLoaders(bubble);
    }
    closeEditModal();
    onSubmit(newContent, editedAtts);
  };
  controls.appendChild(cancelBtn);
  controls.appendChild(saveBtn);
  panel.appendChild(header);
  if (attArea) panel.appendChild(attArea);
  panel.appendChild(ta);
  panel.appendChild(controls);
  overlay.appendChild(panel);
  overlay.onclick = function (e) {
    if (e.target === overlay) closeEditModal();
  };
  document.body.appendChild(overlay);
  document.body.classList.add("edit-modal-open");
  bindEditTextareaResize(ta);
  ta.focus();
  ta.setSelectionRange(ta.value.length, ta.value.length);
}

function startEdit(row, submitLabel, onSubmit) {
  if (isMobileEditMode()) {
    openEditModal(row, submitLabel, onSubmit);
    return;
  }
  startInlineEdit(row, submitLabel, onSubmit);
}

function bindBubbleEdit(row, bubble, submitLabel, onSubmit) {
  bubble.onclick = function (e) {
    if (!isMobileEditMode()) return;
    if (bubble.classList.contains("is-loading")) return;
    if (shouldIgnoreBubbleTap(e)) return;
    startEdit(row, submitLabel, onSubmit);
  };
}

function _buildBubbleImageNode(att, src, bubbleKey, imageIndex) {
  var mediaId = String(att.media_id || att.path || "").trim();
  if (!mediaId || /^(data|blob):/i.test(mediaId)) {
    mediaId = bubbleKey + "-img-" + imageIndex;
  }
  return {
    id: mediaId,
    path: mediaId.indexOf("-img-") >= 0 && mediaId.indexOf(bubbleKey) === 0 ? "" : mediaId,
    media_id: mediaId,
    previewUrl: src,
    signed_url: src,
    asset_type: "image",
    name: att.name || att.filename || ("image-" + (imageIndex + 1)),
    node_type: "file",
  };
}

function renderBubbleAttachments(container, attachments, bubbleKey) {
  if (!attachments || attachments.length === 0) return;
  var key = String(bubbleKey || container.dataset.bubbleKey || "").trim();
  if (!key) {
    key = "bubble-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8);
    container.dataset.bubbleKey = key;
  }
  var imageEntries = [];
  for (var j = 0; j < attachments.length; j++) {
    var attJ = attachments[j];
    if (/^image\//i.test(attJ.mime || attJ.mime_type || "")) {
      imageEntries.push(attJ);
    }
  }
  var bubbleImageNodes = imageEntries.map(function (attItem, imageIndex) {
    var srcItem = attItem.dataUrl || attItem.previewUrl || resolveFileUrl(attItem) || "";
    return _buildBubbleImageNode(attItem, srcItem, key, imageIndex);
  });
  var galleryPreviewKey = bubbleImageNodes.length > 1 ? key : "";
  var imgCounter = imageEntries.length;
  var wrap = document.createElement("div");
  wrap.className = "bubble-attachments";
  var imgSeq = 0;
  for (var i = 0; i < attachments.length; i++) {
    var att = attachments[i];
    var mime = att.mime || att.mime_type || "";
    var item = document.createElement("div");
    item.className = "bubble-att-item";
    if (/^image\//i.test(mime)) {
      imgSeq++;
      var imageIndex = imgSeq - 1;
      var node = bubbleImageNodes[imageIndex];
      var resolvedSrc = node.previewUrl || "";
      var srcKind = att.dataUrl ? "data_url" : att.previewUrl ? "preview_url" : resolvedSrc.indexOf("/media/") >= 0 ? "media_proxy" : resolvedSrc ? "other" : "empty";
      var img = document.createElement("img");
      img.className = "bubble-att-img";
      logAssetUrl("bubble_att_render", {
        mediaId: node.media_id || "",
        mime: mime,
        filename: att.name || att.filename || "",
        srcKind: srcKind,
        srcLen: resolvedSrc.length,
        hasDataUrl: !!att.dataUrl,
        extra: "is_image=1 idx=" + imgSeq,
      });
      img.src = resolvedSrc;
      img.alt = att.name || att.filename || "";
      (function (attRef, nodeRef, imageIdx, imgEl) {
        imgEl.addEventListener("load", function () {
          logAssetUrl("bubble_att_img_load", {
            mediaId: nodeRef.media_id || "",
            mime: mime,
            filename: attRef.name || attRef.filename || "",
            srcKind: srcKind,
            srcLen: (imgEl.src || "").length,
            hasDataUrl: !!attRef.dataUrl,
          });
        });
        imgEl.addEventListener("error", function () {
          logAssetUrl("bubble_att_img_error", {
            mediaId: nodeRef.media_id || "",
            mime: mime,
            filename: attRef.name || attRef.filename || "",
            srcKind: srcKind,
            srcLen: (imgEl.src || "").length,
            hasDataUrl: !!attRef.dataUrl,
            extra: "natural=" + imgEl.naturalWidth + "x" + imgEl.naturalHeight,
          });
        });
        imgEl.addEventListener("click", function (e) {
          e.stopPropagation();
          var src = imgEl.getAttribute("src") || imgEl.src || nodeRef.previewUrl || "";
          var previewNode = Object.assign({}, nodeRef, {
            previewUrl: src,
            signed_url: src,
            name: attRef.name || attRef.filename || nodeRef.name,
          });
          var openOpts = {};
          if (galleryPreviewKey && bubbleImageNodes.length > 1) {
            openOpts.previewKey = galleryPreviewKey;
            openOpts.imageNodes = bubbleImageNodes.map(function (n, idx) {
              if (idx === imageIdx) {
                return Object.assign({}, previewNode);
              }
              return Object.assign({}, n);
            });
            openOpts.imageIndex = imageIdx;
          }
          openPreview(previewNode, openOpts);
        });
      })(att, node, imageIndex, img);
      item.appendChild(img);
      if (imgCounter > 1) {
        var badge = document.createElement("span");
        badge.className = "img-index-badge";
        badge.textContent = imgSeq;
        item.appendChild(badge);
      }
    } else {
      logAssetUrl("bubble_att_render", {
        mediaId: att.media_id || "",
        mime: mime,
        filename: att.name || att.filename || "",
        srcKind: "non_image",
        srcLen: 0,
        hasDataUrl: !!att.dataUrl,
        extra: "is_image=0 idx=" + (i + 1),
      });
      var iconEl = document.createElement("div");
      iconEl.className = "bubble-att-icon";
      if (/^audio\//.test(mime)) iconEl.textContent = "\u{1F3B5}";
      else if (/^video\//.test(mime)) iconEl.textContent = "\u{1F3AC}";
      else if (/pdf/.test(mime)) iconEl.textContent = "\u{1F4C4}";
      else iconEl.textContent = "\u{1F4CE}";
      item.appendChild(iconEl);
      var nameEl = document.createElement("div");
      nameEl.className = "bubble-att-name";
      nameEl.textContent = att.name || att.filename || "file";
      nameEl.title = att.name || att.filename || "";
      item.appendChild(nameEl);
    }
    wrap.appendChild(item);
  }
  container.appendChild(wrap);
}

function getUserBubbleText(row) {
  var bubble = row.querySelector(".bubble");
  if (!bubble) return "";
  var textEl = bubble.querySelector(".bubble-text");
  if (textEl) return textEl.textContent || "";
  return bubble.textContent || "";
}

function getUserBubbleAttachments(row) {
  try {
    var raw = row.dataset.attachments;
    if (raw) return JSON.parse(raw);
  } catch (e) {}
  return [];
}

function _attachmentsForBubbleStorage(attachments) {
  if (!attachments || !attachments.length) return attachments;
  return attachments.map(function (a) {
    var mime = a.mime || a.mime_type || "";
    var row = {
      name: a.name || a.filename || "",
      mime: mime,
      size: a.size,
      media_id: a.media_id || "",
    };
    if (/^image\//i.test(mime)) {
      var src = a.previewUrl || a.dataUrl || "";
      if (src.indexOf("data:") === 0) {
        if (src.length <= 512000) row.previewUrl = src;
        else if (row.media_id) row.previewUrl = resolveFileUrl(Object.assign({}, row, { mime_type: mime, filename: row.name }));
      } else if (src) {
        row.previewUrl = src;
      } else if (row.media_id) {
        row.previewUrl = resolveFileUrl(Object.assign({}, row, { mime_type: mime, filename: row.name }));
      }
    } else if (row.media_id) {
      row.previewUrl = resolveFileUrl(Object.assign({}, row, { mime_type: mime, filename: row.name }));
    } else if (a.previewUrl && a.previewUrl.indexOf("data:") !== 0) {
      row.previewUrl = a.previewUrl;
    }
    return row;
  });
}

export function sendFailureText(reason) {
  if (reason === "connection_down") return "消息发送失败，网络未连接";
  if (reason === "ack_timeout") return "消息发送失败，网络超时";
  if (reason === "task_active") return "上一回合仍在进行中，请稍后重试";
  if (reason === "invalid_index" || reason === "message_not_found") return "消息定位失败，请刷新页面后重试";
  return "消息发送失败";
}

export function clearUserBubbleFailed(row) {
  if (!row) return;
  var bars = row.querySelectorAll(".send-failure, .send-queued, .send-superseded");
  for (var i = 0; i < bars.length; i++) {
    if (bars[i].parentNode) bars[i].parentNode.removeChild(bars[i]);
  }
  row.classList.remove("send-failed");
  row.classList.remove("send-queued-row");
  row.classList.remove("send-superseded-row");
}

export function markUserBubbleQueued(row) {
  if (!row) return;
  clearUserBubbleFailed(row);
  var wrap = row.querySelector(".bubble-wrap");
  if (!wrap) return;
  var bar = document.createElement("div");
  bar.className = "send-queued";
  bar.textContent = "网络断开，恢复连接后将自动发送";
  wrap.appendChild(bar);
  row.classList.add("send-queued-row");
  scrollToBottom();
}

export function markUserBubbleSuperseded(row) {
  if (!row) return;
  clearUserBubbleFailed(row);
  var wrap = row.querySelector(".bubble-wrap");
  if (!wrap) return;
  var bar = document.createElement("div");
  bar.className = "send-superseded";
  bar.textContent = "已被后一条消息取代，不会发送";
  wrap.appendChild(bar);
  row.classList.add("send-superseded-row");
}

export function markUserBubbleFailed(row, reason, onRetry) {
  if (!row) return;
  clearUserBubbleFailed(row);
  var wrap = row.querySelector(".bubble-wrap");
  if (!wrap) return;
  var bar = document.createElement("div");
  bar.className = "send-failure";
  var txt = document.createElement("span");
  txt.className = "send-failure-text";
  txt.textContent = sendFailureText(reason);
  bar.appendChild(txt);
  if (onRetry) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "send-failure-retry";
    btn.textContent = "重试";
    btn.onclick = function () {
      clearUserBubbleFailed(row);
      onRetry();
    };
    bar.appendChild(btn);
  }
  wrap.appendChild(bar);
  row.classList.add("send-failed");
  scrollToBottom();
}

function findUserRowByIndex(cs, userIndex) {
  if (!cs || !cs.el) return null;
  return cs.el.querySelector('.msg-row.user[data-user-index="' + String(userIndex) + '"]');
}

function resendRegenerate(threadId, userIndex, msgData) {
  var cs = getConvState(threadId);
  var row = findUserRowByIndex(cs, userIndex);
  if (!row) return;
  clearUserBubbleFailed(row);
  var textNode = row.querySelector(".bubble-text");
  if (textNode) textNode.textContent = msgData.newContent;
  var area = cs.el;
  var rowPos = Array.prototype.indexOf.call(area.children, row);
  while (area.children.length > rowPos + 1) {
    area.removeChild(area.lastChild);
  }
  area.dataset.userCount = String(userIndex);
  clearCheckpointCursorState(cs);
  blocksResetTurn(cs);
  cs.editingUserMessageIndex = userIndex;
  cs.pendingSend = true;
  cs.currentTaskId = null;
  cs.currentStreamBubble = null;
  showTurnWaiting(cs, "edit_and_regenerate_retry");
  syncComposerForConv(cs, "edit_and_regenerate_retry");
  syncSendButton();
  state.api.sendReliable("edit_and_regenerate", msgData, {
    onFail: function (reason) {
      handleRegenerateFailure(threadId, userIndex, msgData, reason);
    }
  });
}

function handleRegenerateFailure(threadId, userIndex, msgData, reason) {
  var cs = getConvState(threadId);
  if (cs) {
    removeTurnWaiting(cs);
    cs.pendingSend = false;
    cs.editingUserMessageIndex = null;
    cs.outboxFlushing = false;
    syncComposerForConv(cs, "regenerate_failed");
  }
  syncSendButton();
  if (state.api.isNetworkFailure(reason)) {
    var queuedRow = findUserRowByIndex(cs, userIndex);
    markUserBubbleQueued(queuedRow);
    state.api.enqueueOutbox(threadId, function () {
      resendRegenerate(threadId, userIndex, msgData);
    }, function () {
      markUserBubbleSuperseded(queuedRow);
    });
    return;
  }
  state.api.reloadThreadHistory(threadId, "regenerate_failed").then(function () {
    var target = getConvState(threadId);
    var row = findUserRowByIndex(target, userIndex);
    markUserBubbleFailed(row, reason, function () {
      resendRegenerate(threadId, userIndex, msgData);
    });
  });
}

export function clearThreadSendFailures(cs) {
  if (!cs || !cs.el) return;
  var rows = cs.el.querySelectorAll(".msg-row.send-failed, .msg-row.send-queued-row, .msg-row.send-superseded-row");
  for (var i = 0; i < rows.length; i++) {
    clearUserBubbleFailed(rows[i]);
  }
}

export function addUserBubble(content, attachmentsOrArea, targetArea, explicitUserIndex, opts) {
  var attachments = null;
  var area = null;
  var checkpointId = (opts && opts.checkpointId) || "";
  if (attachmentsOrArea instanceof HTMLElement) {
    area = attachmentsOrArea;
  } else if (Array.isArray(attachmentsOrArea)) {
    attachments = attachmentsOrArea;
    area = targetArea || getChatArea();
  } else {
    area = targetArea || getChatArea();
  }
  var knownCount = parseInt(area.dataset.userCount, 10) || 0;
  var explicitIdx = parseInt(explicitUserIndex, 10) || 0;
  const userIndex = explicitIdx > 0 ? explicitIdx : knownCount + 1;
  if (userIndex > knownCount) area.dataset.userCount = String(userIndex);
  const row = document.createElement("div");
  row.className = "msg-row user";
  row.dataset.userIndex = userIndex;
  row.dataset.bubbleKey = "bubble-" + userIndex + "-" + Date.now().toString(36);
  if (checkpointId) row.dataset.checkpointId = String(checkpointId);
  var storedAttachments = attachments && attachments.length > 0 ? _attachmentsForBubbleStorage(attachments) : attachments;
  if (storedAttachments && storedAttachments.length > 0) {
    row.dataset.attachments = JSON.stringify(storedAttachments);
  }
  const avatar = document.createElement("div");
  avatar.className = "avatar";
  const _u = state.currentUser && state.currentUser.userName;
  const _fc = _u ? _u.charAt(0) : "U";
  avatar.textContent = /[\u4e00-\u9fff]/.test(_fc) ? _fc : _fc.toUpperCase();
  const bubbleWrap = document.createElement("div");
  bubbleWrap.className = "bubble-wrap";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (storedAttachments && storedAttachments.length > 0) {
    renderBubbleAttachments(bubble, storedAttachments, row.dataset.bubbleKey);
  }
  if (content) {
    var textNode = document.createElement("div");
    textNode.className = "bubble-text";
    textNode.textContent = content;
    bubble.appendChild(textNode);
  }
  const actions = document.createElement("div");
  actions.className = "bubble-actions";
  const onEditSubmit = function (newContent, editedAttachments) {
    const host = row.parentNode;
    if (!host) throw new Error("user bubble detached before edit");
    const rowPos = Array.prototype.indexOf.call(host.children, row);
    while (host.children.length > rowPos + 1) {
      host.removeChild(host.lastChild);
    }
    host.dataset.userCount = String(parseInt(row.dataset.userIndex, 10) || 0);
    row.dataset.attachments = editedAttachments && editedAttachments.length > 0 ? JSON.stringify(editedAttachments) : "";
    var newBubble = row.querySelector(".bubble");
    if (newBubble) {
      newBubble.innerHTML = "";
      if (editedAttachments && editedAttachments.length > 0) renderBubbleAttachments(newBubble, editedAttachments, row.dataset.bubbleKey);
      if (newContent) {
        var t = document.createElement("div");
        t.className = "bubble-text";
        t.textContent = newContent;
        newBubble.appendChild(t);
      }
    }
    const cs = activeConv();
    const userIdx = parseInt(row.dataset.userIndex);
    if (cs) {
      clearCheckpointCursorState(cs);
      blocksResetTurn(cs);
      cs.editingUserMessageIndex = userIdx;
      cs.pendingSend = true;
      cs.currentTaskId = null;
      cs.currentStreamBubble = null;
      showTurnWaiting(cs, "edit_and_regenerate");
      syncComposerForConv(cs, "edit_and_regenerate");
    }
    var msgData = {
      userMessageIndex: userIdx,
      newContent: newContent,
      search: state.searchEnabled,
      mediaPassthrough: !!state.mediaPassthroughEnabled
    };
    Object.assign(msgData, getSendModelParams());
    msgData.planMode = isPlanModeOn();
    if (state.selectedSkill) {
      msgData.skill = state.selectedSkill;
    }
    if (editedAttachments && editedAttachments.length > 0) {
      msgData.attachments = editedAttachments.map(function (a) {
        return { media_id: a.media_id || "", filename: a.name || a.filename || "", mime_type: a.mime || a.mime_type || "" };
      });
    }
    msgData.threadId = state.currentConversationId;
    var threadId = msgData.threadId;
    state.api.sendReliable("edit_and_regenerate", msgData, {
      onFail: function (reason) {
        handleRegenerateFailure(threadId, userIdx, msgData, reason);
      }
    });
    syncSendButton();
  };
  const editBtn = createEditBtn(row, "\u53d1\u9001", onEditSubmit);
  bindBubbleEdit(row, bubble, "\u53d1\u9001", onEditSubmit);
  actions.appendChild(createRestoreBtn(row));
  actions.appendChild(editBtn);
  bubbleWrap.appendChild(bubble);
  bubbleWrap.appendChild(actions);
  row.appendChild(avatar);
  row.appendChild(bubbleWrap);
  area.appendChild(row);
  var isTargetArea = (attachmentsOrArea instanceof HTMLElement) || !!targetArea;
  if (!isTargetArea) scrollToBottom();
  return row;
}

function resizeEditTextarea(ta) {
  if (!ta) throw new Error("edit textarea missing");
  ta.style.height = "0px";
  var scrollH = ta.scrollHeight;
  var maxH = parseInt(window.getComputedStyle(ta).maxHeight, 10);
  var nextH = maxH > 0 && scrollH > maxH ? maxH : scrollH;
  ta.style.height = nextH + "px";
  ta.style.overflowY = maxH > 0 && scrollH > maxH ? "auto" : "hidden";
}

function bindEditTextareaResize(ta) {
  resizeEditTextarea(ta);
  ta.addEventListener("input", function () {
    resizeEditTextarea(ta);
  });
}

export function startInlineEdit(row, submitLabel, onSubmit) {
  const cs = activeConv();
  if (cs && (cs.isStreaming || cs.pendingSend)) return;
  const bubbleWrap = row.querySelector(".bubble-wrap");
  const bubble = row.querySelector(".bubble");
  const actions = row.querySelector(".bubble-actions");
  var isUser = row.classList.contains("user");
  var originalContent = isUser ? getUserBubbleText(row) : (bubble.dataset.raw || bubble.textContent);
  var originalAtts = isUser ? getUserBubbleAttachments(row) : [];
  const editWidth = Math.max(bubble.getBoundingClientRect().width, 260);
  var wasReverted = row.classList.contains("reverted");
  if (wasReverted) row.classList.remove("reverted");
  bubble.style.display = "none";
  if (actions) actions.style.display = "none";
  var editEls = [];
  var attArea = null;
  if (isUser) {
    attArea = buildEditAttachmentArea(originalAtts);
    attArea.style.width = editWidth + "px";
    bubbleWrap.appendChild(attArea);
    editEls.push(attArea);
  }
  const ta = document.createElement("textarea");
  ta.className = "edit-textarea";
  ta.rows = 1;
  ta.value = originalContent;
  ta.style.width = editWidth + "px";
  const controls = document.createElement("div");
  controls.className = "edit-controls";
  controls.style.width = editWidth + "px";
  const cancelBtn = document.createElement("button");
  cancelBtn.className = "cancel-edit-btn";
  cancelBtn.textContent = "\u53d6\u6d88";
  cancelBtn.onclick = function () {
    if (wasReverted) row.classList.add("reverted");
    bubble.style.display = "";
    if (actions) actions.style.display = "";
    editEls.forEach(function (el) { if (el.parentNode) el.parentNode.removeChild(el); });
    bubbleWrap.removeChild(ta);
    bubbleWrap.removeChild(controls);
  };
  const saveBtn = document.createElement("button");
  saveBtn.className = "submit-edit-btn";
  saveBtn.textContent = submitLabel;
  if (attArea) {
    attArea.setOnStateChange(function (uploading) {
      saveBtn.disabled = uploading;
      saveBtn.textContent = uploading ? "上传中..." : submitLabel;
    });
  }
  saveBtn.onclick = function () {
    if (attArea && attArea.isUploading()) return;
    const newContent = ta.value.trim();
    var editedAtts = attArea ? attArea.getAttachments() : [];
    if (!newContent && editedAtts.length === 0) return;
    if (row.classList.contains("ai")) {
      bubble.dataset.raw = newContent;
      bubble.innerHTML = formatContent(newContent);
      initImageLoaders(bubble);
    }
    bubble.style.display = "";
    if (actions) actions.style.display = "";
    editEls.forEach(function (el) { if (el.parentNode) el.parentNode.removeChild(el); });
    bubbleWrap.removeChild(ta);
    bubbleWrap.removeChild(controls);
    onSubmit(newContent, editedAtts);
  };
  controls.appendChild(cancelBtn);
  controls.appendChild(saveBtn);
  bubbleWrap.appendChild(ta);
  bubbleWrap.appendChild(controls);
  bindEditTextareaResize(ta);
  ta.focus();
  ta.setSelectionRange(0, 0);
}

const THINK_ICON_ACTIVE = '<img class="think-icon" src="/static/resource/think.jpg" alt="">';
const THINK_ICON_DONE = '<img class="think-icon" src="/static/resource/ok.jpg" alt="">';

export function addAiBubbleWithThink(targetArea) {
  const row = addAiBubble("", { loading: true }, targetArea);
  const bubbleWrap = row.querySelector(".bubble-wrap");
  const bubbleShell = getAiBubbleShell(row);
  const thinkBlock = document.createElement("div");
  thinkBlock.className = "think-block";
  thinkBlock.style.display = "none";
  const header = document.createElement("div");
  header.className = "think-header";
  header.innerHTML = THINK_ICON_ACTIVE + '<span class="think-title">思考中</span><span class="think-time"></span><span class="think-caret">&#9660;</span>';
  const body = document.createElement("div");
  body.className = "think-body";
  thinkBlock.appendChild(header);
  thinkBlock.appendChild(body);
  prepareThinkBlock(thinkBlock);
  bubbleWrap.insertBefore(thinkBlock, bubbleShell);
  syncCurrentPageThinkToggle(targetArea || row.closest(".chat-area"));
  return row;
}

export function setBubbleContent(row, text) {
  var bubble = row.querySelector(".bubble");
  var textTarget = bubble.querySelector(".media-text-area");
  if (textTarget) {
    textTarget.dataset.raw = text;
    textTarget.innerHTML = formatContent(text);
    initImageLoaders(textTarget);
  } else {
    bubble.classList.remove("is-loading", "is-media-loading");
    bubble.dataset.raw = text;
    bubble.innerHTML = formatContent(text);
    initImageLoaders(bubble);
  }
  refreshAiBubbleCopyState(row);
}

export function appendBubbleContent(row, text) {
  var bubble = row.querySelector(".bubble");
  var textTarget = bubble.querySelector(".media-text-area");
  if (textTarget) {
    var raw = (textTarget.dataset.raw || "") + text;
    textTarget.dataset.raw = raw;
    textTarget.innerHTML = formatContent(raw);
    initImageLoaders(textTarget);
    autoScrollIfFollowing();
  } else {
    bubble.classList.remove("is-loading", "is-media-loading");
    var raw = (bubble.dataset.raw || "") + text;
    bubble.dataset.raw = raw;
    bubble.innerHTML = formatContent(raw);
    initImageLoaders(bubble);
    autoScrollIfFollowing();
  }
  refreshAiBubbleCopyState(row);
}

export function setThinkContent(row, text) {
  let block = row.querySelector(".think-block");
  if (!block) {
    const bubbleWrap = row.querySelector(".bubble-wrap");
    const insertRef = getAiBubbleShell(row) || row.querySelector(".bubble");
    block = document.createElement("div");
    block.className = "think-block";
    const header = document.createElement("div");
    header.className = "think-header";
    header.innerHTML = THINK_ICON_ACTIVE + '<span class="think-title">思考中</span><span class="think-time"></span><span class="think-caret">&#9660;</span>';
    const body = document.createElement("div");
    body.className = "think-body";
    block.appendChild(header);
    block.appendChild(body);
    prepareThinkBlock(block);
    bubbleWrap.insertBefore(block, insertRef);
  }
  block.style.display = "block";
  applyThinkVisibilityPreference(block);
  const body = row.querySelector(".think-body");
  const existing = body.querySelector(".think-text");
  if (existing) {
    existing.innerHTML = formatThinkContent(text);
  } else {
    const el = document.createElement("div");
    el.className = "think-text";
    el.innerHTML = formatThinkContent(text);
    body.appendChild(el);
  }
  syncCurrentPageThinkToggle(row.closest(".chat-area"));
}

export function appendThinkContent(row, text) {
  const body = row.querySelector(".think-body");
  if (!body) {
    setThinkContent(row, text);
    return;
  }
  let el = body.querySelector(".think-text");
  if (!el) {
    el = document.createElement("div");
    el.className = "think-text";
    body.appendChild(el);
  }
  const full = (el.textContent || "") + text;
  el.innerHTML = formatThinkContent(full);
  autoScrollIfFollowing();
}

export function setThinkDone(row, seconds) {
  const header = row.querySelector(".think-header");
  const title = header.querySelector(".think-title");
  const timeEl = header.querySelector(".think-time");
  const iconEl = header.querySelector(".think-icon");
  if (iconEl) iconEl.src = "/static/resource/ok.jpg";
  title.textContent = "已思考";
  timeEl.textContent = seconds >= 0 ? " (用时 " + Math.round(seconds) + "秒)" : "";
}

export function hideThinkBlock(row) {
  const blk = row.querySelector(".think-block");
  if (blk) blk.style.display = "none";
  syncCurrentPageThinkToggle(row.closest(".chat-area"));
}

export function addAiBubble(text, options, targetArea) {
  const area = targetArea || getChatArea();
  const aiIndex = area.querySelectorAll(".msg-row.ai").length + 1;
  const row = document.createElement("div");
  row.className = "msg-row ai";
  row.dataset.aiIndex = aiIndex;
  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = "AI";
  const bubbleWrap = document.createElement("div");
  bubbleWrap.className = "bubble-wrap";
  const bubbleShell = document.createElement("div");
  bubbleShell.className = "ai-bubble-shell";
  const copyBtn = createAiCopyBtn(row);
  const thinkingContent = options && options.thinking;
  if (thinkingContent) {
    const thinkBlock = document.createElement("div");
    thinkBlock.className = "think-block";
    const header = document.createElement("div");
    header.className = "think-header";
    header.innerHTML = THINK_ICON_DONE + '<span class="think-title">已思考</span><span class="think-time"></span><span class="think-caret">&#9660;</span>';
    const body = document.createElement("div");
    body.className = "think-body";
    const thinkText = document.createElement("div");
    thinkText.className = "think-text";
    thinkText.innerHTML = formatThinkContent(thinkingContent);
    body.appendChild(thinkText);
    thinkBlock.appendChild(header);
    thinkBlock.appendChild(body);
    prepareThinkBlock(thinkBlock);
    if (options.thinkingSeconds != null && options.thinkingSeconds >= 0) {
      var timeEl = header.querySelector(".think-time");
      if (timeEl) timeEl.textContent = " (用时 " + Math.round(options.thinkingSeconds) + "秒)";
    }
    bubbleWrap.appendChild(thinkBlock);
  }
  const bubble = document.createElement("div");
  bubble.className = "bubble has-copy-btn";
  if (options && options.error) bubble.classList.add("error");
  if (options && options.loading) {
    bubble.classList.add("is-loading");
    bubble.textContent = "生成中...";
  } else if (options && options.raw) {
    bubble.textContent = text;
  } else {
    bubble.dataset.raw = text;
    bubble.innerHTML = formatContent(text);
    initImageLoaders(bubble);
  }
  const actions = document.createElement("div");
  actions.className = "bubble-actions";
  const onEditSubmit = function (newContent) {
    state.api.sendWsMsg("edit_ai_message", {
      aiMessageIndex: parseInt(row.dataset.aiIndex),
      newContent: newContent,
      threadId: state.currentConversationId
    });
  };
  const editBtn = createEditBtn(row, "保存", onEditSubmit);
  bindBubbleEdit(row, bubble, "保存", onEditSubmit);
  actions.appendChild(editBtn);
  bubbleShell.appendChild(copyBtn);
  bubbleShell.appendChild(bubble);
  bubbleWrap.appendChild(bubbleShell);
  bubbleWrap.appendChild(actions);
  if (options && options.modelLabel) {
    const modelTag = document.createElement("div");
    modelTag.className = "model-label";
    modelTag.textContent = options.modelLabel;
    bubbleWrap.appendChild(modelTag);
  }
  row.appendChild(avatar);
  row.appendChild(bubbleWrap);
  area.appendChild(row);
  refreshAiBubbleCopyState(row);
  syncCurrentPageThinkToggle(area);
  if (!targetArea) scrollToBottom();
  return row;
}

let _autoFollow = true;
let _scrollEl = null;

const AT_BOTTOM_THRESHOLD = 60;

function _isAtBottom() {
  if (!_scrollEl) return true;
  return _scrollEl.scrollHeight - _scrollEl.scrollTop - _scrollEl.clientHeight < AT_BOTTOM_THRESHOLD;
}

function _updateFollowBtn() {
  const btn = document.getElementById("scrollFollowBtn");
  if (!btn) return;
  const show = !_isAtBottom();
  if (show) {
    btn.style.display = "flex";
    requestAnimationFrame(function () { btn.classList.add("visible"); });
  } else {
    btn.classList.remove("visible");
    setTimeout(function () {
      if (_isAtBottom()) btn.style.display = "none";
    }, 200);
  }
}

function autoScrollIfFollowing() {
  if (!_autoFollow) return;
  if (_scrollEl) _scrollEl.scrollTop = _scrollEl.scrollHeight;
}

export function shouldAutoFollowScroll() {
  return _autoFollow || _isAtBottom();
}

export function enableAutoFollow() {
  _autoFollow = true;
  if (_scrollEl) _scrollEl.scrollTop = _scrollEl.scrollHeight;
  _updateFollowBtn();
}

export function initScrollFollowBtn() {
  _scrollEl = document.getElementById("chatScroll");
  const btn = document.getElementById("scrollFollowBtn");
  if (!_scrollEl || !btn) return;

  btn.onclick = function () {
    enableAutoFollow();
  };

  _scrollEl.addEventListener("scroll", function () {
    if (_isAtBottom()) {
      _autoFollow = true;
    } else {
      _autoFollow = false;
    }
    _updateFollowBtn();
  }, { passive: true });
}

export function scrollToBottom() {
  if (!shouldAutoFollowScroll()) return;
  const scroll = _scrollEl || document.getElementById("chatScroll");
  if (scroll) scroll.scrollTop = scroll.scrollHeight;
}

const SEND_ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>';
const STOP_ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';

export function setSendBtnStop() {
  const btn = document.getElementById("sendBtn");
  btn.innerHTML = STOP_ICON;
  btn.title = "停止";
  btn.className = "send-btn stop";
  btn.disabled = false;
}

export function setSendBtnSend() {
  const btn = document.getElementById("sendBtn");
  btn.innerHTML = SEND_ICON;
  btn.title = "发送";
  btn.className = "send-btn";
  btn.disabled = false;
}

export function syncSendButton() {
  var cs = activeConv();
  if (!cs) {
    setComposerLocked(false);
    setSendBtnSend();
    return;
  }
  var action = syncComposerForConv(cs);
  setComposerLocked(action.mode === "compacting");
  if (action.mode === "compacting") setSendBtnCompacting();
  else if (action.mode === "stop") setSendBtnStop();
  else if (action.mode === "waiting") setSendBtnWaiting();
  else setSendBtnSend();
}

function setComposerLocked(locked) {
  const input = document.getElementById("msgInput");
  if (!input) return;
  input.disabled = !!locked;
  input.classList.toggle("is-locked", !!locked);
  input.placeholder = locked
    ? "正在压缩上下文，稍候..."
    : "输入消息... (Enter发送, Shift+Enter换行)";
}

export function setSendBtnCompacting() {
  const btn = document.getElementById("sendBtn");
  btn.innerHTML = '<span class="send-btn-spinner"></span>';
  btn.title = "正在压缩上下文";
  btn.className = "send-btn waiting";
  btn.disabled = true;
}

export function setSendBtnWaiting() {
  const btn = document.getElementById("sendBtn");
  btn.innerHTML = '<span class="send-btn-spinner"></span>';
  btn.title = "发送中...";
  btn.className = "send-btn waiting";
  btn.disabled = true;
}

export function setSendBtnUploading() {
  const btn = document.getElementById("sendBtn");
  btn.innerHTML = '<span class="send-btn-spinner"></span>';
  btn.title = "上传中...";
  btn.className = "send-btn uploading";
  btn.disabled = true;
}

export function getChatAreaEl() {
  return getChatArea();
}

export function ensureToolStepMedia(toolRow, areaKey, toolName) {
  if (!toolRow || !areaKey) return null;
  var body = toolRow.querySelector(".tool-step-body");
  if (!body) return null;
  var slot = toolRow.querySelector(".tool-step-media");
  if (!slot) {
    slot = document.createElement("div");
    slot.className = "tool-step-media";
    var io = body.querySelector(".tool-step-io");
    if (io) body.insertBefore(slot, io);
    else body.insertBefore(slot, body.firstChild);
  }
  var existing = slot.querySelector('.media-image-area[data-bg-task-id="' + areaKey + '"]');
  if (!existing) {
    slot.appendChild(_buildMediaImageArea(areaKey, toolName));
  }
  body.hidden = false;
  body.style.display = "";
  var card = body.closest(".tool-step-card");
  if (card) card.classList.add("is-open");
  toolRow.classList.add("tool-step-has-media");
  return toolRow;
}

function _buildMediaImageArea(bgTaskId, _toolName) {
  var imageArea = document.createElement("div");
  imageArea.className = "media-image-area";
  imageArea.dataset.bgTaskId = bgTaskId;
  var overlay = document.createElement("div");
  overlay.className = "media-overlay";
  overlay.innerHTML =
    '<button type="button" class="media-task-btn media-task-refresh" title="\u5237\u65b0"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg></button>';
  overlay.querySelector(".media-task-refresh").onclick = function () {
    _reloadMediaElements(imageArea);
  };
  imageArea.appendChild(overlay);
  return imageArea;
}

function _bumpReloadParam(url) {
  var stripped = String(url || "").replace(/([?&])_r=\d+(&|$)/, function (_m, p1, p2) {
    return p2 ? p1 : "";
  });
  var sep = stripped.indexOf("?") === -1 ? "?" : "&";
  return stripped + sep + "_r=" + Date.now();
}

function _reloadMediaElements(imageArea) {
  var contentEl = imageArea.querySelector(".media-image-content");
  if (!contentEl) return;
  contentEl.querySelectorAll("img").forEach(function (img) {
    if (img.getAttribute("data-na-media-id")) {
      delete img.dataset.naLoadState;
      bindImage(img, "full");
      return;
    }
    var src = img.getAttribute("src") || "";
    if (!src || /^(blob|data):/i.test(src)) return;
    img.setAttribute("src", _bumpReloadParam(src));
  });
  contentEl.querySelectorAll("video, audio").forEach(function (mediaEl) {
    var source = mediaEl.querySelector("source[src]");
    if (source) source.setAttribute("src", _bumpReloadParam(source.getAttribute("src")));
    else if (mediaEl.getAttribute("src")) mediaEl.setAttribute("src", _bumpReloadParam(mediaEl.getAttribute("src")));
    mediaEl.load();
  });
}

function stampMediaVersionInRoot(root, mediaVersion) {
  var ver = String(mediaVersion || "");
  if (!root || !root.querySelectorAll || !ver) return;
  function addVer(selector, attr) {
    root.querySelectorAll(selector).forEach(function (el) {
      var u = el.getAttribute(attr) || "";
      if (!u || u.indexOf("/workspace/read") === -1) return;
      if (u.indexOf("_mv=") !== -1) return;
      var sep = u.indexOf("?") === -1 ? "?" : "&";
      el.setAttribute(attr, u + sep + "_mv=" + encodeURIComponent(ver));
    });
  }
  addVer("img[src]", "src");
  addVer("video[src], audio[src]", "src");
  addVer("video source[src], audio source[src]", "src");
}

function _retryMediaElementOnError(mediaEl) {
  if (!mediaEl) return;
  var source = mediaEl.querySelector("source");
  var getUrl = function () {
    return (source ? source.getAttribute("src") : null) || mediaEl.getAttribute("src") || "";
  };
  var baseUrl = getUrl();
  if (!baseUrl || baseUrl.indexOf("/workspace/read") === -1) return;
  var attempts = 0;
  var maxAttempts = 8;
  var onError = function () {
    if (attempts >= maxAttempts) return;
    attempts += 1;
    setTimeout(function () {
      var stripped = baseUrl.replace(/([?&])_r=\d+(&|$)/, function (_m, p1, p2) {
        return p2 ? p1 : "";
      });
      var sep = stripped.indexOf("?") === -1 ? "?" : "&";
      var next = stripped + sep + "_r=" + attempts;
      if (source) source.setAttribute("src", next);
      else mediaEl.setAttribute("src", next);
      try {
        mediaEl.load();
      } catch (e) {}
    }, Math.min(1000 * attempts, 4000));
  };
  if (source) source.addEventListener("error", onError);
  mediaEl.addEventListener("error", onError);
}

function _finishMediaTaskUiOnly(el, imageArea, contentEl, textContent) {
  if (textContent) {
    var textArea = el.querySelector(".media-text-area");
    if (textArea) {
      textArea.dataset.raw = textContent;
      textArea.innerHTML = formatContent(textContent);
      initImageLoaders(textArea);
    }
  }
  refreshAiBubbleCopyState(el);
}

export function finishMediaTaskBubble(el, contentHtml, bgTaskId, textContent, mediaVersion) {
  if (!el) return;
  var imageArea = bgTaskId ? el.querySelector('.media-image-area[data-bg-task-id="' + bgTaskId + '"]') : null;
  if (!imageArea) imageArea = el.querySelector(".media-image-area");
  if (!imageArea) return;

  var rawContent = decodeHtmlEntities(String(contentHtml || "")).trim();
  if (
    finishFileMediaBubble(el, rawContent, bgTaskId, textContent) ||
    finishFileMediaBubble(el, contentHtml, bgTaskId, textContent)
  ) {
    return;
  }
  if (
    rawContent.indexOf("chat-file-wrap") >= 0 ||
    /\[download:/i.test(rawContent) ||
    rawContent.indexOf('"kind":"file"') >= 0 ||
    rawContent.indexOf('"kind": "file"') >= 0
  ) {
    return;
  }
  var mediaHtml = extractMediaOnlyHtml(rawContent);
  if (!mediaHtml) {
    throw new Error("render_media \u5185\u5bb9\u65e0\u6cd5\u89e3\u6790\u4e3a\u5a92\u4f53: " + rawContent.slice(0, 200));
  }
  if (finishFileMediaBubble(el, mediaHtml, bgTaskId, textContent)) {
    return;
  }
  if (mediaHtml.indexOf("chat-file-wrap") >= 0) {
    return;
  }

  var contentEl = imageArea.querySelector(".media-image-content");
  if (!contentEl) {
    contentEl = document.createElement("div");
    contentEl.className = "media-image-content";
    imageArea.insertBefore(contentEl, imageArea.querySelector(".media-overlay"));
  }
  var readyKey = String(mediaHtml).trim();
  if (
    contentEl.dataset.mediaReadyHtml === readyKey &&
    contentEl.querySelector("video, audio, img")
  ) {
    _finishMediaTaskUiOnly(el, imageArea, contentEl, textContent);
    return;
  }
  contentEl.dataset.mediaReadyHtml = readyKey;
  contentEl.innerHTML = mediaHtml;
  imageArea.classList.remove("has-file");
  logAssetHtml("finish_innerHTML", mediaHtml);
  stampWorkspaceUrlsInRoot(contentEl);
  stampMediaVersionInRoot(contentEl, mediaVersion);
  bindImages(contentEl, "full");
  var previewWraps = Array.from(contentEl.querySelectorAll(".chat-image-wrap, .chat-media-wrap"));
  if (previewWraps.length > 1) {
    contentEl.classList.add("media-grid");
  } else {
    contentEl.classList.remove("media-grid");
  }
  if (previewWraps.length === 4) {
    contentEl.classList.add("media-quad-grid");
  } else {
    contentEl.classList.remove("media-quad-grid");
  }
  previewWraps.forEach(function (previewWrap) {
    previewWrap.classList.add("media-preview-wrap");
  });
  var previewImages = Array.from(contentEl.querySelectorAll("img"));
  previewImages.forEach(function (previewImage) {
    previewImage.classList.add("media-preview-media");
    previewImage.style.cursor = "pointer";
    snapshotImgSrc(previewImage, "finish_dom");
    previewImage.addEventListener("error", function () {
      snapshotImgSrc(previewImage, "finish_img_error");
      if (globalThis.__NA_DEV__) probeAssetImg(previewImage, "finish_img_error");
    });
    previewImage.addEventListener("load", function () {
      snapshotImgSrc(previewImage, "finish_img_load");
    });
    if (globalThis.__NA_DEV__) probeAssetImg(previewImage, "finish_dom");
    previewImage.addEventListener("click", function () {
      var node = previewNodeFromMediaSrc(previewImage.getAttribute("src") || previewImage.src || "");
      if (!node) return;
      openPreview(node);
    });
  });
  var previewVideos = Array.from(contentEl.querySelectorAll("video"));
  previewVideos.forEach(function (previewVideo) {
    previewVideo.classList.add("media-preview-media", "media-preview-video");
    previewVideo.playsInline = true;
    previewVideo.setAttribute("playsinline", "");
    _retryMediaElementOnError(previewVideo);
  });
  var previewAudios = Array.from(contentEl.querySelectorAll("audio"));
  previewAudios.forEach(function (previewAudio) {
    previewAudio.classList.add("media-preview-media", "media-preview-audio");
    _retryMediaElementOnError(previewAudio);
  });
  initImageLoaders(contentEl);
  var imgEl = previewImages[0];
  if (imgEl) {
    var _applyBlurBg = function (src) {
      if (!src) return;
      imageArea.style.setProperty("--media-bg-src", "url(" + src + ")");
      imageArea.classList.add("has-image");
    };
    var blurMediaId = imgEl.getAttribute("data-na-media-id") || "";
    if (/^[a-f0-9]{32}$/.test(blurMediaId)) {
      resolveBlobUrl(blurMediaId, "thumb").then(function (blobUrl) {
        if (blobUrl) _applyBlurBg(blobUrl);
      });
    } else if (imgEl.complete && imgEl.naturalWidth > 0) {
      _applyBlurBg(imgEl.src);
    } else {
      imgEl.addEventListener("load", function () {
        _applyBlurBg(imgEl.src);
      });
    }
  }
  if (textContent) {
    var textArea = el.querySelector(".media-text-area");
    if (textArea) {
      textArea.dataset.raw = textContent;
      textArea.innerHTML = formatContent(textContent);
      initImageLoaders(textArea);
    }
  }
  refreshAiBubbleCopyState(el);
}
