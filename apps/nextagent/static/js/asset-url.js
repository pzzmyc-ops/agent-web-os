import { API_BASE } from "./config.js";
import { resolveDisplayUrl } from "./asset-catalog.js";
import { naFetch } from "./http.js";

var _workspaceRoot = "";
var _roots = [];

export function workspaceRoot() {
  if (!_workspaceRoot) {
    throw new Error("workspace env not loaded");
  }
  return _workspaceRoot;
}

export function driveRoots() {
  return _roots.slice();
}

export async function loadWorkspaceEnv() {
  var resp = await naFetch(API_BASE + "/workspace/info");
  var data = await resp.json();
  if (!resp.ok || !data.workspace) {
    throw new Error("加载工作区信息失败: HTTP " + resp.status);
  }
  _workspaceRoot = normalizeAbsolutePath(data.workspace);
  _roots = (data.roots || []).map(normalizeAbsolutePath);
  return { workspace: _workspaceRoot, roots: _roots.slice() };
}

export function isAbsolutePath(raw) {
  var s = String(raw || "");
  return /^[A-Za-z]:(\/|\\|$)/.test(s) || s.charAt(0) === "/" || s.charAt(0) === "\\";
}

export function isRootPath(path) {
  var s = String(path || "");
  return s === "/" || /^[A-Z]:\/$/.test(s);
}

function _collapse(segments) {
  var out = [];
  segments.forEach(function (seg) {
    if (!seg || seg === ".") return;
    if (seg === "..") {
      if (out.length) out.pop();
      return;
    }
    out.push(seg);
  });
  return out;
}

export function normalizeAbsolutePath(raw) {
  var s = String(raw || "").trim().replace(/\\/g, "/");
  if (!s) {
    throw new Error("path is required");
  }
  var m = s.match(/^([A-Za-z]):(?:\/(.*))?$/);
  if (m) {
    var tail = _collapse(String(m[2] || "").split("/")).join("/");
    return m[1].toUpperCase() + ":/" + tail;
  }
  if (s.charAt(0) !== "/") {
    throw new Error("path must be absolute: " + raw);
  }
  var parts = _collapse(s.split("/"));
  return "/" + parts.join("/");
}

export function joinPath(parent, name) {
  var p = normalizeAbsolutePath(parent);
  var n = String(name || "").trim().replace(/\\/g, "/").replace(/^\/+/, "");
  if (!n) return p;
  return normalizeAbsolutePath(p.replace(/\/+$/, "") + "/" + n);
}

export function pathBaseName(path) {
  var s = String(path || "");
  if (isRootPath(s)) return s.replace(/\/$/, "") || "/";
  var parts = s.split("/").filter(Boolean);
  return parts.length ? parts[parts.length - 1] : s;
}

export function coerceWorkspacePath(raw) {
  var s = String(raw || "").trim();
  if (/^(data|blob|https?):/i.test(s)) {
    return "";
  }
  if (s.indexOf("/media/") === 0) {
    var part = s.slice("/media/".length).split("?")[0].split("#")[0];
    try {
      part = decodeURIComponent(part);
    } catch (e) {}
    s = part;
  }
  if (!s || s === ".") {
    return workspaceRoot();
  }
  if (isAbsolutePath(s)) {
    return normalizeAbsolutePath(s);
  }
  return joinPath(workspaceRoot(), s);
}

export function parentWorkspacePath(path) {
  var s = coerceWorkspacePath(path);
  if (!s || s === "/") {
    return "/";
  }
  if (isRootPath(s)) {
    return "/";
  }
  var idx = s.lastIndexOf("/");
  var head = s.slice(0, idx);
  if (/^[A-Z]:$/.test(head)) return head + "/";
  return head || "/";
}

export function workspaceReadUrl(path) {
  var abs = coerceWorkspacePath(path);
  if (!abs || isRootPath(abs)) {
    return "";
  }
  return API_BASE + "/workspace/read?path=" + encodeURIComponent(abs);
}

export function resolveWorkspacePath(nodeOrUri) {
  if (!nodeOrUri) {
    return "";
  }
  if (typeof nodeOrUri === "string") {
    return coerceWorkspacePath(nodeOrUri);
  }
  var fields = [nodeOrUri.path, nodeOrUri.id, nodeOrUri.media_id];
  for (var i = 0; i < fields.length; i++) {
    var raw = String(fields[i] || "").trim();
    if (!raw) continue;
    var abs = coerceWorkspacePath(raw);
    if (abs) {
      return abs;
    }
  }
  return "";
}

export function formatWorkspaceAgentPath(nodeOrUri, browsePath) {
  var abs = resolveWorkspacePath(nodeOrUri);
  if (!abs && nodeOrUri && typeof nodeOrUri === "object") {
    var name = String(nodeOrUri.name || "").trim();
    if (name) {
      abs = joinPath(coerceWorkspacePath(browsePath), name);
    }
  }
  return abs || "";
}

export function mediaUrl(raw) {
  var s = String(raw || "").trim();
  if (!s) {
    return "";
  }
  if (/^(data|blob):/i.test(s)) {
    return s;
  }
  if (/^https?:\/\//i.test(s)) {
    return s;
  }
  if (s.indexOf("/workspace/read") === 0) {
    return s.indexOf(API_BASE) === 0 ? s : API_BASE + s;
  }
  return workspaceReadUrl(s);
}

export function resolveFileUrl(nodeOrUri) {
  if (!nodeOrUri) {
    return "";
  }
  if (typeof nodeOrUri === "object") {
    if (nodeOrUri.thumb_url && String(nodeOrUri.thumb_url).indexOf("http") === 0) {
      return String(nodeOrUri.thumb_url);
    }
    if (nodeOrUri.signed_url && String(nodeOrUri.signed_url).indexOf("/") === 0) {
      return nodeOrUri.signed_url.indexOf(API_BASE) === 0 ? nodeOrUri.signed_url : API_BASE + nodeOrUri.signed_url;
    }
    var objMid = String(nodeOrUri.media_id || nodeOrUri.path || "").trim();
    if (objMid) {
      var absObj = coerceWorkspacePath(objMid);
      if (absObj) {
        var fromCat = resolveDisplayUrl(absObj, "thumb");
        if (fromCat && fromCat.indexOf("http") === 0) return fromCat;
      }
    }
  }
  if (typeof nodeOrUri === "object" && nodeOrUri.previewUrl) {
    var mediaFromPreview = mediaUrl(nodeOrUri.previewUrl);
    if (mediaFromPreview) {
      return mediaFromPreview;
    }
  }
  if (typeof nodeOrUri === "object") {
    var mid = String(nodeOrUri.media_id || nodeOrUri.path || "").trim();
    if (mid) {
      var fromMediaId = mediaUrl(mid);
      if (fromMediaId) {
        return fromMediaId;
      }
    }
  }
  if (typeof nodeOrUri === "string") {
    var fromRaw = mediaUrl(nodeOrUri);
    if (fromRaw) {
      return fromRaw;
    }
  }
  var abs = resolveWorkspacePath(nodeOrUri);
  return abs ? workspaceReadUrl(abs) : "";
}

export function workspacePathFromHttpUrl(url) {
  var s = String(url || "").trim();
  if (!s) {
    return "";
  }
  var m = s.match(/[?&]path=([^&]+)/);
  if (m) {
    try {
      return coerceWorkspacePath(decodeURIComponent(m[1]));
    } catch (e) {}
  }
  return coerceWorkspacePath(s);
}

export function mediaIdFromHttpUrl(url) {
  var s = String(url || "").trim();
  if (!s) {
    return "";
  }
  var m = s.match(/^(?:https?:\/\/[^/]+)?\/media\/([^?#]+)/i);
  if (m) {
    try {
      return decodeURIComponent(m[1]);
    } catch (e) {
      return m[1];
    }
  }
  return "";
}

export function previewNodeFromMediaSrc(url) {
  var s = String(url || "").trim();
  if (!s) {
    return null;
  }
  if (/^(data|blob):/i.test(s)) {
    return {
      id: "inline-preview",
      path: "",
      media_id: "inline-preview",
      previewUrl: s,
      asset_type: "image",
      name: "image",
      node_type: "file",
    };
  }
  var pathMatch = s.match(/[?&]path=([^&]+)/);
  if (pathMatch) {
    try {
      var decodedPath = decodeURIComponent(pathMatch[1]);
      if (/^(data|blob):/i.test(decodedPath)) {
        return {
          id: "inline-preview",
          path: "",
          media_id: "inline-preview",
          previewUrl: decodedPath,
          asset_type: "image",
          name: "image",
          node_type: "file",
        };
      }
    } catch (e) {}
  }
  var fromMedia = mediaIdFromHttpUrl(s);
  var abs = fromMedia ? coerceWorkspacePath(fromMedia) : workspacePathFromHttpUrl(s);
  if (!abs || isRootPath(abs)) {
    return null;
  }
  return {
    id: abs,
    path: abs,
    media_id: abs,
    asset_type: "image",
    name: pathBaseName(abs),
    node_type: "file",
  };
}

export function toWorkspaceReadUrl(url) {
  var s = String(url || "").trim();
  if (!s) {
    return "";
  }
  if (s.indexOf("/workspace/read") !== -1) {
    return s;
  }
  return mediaUrl(s);
}

export function stampWorkspaceUrlsInRoot(root) {
  if (!root || !root.querySelectorAll) {
    return;
  }
  function stampAttr(selector, attr) {
    root.querySelectorAll(selector).forEach(function (el) {
      var u = el.getAttribute(attr) || "";
      var next = toWorkspaceReadUrl(u);
      if (next) {
        el.setAttribute(attr, next);
      }
    });
  }
  stampAttr("img[src]", "src");
  stampAttr("video[src], audio[src]", "src");
  stampAttr("video source[src], audio source[src]", "src");
  stampAttr("a[href]", "href");
}
