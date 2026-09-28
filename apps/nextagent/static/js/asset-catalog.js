import { API_BASE } from "./config.js";
import { naFetch } from "./http.js";
import { logAssetUrl } from "./asset-diag.js";

var DB_NAME = "nextagent_catalog_v1";
var DB_VERSION = 1;
var STORE_NODES = "nodes";
var CACHE_NAME = "nextagent-media-bin-v1";

var _memory = new Map();
var _userId = "";
var _dbPromise = null;
var _cacheApi = null;
var _inflight = new Map();
var _blobUrls = new Map();

function _mediaIdFromHttpUrl(url) {
  var s = String(url || "").trim();
  if (!s) return "";
  var m = s.match(/\/media\/([a-f0-9]{32})/i);
  if (m) return m[1];
  return "";
}

function _nodeKey(mediaId) {
  return _userId + ":" + String(mediaId || "");
}

function _cacheKey(mediaId, variant) {
  return _userId + ":" + String(mediaId || "") + ":" + (variant || "thumb");
}

function _openDb() {
  if (_dbPromise) return _dbPromise;
  _dbPromise = new Promise(function (resolve, reject) {
    var req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onerror = function () {
      reject(req.error);
    };
    req.onsuccess = function () {
      resolve(req.result);
    };
    req.onupgradeneeded = function (e) {
      var db = e.target.result;
      if (!db.objectStoreNames.contains(STORE_NODES)) {
        db.createObjectStore(STORE_NODES, { keyPath: "key" });
      }
    };
  });
  return _dbPromise;
}

function _getCacheApi() {
  if (_cacheApi !== null) return Promise.resolve(_cacheApi);
  if (!globalThis.caches) {
    _cacheApi = false;
    return Promise.resolve(false);
  }
  return caches.open(CACHE_NAME).then(function (c) {
    _cacheApi = c;
    return c;
  }).catch(function () {
    _cacheApi = false;
    return false;
  });
}

export function setCatalogUserId(userId) {
  _userId = String(userId || "").trim();
}

function _normalizeNode(raw) {
  if (!raw) return null;
  var mediaId = String(raw.media_id || raw.id || "").trim();
  if (!/^[a-f0-9]{32}$/.test(mediaId)) {
    var fromUrl = _mediaIdFromHttpUrl(String(raw.previewUrl || raw.url || ""));
    if (fromUrl) mediaId = fromUrl;
  }
  if (!/^[a-f0-9]{32}$/.test(mediaId)) return null;
  return {
    media_id: mediaId,
    signed_url: String(raw.signed_url || ""),
    thumb_url: String(raw.thumb_url || ""),
    mime_type: String(raw.mime_type || ""),
    name: String(raw.name || ""),
    path: String(raw.path || ""),
    asset_type: String(raw.asset_type || "image"),
  };
}

function _putMemory(node) {
  if (!node || !node.media_id) return;
  _memory.set(node.media_id, node);
}

function _idbPut(node) {
  if (!_userId || !node || !node.media_id) return Promise.resolve();
  return _openDb().then(function (db) {
    return new Promise(function (resolve, reject) {
      var tx = db.transaction(STORE_NODES, "readwrite");
      tx.objectStore(STORE_NODES).put({
        key: _nodeKey(node.media_id),
        userId: _userId,
        media_id: node.media_id,
        node: node,
        updatedAt: Date.now(),
      });
      tx.oncomplete = function () {
        resolve();
      };
      tx.onerror = function () {
        reject(tx.error);
      };
    });
  }).catch(function () {});
}

function _idbGet(mediaId) {
  if (!_userId || !mediaId) return Promise.resolve(null);
  return _openDb().then(function (db) {
    return new Promise(function (resolve, reject) {
      var tx = db.transaction(STORE_NODES, "readonly");
      var req = tx.objectStore(STORE_NODES).get(_nodeKey(mediaId));
      req.onsuccess = function () {
        var row = req.result;
        resolve(row && row.node ? row.node : null);
      };
      req.onerror = function () {
        reject(req.error);
      };
    });
  }).catch(function () {
    return null;
  });
}

export function upsertNode(raw) {
  var node = _normalizeNode(raw);
  if (!node) return null;
  var prev = _memory.get(node.media_id);
  if (prev) {
    if (!node.signed_url && prev.signed_url) node.signed_url = prev.signed_url;
    if (!node.thumb_url && prev.thumb_url) node.thumb_url = prev.thumb_url;
  }
  _putMemory(node);
  _idbPut(node);
  return node;
}

export function upsertFromMediaReady(data) {
  if (!data) return null;
  return upsertNode({
    media_id: data.media_id,
    signed_url: data.signed_url,
    thumb_url: data.thumb_url,
    toolName: data.toolName,
  });
}

export function upsertFromHistoryMedia(media) {
  if (!media) return null;
  return upsertNode({
    media_id: media.media_id,
    signed_url: media.signed_url,
    thumb_url: media.thumb_url,
  });
}

export function upsertNodesFromList(nodes) {
  if (!nodes || !nodes.length) return;
  for (var i = 0; i < nodes.length; i++) {
    var n = nodes[i];
    if (!n || !n.media_id) continue;
    upsertNode({
      media_id: n.media_id,
      signed_url: n.signed_url || "",
      thumb_url: n.thumb_url || "",
      mime_type: n.mime_type,
      name: n.name,
      path: n.path,
      asset_type: n.asset_type,
    });
  }
}

export function getNode(mediaId) {
  var id = String(mediaId || "").trim();
  if (!id) return null;
  return _memory.get(id) || null;
}

export function resolveDisplayUrl(mediaId, variant) {
  var node = getNode(mediaId);
  if (!node) return "";
  if (variant === "full") {
    return node.signed_url || "";
  }
  return node.thumb_url || node.signed_url || "";
}

function _revokeBlob(mediaId, variant) {
  var k = _cacheKey(mediaId, variant);
  var old = _blobUrls.get(k);
  if (old) {
    URL.revokeObjectURL(old);
    _blobUrls.delete(k);
  }
}

async function _readCacheBlob(mediaId, variant) {
  var c = await _getCacheApi();
  if (!c) return null;
  var res = await c.match(_cacheKey(mediaId, variant));
  if (!res || !res.ok) return null;
  return res.blob();
}

async function _writeCacheBlob(mediaId, variant, blob) {
  var c = await _getCacheApi();
  if (!c || !blob) return;
  await c.put(_cacheKey(mediaId, variant), new Response(blob));
}

async function _fetchBatchUrls(ids) {
  var missing = ids.filter(function (id) {
    var n = getNode(id);
    return !n || !n.signed_url;
  });
  if (!missing.length) return;
  var resp = await naFetch(API_BASE + "/api/v1/media/urls", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids: missing.slice(0, 50) }),
  });
  if (!resp.ok) return;
  var data = await resp.json();
  var items = data.items || {};
  var resolvedIds = Object.keys(items);
  var unresolved = missing.filter(function (id) {
    return resolvedIds.indexOf(id) < 0;
  });
  if (unresolved.length) {
    logAssetUrl("catalog_batch_miss", {
      extra: "requested=" + missing.length + " resolved=" + resolvedIds.length + " missing=" + unresolved.slice(0, 5).join(","),
    });
  }
  Object.keys(items).forEach(function (id) {
    upsertNode(Object.assign({ media_id: id }, items[id]));
  });
}

function _singleFlight(key, fn) {
  if (_inflight.has(key)) return _inflight.get(key);
  var p = Promise.resolve().then(fn).finally(function () {
    _inflight.delete(key);
  });
  _inflight.set(key, p);
  return p;
}

export async function ensureNode(mediaId) {
  var id = String(mediaId || "").trim();
  if (!/^[a-f0-9]{32}$/.test(id)) return null;
  var node = getNode(id);
  if (node && node.signed_url) return node;
  var fromDb = await _idbGet(id);
  if (fromDb) {
    _putMemory(fromDb);
    if (fromDb.signed_url) return fromDb;
  }
  await _singleFlight("batch:" + id, function () {
    return _fetchBatchUrls([id]);
  });
  var resolved = getNode(id);
  if (!resolved) {
    logAssetUrl("catalog_ensure_miss", {
      mediaId: id,
      extra: "after_batch_fetch",
    });
  }
  return resolved;
}

async function _fetchHttpBlob(url) {
  if (!url || url.indexOf("http") !== 0) return null;
  var resp = await fetch(url);
  if (!resp.ok) return null;
  return resp.blob();
}

async function _fetchMediaProxyBlob(mediaId) {
  var url = API_BASE + "/media/" + mediaId;
  var resp = await naFetch(url);
  if (!resp.ok) {
    logAssetUrl("catalog_media_proxy_fail", {
      mediaId: mediaId,
      extra: "status=" + resp.status,
    });
    return null;
  }
  return resp.blob();
}

export async function loadBlob(mediaId, variant) {
  var id = String(mediaId || "").trim();
  if (!/^[a-f0-9]{32}$/.test(id)) return null;
  var v = variant === "full" ? "full" : "thumb";
  var cached = await _readCacheBlob(id, v);
  if (cached) return cached;
  var node = await ensureNode(id);
  if (!node) return null;
  var urls = v === "full"
    ? [node.signed_url]
    : [node.thumb_url, node.signed_url];
  for (var i = 0; i < urls.length; i++) {
    var blob = await _fetchHttpBlob(urls[i]);
    if (blob) {
      await _writeCacheBlob(id, v, blob);
      return blob;
    }
  }
  var proxyBlob = await _fetchMediaProxyBlob(id);
  if (!proxyBlob) return null;
  await _writeCacheBlob(id, v, proxyBlob);
  return proxyBlob;
}

export async function resolveBlobUrl(mediaId, variant) {
  var id = String(mediaId || "").trim();
  var v = variant === "full" ? "full" : "thumb";
  var k = _cacheKey(id, v);
  if (_blobUrls.has(k)) return _blobUrls.get(k);
  var blob = await loadBlob(id, v);
  if (!blob) return "";
  _revokeBlob(id, v);
  var blobUrl = URL.createObjectURL(blob);
  _blobUrls.set(k, blobUrl);
  return blobUrl;
}

export async function fetchBatchUrlsForIds(ids) {
  var list = (ids || []).filter(function (id) {
    return /^[a-f0-9]{32}$/.test(String(id || ""));
  });
  if (!list.length) return;
  await _singleFlight("batch:" + list.join(","), function () {
    return _fetchBatchUrls(list);
  });
}

export async function clearCatalogForUser(userId) {
  var uid = String(userId || "").trim();
  _memory.clear();
  _inflight.clear();
  _blobUrls.forEach(function (url) {
    URL.revokeObjectURL(url);
  });
  _blobUrls.clear();
  if (_cacheApi) {
    var keys = await _cacheApi.keys();
    await Promise.all(keys.filter(function (k) {
      return String(k).indexOf(uid + ":") === 0;
    }).map(function (k) {
      return _cacheApi.delete(k);
    }));
  }
  try {
    var db = await _openDb();
    await new Promise(function (resolve, reject) {
      var tx = db.transaction(STORE_NODES, "readwrite");
      var store = tx.objectStore(STORE_NODES);
      var req = store.openCursor();
      req.onsuccess = function (e) {
        var cursor = e.target.result;
        if (!cursor) return;
        if (String(cursor.key).indexOf(uid + ":") === 0) {
          cursor.delete();
        }
        cursor.continue();
      };
      tx.oncomplete = function () {
        resolve();
      };
      tx.onerror = function () {
        reject(tx.error);
      };
    });
  } catch (e) {}
}
