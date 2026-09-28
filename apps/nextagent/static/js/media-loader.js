import { mediaIdFromHttpUrl, mediaUrl, workspaceReadUrl, resolveFileUrl } from "./asset-url.js";
import {
  ensureNode,
  resolveBlobUrl,
  resolveDisplayUrl,
  upsertNode,
} from "./asset-catalog.js";

var _MEDIA_ID_RE = /^[a-f0-9]{32}$/i;

function _mediaIdFromImg(img) {
  if (!img) return "";
  var attr = img.getAttribute("data-na-media-id") || "";
  if (_MEDIA_ID_RE.test(attr)) return attr.toLowerCase();
  var src = img.getAttribute("src") || img.src || "";
  return mediaIdFromHttpUrl(src);
}

export function mediaIdFromNode(node) {
  if (!node) return "";
  var fields = [node.media_id, node.id];
  for (var i = 0; i < fields.length; i++) {
    var s = String(fields[i] || "").trim();
    if (_MEDIA_ID_RE.test(s)) return s.toLowerCase();
  }
  var fromUrl = mediaIdFromHttpUrl(String(node.media_id || node.id || ""));
  if (_MEDIA_ID_RE.test(fromUrl)) return fromUrl.toLowerCase();
  return "";
}

function _markPending(img) {
  img.dataset.naLoadState = "pending";
}

function _markDone(img) {
  img.dataset.naLoadState = "done";
}

function _markFail(img) {
  img.dataset.naLoadState = "error";
}

async function _bindOne(img, variant) {
  if (!img || img.dataset.naLoadState === "pending") return;
  var mediaId = _mediaIdFromImg(img);
  if (!_MEDIA_ID_RE.test(mediaId)) return;
  img.setAttribute("data-na-media-id", mediaId);
  var direct = resolveDisplayUrl(mediaId, variant);
  if (direct && direct.indexOf("http") === 0) {
    _markPending(img);
    try {
      var blobUrl = await resolveBlobUrl(mediaId, variant);
      if (blobUrl) {
        img.src = blobUrl;
        _markDone(img);
        return;
      }
    } catch (e) {
      _markFail(img);
      throw e;
    }
  }
  _markPending(img);
  try {
    await ensureNode(mediaId);
    var url = await resolveBlobUrl(mediaId, variant);
    if (!url) {
      _markFail(img);
      return;
    }
    img.src = url;
    _markDone(img);
  } catch (e) {
    _markFail(img);
    throw e;
  }
}

export function bindImage(img, variant) {
  if (!img) return Promise.resolve();
  return _bindOne(img, variant || "thumb");
}

export function bindImages(root, variant) {
  if (!root || !root.querySelectorAll) return Promise.resolve();
  var imgs = Array.from(root.querySelectorAll("img"));
  var tasks = imgs.map(function (img) {
    return _bindOne(img, variant || "thumb");
  });
  return Promise.all(tasks);
}

export function bindThumb(img, node) {
  if (!img) return Promise.resolve();
  if (node) upsertNode(node);
  var mediaId = mediaIdFromNode(node);
  if (mediaId) {
    img.setAttribute("data-na-media-id", mediaId);
    return bindImage(img, "thumb");
  }
  var directUrl =
    (node && (node.previewUrl || node.signed_url || node.thumb_url) && mediaUrl(node.previewUrl || node.signed_url || node.thumb_url)) ||
    resolveFileUrl(node) ||
    workspaceReadUrl(node && (node.path || node.media_id || node.id));
  if (directUrl) {
    img.src = directUrl;
    img.dataset.naLoadState = "done";
  }
  return Promise.resolve();
}

export function bindPreviewImage(img, node) {
  if (!img) return Promise.resolve();
  if (node) upsertNode(node);
  var inlinePreview = node && (node.previewUrl || node.signed_url || node.thumb_url || "");
  if (/^(data|blob):/i.test(String(inlinePreview))) {
    img.src = String(inlinePreview);
    img.dataset.naLoadState = "done";
    return Promise.resolve();
  }
  if (inlinePreview && /^https?:\/\//i.test(String(inlinePreview))) {
    img.src = String(inlinePreview);
    img.dataset.naLoadState = "done";
    return Promise.resolve();
  }
  var mediaId = mediaIdFromNode(node);
  if (mediaId) {
    img.setAttribute("data-na-media-id", mediaId);
    return bindImage(img, "full").then(function () {
      if (img.dataset.naLoadState === "done" && img.src) return;
      var proxyUrl = mediaUrl(mediaId);
      if (proxyUrl) {
        img.src = proxyUrl;
        img.dataset.naLoadState = "done";
      }
    });
  }
  var directUrl = resolveFileUrl(node) || workspaceReadUrl(node.path || node.id);
  if (directUrl) {
    img.src = directUrl;
    img.dataset.naLoadState = "done";
  }
  return Promise.resolve();
}

export function stampMediaIdsInRoot(root) {
  if (!root || !root.querySelectorAll) return;
  root.querySelectorAll("img[src]").forEach(function (img) {
    var mediaId = _mediaIdFromImg(img);
    if (!_MEDIA_ID_RE.test(mediaId)) return;
    img.setAttribute("data-na-media-id", mediaId);
    if (img.getAttribute("src") && img.getAttribute("src").indexOf("/media/") >= 0) {
      img.removeAttribute("src");
    }
  });
}
