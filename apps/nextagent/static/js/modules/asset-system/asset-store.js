import { API_BASE } from "../../config.js";
import { naFetch } from "../../http.js";
import { state, getConvState } from "../../state.js";
import { uploadAndRegisterAsset, uploadFile } from "../../upload.js";
import { coerceWorkspacePath, joinPath, parentWorkspacePath, workspaceRoot } from "../../asset-url.js";

export var CHAT_UPLOAD_DIR = "temp";

export function ASSET_ROOT() {
  return workspaceRoot();
}

var _currentPath = "";
var _nodes = [];
var _listeners = [];
var _loadSeq = 0;
var _loadAbort = null;
var _loading = false;
var _pendingPath = "";
var _treeCache = {};

function _samePath(a, b) {
  return String(a || "").replace(/\/+$/, "") === String(b || "").replace(/\/+$/, "");
}

function _sortNodes(nodes) {
  return (nodes || []).slice().sort(function (a, b) {
    if (a.node_type !== b.node_type) {
      return a.node_type === "folder" ? -1 : 1;
    }
    return String(a.name || "").localeCompare(String(b.name || ""));
  });
}

function _upsertNode(nodes, node) {
  var next = (nodes || []).slice();
  var idx = -1;
  for (var i = 0; i < next.length; i++) {
    if (_samePath(next[i].path, node.path) || (next[i].media_id && node.media_id && next[i].media_id === node.media_id)) {
      idx = i;
      break;
    }
  }
  if (idx >= 0) {
    next[idx] = node;
  } else {
    next.push(node);
  }
  return _sortNodes(next);
}

function _removeNodeAtPath(nodes, path) {
  return (nodes || []).filter(function (n) {
    return !_samePath(n.path, path);
  });
}

function _patchCurrentDir(mutator) {
  _nodes = mutator(_nodes);
  _treeCache[_currentPath] = _cloneNodes(_nodes);
  _notify();
}

export function applyNodeFromApi(node) {
  if (!node || !node.path) return;
  var parent = parentWorkspacePath(node.path);
  if (!_samePath(_currentPath, parent)) return;
  _patchCurrentDir(function (nodes) {
    return _upsertNode(nodes, node);
  });
}

export function handleAssetEvent(msg) {
  var type = msg.type || "";
  var data = msg.data || {};
  var threadId = String(data.threadId || "").trim();
  if (threadId && threadId !== getEffectiveThreadId()) return;
  if (type === "workspace_node_created" || type === "workspace_node_updated") {
    if (data.node && data.node.path) {
      delete _treeCache[parentWorkspacePath(data.node.path)];
      applyNodeFromApi(data.node);
    }
    return;
  }
  if (type === "workspace_node_deleted") {
    var path = String(data.path || "");
    var parentPath = String(data.parent_path || parentWorkspacePath(path));
    delete _treeCache[parentPath];
    if (!_samePath(_currentPath, parentPath)) return;
    _patchCurrentDir(function (nodes) {
      return _removeNodeAtPath(nodes, path);
    });
  }
}

export function isAssetTreeLoading() {
  return _loading;
}

export function getBrowsePath() {
  return _pendingPath || _currentPath;
}

function _cloneNodes(nodes) {
  return (nodes || []).slice();
}

export function sessionRoot(threadId) {
  void threadId;
  return ASSET_ROOT();
}

export function resolveUploadParentPath() {
  return _currentPath || ASSET_ROOT();
}

export function resolveChatUploadParentPath() {
  return joinPath(workspaceRoot(), CHAT_UPLOAD_DIR);
}

export function joinWorkspacePath(parent, name) {
  var n = String(name || "").trim();
  if (!n) throw new Error("name required");
  return joinPath(coerceWorkspacePath(parent), n);
}

export function breadcrumbRootPath() {
  return "/";
}

export function breadcrumbRelativeParts(path, rootPath) {
  void rootPath;
  var abs = coerceWorkspacePath(path);
  if (abs === "/") return [];
  var m = abs.match(/^([A-Z]:)\/(.*)$/);
  if (m) {
    return [m[1] + "/"].concat(m[2].split("/").filter(Boolean));
  }
  return abs.split("/").filter(Boolean);
}

export function buildPathFromRoot(rootPath, parts) {
  var list = (parts || []).slice();
  if (!list.length) return "/";
  var path = list[0].indexOf(":") > 0 ? list.shift() : String(rootPath || "/");
  list.forEach(function (part) {
    path = joinPath(path, part);
  });
  return path;
}

export function getCurrentPath() { return _currentPath; }
export function getNodes() { return _nodes; }

export function getEffectiveThreadId() {
  return state.currentConversationId || "";
}

function _resolveBrowsePath(threadId, path) {
  void threadId;
  var raw = path !== undefined ? path : _currentPath;
  raw = String(raw || "").trim();
  if (!raw) {
    return ASSET_ROOT();
  }
  return coerceWorkspacePath(raw);
}

export function subscribe(fn) {
  _listeners.push(fn);
  return function () { _listeners = _listeners.filter(function (f) { return f !== fn; }); };
}

function _notify() {
  _listeners.forEach(function (fn) { fn(_nodes, _currentPath); });
}

function _invalidateCachedPaths(paths) {
  var seen = {};
  function drop(p) {
    var key = String(p || "").trim();
    if (!key || seen[key]) return;
    seen[key] = true;
    delete _treeCache[key];
  }
  drop(_currentPath);
  (paths || []).forEach(function (raw) {
    drop(raw);
    drop(parentWorkspacePath(raw));
  });
}

export async function refreshAfterAssetMutation(threadId, paths, reloadPath) {
  _invalidateCachedPaths(paths);
  var targetPath = reloadPath !== undefined ? reloadPath : _currentPath;
  await loadTree(threadId, targetPath);
}

async function _readJson(resp) {
  var data = await resp.json();
  if (!resp.ok || data.ok === false) {
    throw new Error(data.detail || data.error || ("HTTP " + resp.status));
  }
  return data;
}

function _blockDiag(event, fields) {
  var parts = ["[block_diag]", "t=" + performance.now().toFixed(0), event];
  if (fields) {
    Object.keys(fields).forEach(function (k) {
      parts.push(k + "=" + fields[k]);
    });
  }
  console.log(parts.join(" "));
}

export async function loadTree(threadId, path, options) {
  options = options || {};
  var targetPath = _resolveBrowsePath(threadId, path);
  var url = API_BASE + "/workspace/list?path=" + encodeURIComponent(targetPath);
  var loadT0 = performance.now();
  var convId = state.currentConversationId || "";
  var cs = convId ? getConvState(convId) : null;
  _blockDiag("asset_loadTree_enter", {
    path: targetPath,
    isStreaming: cs ? !!cs.isStreaming : "-",
    currentTaskId: cs && cs.currentTaskId ? cs.currentTaskId : "",
  });

  if (options.background) {
    if (_loading) return null;
    try {
      var bgData = await _readJson(await naFetch(url));
      _treeCache[targetPath] = _cloneNodes(bgData.nodes || []);
      if (_samePath(_currentPath, targetPath)) {
        _nodes = _cloneNodes(_treeCache[targetPath]);
        _notify();
      }
      return bgData;
    } catch (err) {
      return null;
    }
  }

  var seq = ++_loadSeq;
  if (_loadAbort) _loadAbort.abort();
  var ctrl = new AbortController();
  _loadAbort = ctrl;
  _loading = true;
  _pendingPath = targetPath;
  var cached = _treeCache[targetPath];
  if (cached) {
    _currentPath = targetPath;
    _nodes = _cloneNodes(cached);
    _pendingPath = "";
    _notify();
  }
  try {
    var data = await _readJson(await naFetch(url, { signal: ctrl.signal }));
    if (seq !== _loadSeq) return data;
    _currentPath = targetPath;
    _pendingPath = "";
    _nodes = data.nodes || [];
    _treeCache[targetPath] = _cloneNodes(_nodes);
    _notify();
    _blockDiag("asset_loadTree_done", {
      path: targetPath,
      nodes: (_nodes || []).length,
      ms: Math.round(performance.now() - loadT0),
    });
    return data;
  } catch (err) {
    if (seq !== _loadSeq) return null;
    _pendingPath = "";
    _notify();
    if (err && err.name === "AbortError") return null;
    _blockDiag("asset_loadTree_error", {
      path: targetPath,
      ms: Math.round(performance.now() - loadT0),
      err: err && err.message ? err.message : String(err),
    });
    throw err;
  } finally {
    if (_loadAbort === ctrl) {
      _loadAbort = null;
      _loading = false;
    }
  }
}

async function _fetchTreeIntoCache(threadId, path) {
  var targetPath = _resolveBrowsePath(threadId, path);
  if (_treeCache[targetPath]) return _treeCache[targetPath];
  var url = API_BASE + "/workspace/list?path=" + encodeURIComponent(targetPath);
  var data = await _readJson(await naFetch(url));
  _treeCache[targetPath] = _cloneNodes(data.nodes || []);
  return _treeCache[targetPath];
}

export async function fetchSiblingImageNodes(threadId, node) {
  if (!node || !node.path) return [];
  var parentPath = parentWorkspacePath(node.path);
  if (!_treeCache[parentPath]) {
    await loadTree(threadId, parentPath);
  }
  var nodes = _samePath(_currentPath, parentPath) ? _nodes : (_treeCache[parentPath] || []);
  return (nodes || []).filter(function (n) {
    return n && n.node_type === "file" && n.asset_type === "image" && n.media_id;
  });
}

export async function navigateTo(threadId, path) {
  await loadTree(threadId, path);
}

export async function goToSessionRoot(threadId) {
  await loadTree(threadId, sessionRoot(threadId));
}

export async function createFolder(threadId, name) {
  var target = joinWorkspacePath(_currentPath, name);
  var resp = await naFetch(API_BASE + "/workspace/mkdir", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: target }),
  });
  var data = await _readJson(resp);
  await refreshAfterAssetMutation(threadId, [target]);
  return data;
}

export async function renameNode(threadId, assetUri, newName) {
  var parent = parentWorkspacePath(assetUri);
  var resp = await naFetch(API_BASE + "/workspace/rename", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: assetUri, new_name: newName }),
  });
  var data = await _readJson(resp);
  await refreshAfterAssetMutation(threadId, [assetUri, joinWorkspacePath(parent, newName), parent]);
  return data;
}

export async function batchDeleteNodes(threadId, assetUris) {
  var resp = await naFetch(API_BASE + "/workspace/batch-delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths: assetUris }),
  });
  var data = await _readJson(resp);
  if (data.deletedThreads && data.deletedThreads.length) {
    window.dispatchEvent(new CustomEvent("na:threads-deleted", { detail: data }));
  }
  await refreshAfterAssetMutation(threadId, assetUris || []);
  return data;
}

export async function transferAssets(sources, target, operation) {
  var resp = await naFetch(API_BASE + "/workspace/transfer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      sources: sources,
      target: target,
      operation: operation,
    }),
  });
  var data = await _readJson(resp);
  var paths = (sources || []).slice();
  if (target) paths.push(target);
  await refreshAfterAssetMutation(getEffectiveThreadId(), paths);
  return data;
}

export async function uploadAndCreateNode(threadId, file, options) {
  options = options || {};
  var parentPath = options.parentPath || _currentPath;
  var result = await uploadAndRegisterAsset(file, {
    onProgress: options.onProgress,
    parentPath: parentPath,
  });
  if (result && result.node) applyNodeFromApi(result.node);
  return { ok: true, node: result.node };
}

export async function replaceNodeMedia(threadId, nodeId, file, options) {
  options = options || {};
  var targetUri = String(options.targetPath || nodeId || "").trim();
  if (!targetUri) {
    throw new Error("target path required");
  }
  var parentPath = options.parentPath || _currentPath;
  if (options.nodePath) {
    parentPath = parentWorkspacePath(options.nodePath);
  }
  var uploadData = await uploadFile(file, { parentPath: parentPath });
  var sourceUri = String(
    (uploadData.node && (uploadData.node.path || uploadData.node.id))
    || ""
  ).trim();
  if (!sourceUri) {
    throw new Error("upload did not return path");
  }
  var resp = await naFetch(API_BASE + "/workspace/replace", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      path: targetUri,
      source: sourceUri,
    }),
  });
  var data = await _readJson(resp);
  if (data.node) applyNodeFromApi(data.node);
  await refreshAfterAssetMutation(threadId, [targetUri, sourceUri, parentPath]);
  return {
    ok: true,
    node: data.node || null,
    media_id: uploadData.media_id,
    filename: uploadData.filename || "",
    mime_type: uploadData.mime_type || "",
    size: uploadData.size || 0,
  };
}

export function reset() {
  _currentPath = "";
  _pendingPath = "";
  _nodes = [];
  _treeCache = {};
  _loadSeq += 1;
  if (_loadAbort) _loadAbort.abort();
  _notify();
}
