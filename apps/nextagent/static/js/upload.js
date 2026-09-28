import { API_BASE } from "./config.js";
import { coerceWorkspacePath, resolveFileUrl } from "./asset-url.js";
import { naFetch } from "./http.js";

export function uploadFile(file, options) {
  options = options || {};
  var onProgress = options.onProgress || function () {};
  var cancelled = false;
  var abortCtrl = null;
  onProgress(0);

  var promise = (async function () {
    abortCtrl = new AbortController();
    var data = await _uploadDirect(file, Object.assign({}, options, { signal: abortCtrl.signal }));
    if (cancelled) throw new Error("cancelled");
    onProgress(1);
    return data;
  })();

  promise.cancel = function () {
    cancelled = true;
    if (abortCtrl) abortCtrl.abort();
  };

  return promise;
}

export function uploadAndRegisterAsset(file, options) {
  options = options || {};
  var cancelled = false;
  var abortCtrl = null;
  var parentPath = coerceWorkspacePath(options.parentPath);

  var promise = (async function () {
    abortCtrl = new AbortController();
    var data = await _uploadDirect(file, Object.assign({}, options, {
      parentPath: parentPath,
      signal: abortCtrl.signal,
    }));
    if (cancelled) throw new Error("cancelled");
    var mime = data.mime_type || file.type || "";
    var assetType = "file";
    if (mime.startsWith("image/")) assetType = "image";
    else if (mime.startsWith("video/")) assetType = "video";
    else if (mime.startsWith("audio/")) assetType = "audio";
    return {
      media_id: data.media_id,
      filename: data.filename,
      mime_type: data.mime_type,
      size: data.size,
      asset_type: assetType,
      node: data.node || null,
    };
  })();

  promise.cancel = function () {
    cancelled = true;
    if (abortCtrl) abortCtrl.abort();
  };

  return promise;
}

export async function downloadAsset(node) {
  if (!node || !node.media_id) return;
  var url = resolveFileUrl(node);
  if (!url) return;
  var resp = await naFetch(url);
  var blob = await resp.blob();
  var a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = node.name || "download";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  URL.revokeObjectURL(a.href);
  document.body.removeChild(a);
}

export function downloadMedia(mediaId, filename) {
  if (!mediaId) return;
  downloadAsset({ media_id: mediaId, name: filename || "download" });
}

async function _checkResp(resp, label) {
  if (!resp.ok) {
    var text = "";
    try { text = await resp.text(); } catch (e) {}
    var detail = "";
    try { detail = JSON.parse(text).detail || JSON.parse(text).error || ""; } catch (e) {}
    throw new Error(label + ": " + (detail || "HTTP " + resp.status));
  }
  var data = await resp.json();
  if (data.error) throw new Error(data.error);
  return data;
}

async function _uploadDirect(file, options) {
  options = options || {};
  var parentPath = coerceWorkspacePath(options.parentPath);
  var formData = new FormData();
  formData.append("file", file);
  var url = API_BASE + "/workspace/upload?parent=" + encodeURIComponent(parentPath);
  var fetchInit = { method: "POST", body: formData };
  if (options.signal) fetchInit.signal = options.signal;
  var resp = await naFetch(url, fetchInit);
  return _checkResp(resp, "asset upload");
}
