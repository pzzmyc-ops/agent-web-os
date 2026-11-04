import { API_BASE } from "./config.js";
import { naFetch } from "./http.js";
import {
  openWindow,
  closeWindow,
  focusWindow,
  getWindow,
  getWindowBody,
  getWindowAction,
} from "./modules/window-manager/index.js";
import { messageBus } from "./message-bus.js";

var WIN_ID = "retrieval-index";
var _pendingPath = "";
var _embed = { url: "", key: "", model: "", dirty: false, note: "" };

function fetchJson(path, method, body) {
  return naFetch(API_BASE + path, {
    method: method || "GET",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then(function (r) {
    return r.json().then(function (data) {
      if (!r.ok || !data.ok) throw new Error(data.error || data.detail || "请求失败: " + r.status);
      return data;
    });
  });
}

function row(label, value) {
  return '<div class="ctxwin-row"><span>' + label + "</span><b>" + value + "</b></div>";
}

function escapeText(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
  });
}

function phaseText(st) {
  if (st.active) {
    if (st.phase === "enumerating") return "正在枚举文件…";
    if (st.phase === "embedding") return "正在向量化 " + st.processed + " / " + st.total + "（改动 " + st.changed + "，删除 " + st.removed + "）";
    return "进行中…";
  }
  if (st.phase === "done") return "完成";
  if (st.phase === "cancelled") return "已取消";
  if (st.phase === "error") return "失败";
  if (!st.sources || !st.sources.length) return "还没有添加文件夹";
  return st.indexExists ? "空闲" : "已添加文件夹，尚未建立索引";
}

function sourceRows(st) {
  var sources = st.sources || [];
  if (!sources.length) {
    return '<div class="rtv-empty">还没有向量化文件夹。在上面输入路径并添加，添加后立即开始向量化。</div>';
  }
  return sources.map(function (s) {
    var busy = st.active && st.source && st.source.toLowerCase() === s.path.toLowerCase();
    var meta = s.files + " 个文件 / " + s.chunks + " 个块";
    if (busy) meta = "正在向量化…";
    else if (!s.lastSync) meta = "尚未建立";
    else meta += " · " + escapeText(s.lastSync);
    return '<div class="rtv-source">'
      + '<div class="rtv-source-main">'
      + '<div class="rtv-source-path" title="' + escapeText(s.path) + '">' + escapeText(s.display) + "</div>"
      + '<div class="rtv-source-meta">' + meta + "</div>"
      + "</div>"
      + '<button type="button" class="rtv-source-remove" data-path="' + escapeText(s.path) + '"'
      + (st.active ? " disabled" : "") + ">移除</button>"
      + "</div>";
  }).join("");
}

function syncEmbed(st) {
  if (_embed.dirty || !st) return;
  _embed.url = st.embeddingUrl || "";
  _embed.model = st.model || "";
  if (st.embeddingApiKey) _embed.key = st.embeddingApiKey;
}

function embedForm(st) {
  var mismatch = st.indexedModel && st.indexedModel !== _embed.model
    ? "当前索引用的是 " + st.indexedModel + "，改模型后需要重建索引。"
    : "";
  var disabled = st.active ? " disabled" : "";
  return '<div class="ctxwin-label">嵌入服务</div>'
    + '<input id="rtvEmbedUrl" class="ctxwin-input" type="text" placeholder="接口地址，如 http://127.0.0.1:8888/v1" value="' + escapeText(_embed.url) + '"' + disabled + ">"
    + '<div class="ctxwin-label">密钥</div>'
    + '<input id="rtvEmbedKey" class="ctxwin-input" type="text" placeholder="API Key" value="' + escapeText(_embed.key) + '"' + disabled + ">"
    + '<div class="ctxwin-label">嵌入模型名</div>'
    + '<input id="rtvEmbedModel" class="ctxwin-input" type="text" placeholder="模型名，如 bge-m3" value="' + escapeText(_embed.model) + '"' + disabled + ">"
    + '<div class="rtv-add">'
    + '<button type="button" id="rtvEmbedProbe" class="wm-footer-btn"' + disabled + ">测试连接</button>"
    + '<button type="button" id="rtvEmbedSave" class="wm-footer-btn wm-footer-primary"' + disabled + ">保存</button>"
    + "</div>"
    + (mismatch ? '<p class="ctxwin-hint">' + escapeText(mismatch) + "</p>" : "")
    + '<p id="rtvEmbedNote" class="ctxwin-hint"' + (_embed.note ? "" : " hidden") + ">" + escapeText(_embed.note) + "</p>";
}

function renderPanel(body, st) {
  var pct = st.total > 0 ? Math.round((st.processed / st.total) * 100) : (st.active ? 0 : 100);
  syncEmbed(st);
  body.innerHTML =
    '<div class="ctxwin">'
    + (st.active ? '<div class="ctxwin-bar"><i style="width:' + pct + '%"></i></div>' : "")
    + row("状态", escapeText(phaseText(st)))
    + row("已索引", st.files + " 个文件 / " + st.chunks + " 个块")
    + row("上次建立", escapeText(st.lastBuild || "从未"))
    + embedForm(st)
    + '<div class="ctxwin-label">向量化文件夹</div>'
    + '<div class="rtv-add">'
    + '<input id="rtvAddInput" class="ctxwin-input" type="text" placeholder="文件夹绝对路径，如 D:/proj/src；相对路径按工作区目录解析" value="' + escapeText(_pendingPath) + '"' + (st.active ? " disabled" : "") + ">"
    + '<button type="button" id="rtvAddBtn" class="wm-footer-btn wm-footer-primary"' + (st.active ? " disabled" : "") + ">添加</button>"
    + "</div>"
    + '<div class="rtv-sources">' + sourceRows(st) + "</div>"
    + '<p class="ctxwin-hint">添加文件夹后立即对它向量化；移除文件夹会删掉它的全部向量数据，不动磁盘上的原文件。'
    + '「建立 / 更新索引」把所有文件夹重新增量同步一遍，只对改动过的文件重新向量化。'
    + '每个文件夹内遵守它自己的 .gitignore / .cursorignore / .cursorindexingignore，跳过二进制、超过 1MB 的文件以及 node_modules 等目录。'
    + '索引不会自动跟随文件改动。向量由嵌入服务计算。</p>'
    + '<p id="rtvWinError" class="ctxwin-error"' + (st.error ? "" : " hidden") + ">" + escapeText(st.error || "") + "</p>"
    + "</div>";
  bindPanel(body);
  var rebuild = getWindowAction(WIN_ID, "rebuild");
  if (rebuild) rebuild.disabled = !!st.active || !(st.sources && st.sources.length);
  var cancel = getWindowAction(WIN_ID, "cancel");
  if (cancel) cancel.disabled = !st.active;
}

function readEmbed(body) {
  var url = body.querySelector("#rtvEmbedUrl");
  var key = body.querySelector("#rtvEmbedKey");
  var model = body.querySelector("#rtvEmbedModel");
  if (!url || !key || !model) return null;
  return { url: url.value.trim(), api_key: key.value.trim(), model: model.value.trim() };
}

function markEmbedDirty(body) {
  var draft = readEmbed(body);
  if (!draft) return;
  _embed.dirty = true;
  _embed.url = draft.url;
  _embed.key = draft.api_key;
  _embed.model = draft.model;
}

function onProbeEmbed() {
  var body = getWindowBody(WIN_ID);
  var draft = body && readEmbed(body);
  if (!draft) return;
  markEmbedDirty(body);
  _embed.note = "正在测试…";
  var btn = body.querySelector("#rtvEmbedProbe");
  if (btn) btn.disabled = true;
  fetchJson("/api/v1/retrieval/embedding/probe", "POST", draft)
    .then(function (res) {
      var info = res.embedder || {};
      var listed = Array.isArray(info.available_models) ? info.available_models.length : 0;
      _embed.note = "连接正常，模型 " + (info.model || draft.model) + " 可用" + (listed ? "（服务共 " + listed + " 个模型）" : "");
      _embed.dirty = true;
      fillFromStatus();
    })
    .catch(function (err) {
      _embed.note = "";
      fillFromStatus().then(function () { showError(err.message); });
    });
}

function onSaveEmbed() {
  var body = getWindowBody(WIN_ID);
  var draft = body && readEmbed(body);
  if (!draft) return;
  markEmbedDirty(body);
  var btn = body.querySelector("#rtvEmbedSave");
  if (btn) btn.disabled = true;
  _embed.note = "正在测试并保存…";
  fetchJson("/api/v1/retrieval/embedding", "PUT", draft)
    .then(function (res) {
      _embed.dirty = false;
      _embed.note = "已保存，模型 " + ((res.embedder && res.embedder.model) || draft.model);
      applyStatus(res);
    })
    .catch(function (err) {
      _embed.note = "";
      fillFromStatus().then(function () { showError(err.message); });
    });
}

function fillFromStatus() {
  return fetchJson("/api/v1/retrieval/status").then(applyStatus);
}

function bindPanel(body) {
  ["#rtvEmbedUrl", "#rtvEmbedKey", "#rtvEmbedModel"].forEach(function (sel) {
    var input = body.querySelector(sel);
    if (!input) return;
    input.addEventListener("input", function () {
      markEmbedDirty(body);
      _embed.note = "";
    });
  });
  var probe = body.querySelector("#rtvEmbedProbe");
  var save = body.querySelector("#rtvEmbedSave");
  if (probe) probe.onclick = onProbeEmbed;
  if (save) save.onclick = onSaveEmbed;
  var input = body.querySelector("#rtvAddInput");
  var addBtn = body.querySelector("#rtvAddBtn");
  if (input) {
    input.addEventListener("input", function () { _pendingPath = input.value; });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        onAddSource();
      }
    });
  }
  if (addBtn) addBtn.onclick = onAddSource;
  body.querySelectorAll(".rtv-source-remove").forEach(function (btn) {
    btn.onclick = function () { onRemoveSource(btn.getAttribute("data-path")); };
  });
}

function showError(message) {
  var body = getWindowBody(WIN_ID);
  if (!body) return;
  var el = body.querySelector("#rtvWinError");
  if (!el) return;
  el.textContent = message;
  el.hidden = false;
}

function applyStatus(res) {
  syncEmbed(res.status);
  var body = getWindowBody(WIN_ID);
  if (body) renderPanel(body, res.status);
}

function fillWindow() {
  var body = getWindowBody(WIN_ID);
  if (!body) throw new Error("retrieval window body missing");
  return fillFromStatus().catch(function (err) {
    showError(err.message);
  });
}

function onAddSource() {
  var path = _pendingPath.trim();
  if (!path) {
    showError("先填一个文件夹路径");
    return;
  }
  var body = getWindowBody(WIN_ID);
  var addBtn = body && body.querySelector("#rtvAddBtn");
  if (addBtn) addBtn.disabled = true;
  fetchJson("/api/v1/retrieval/sources", "POST", { path: path })
    .then(function (res) {
      _pendingPath = "";
      applyStatus(res);
    })
    .catch(function (err) {
      if (addBtn) addBtn.disabled = false;
      showError(err.message);
    });
}

function onRemoveSource(path) {
  if (!path) return;
  if (!window.confirm("移除这个文件夹，并删掉它的全部向量数据？\n" + path)) return;
  fetchJson("/api/v1/retrieval/sources/remove", "POST", { path: path })
    .then(applyStatus)
    .catch(function (err) {
      showError(err.message);
    });
}

function onRebuild() {
  var btn = getWindowAction(WIN_ID, "rebuild");
  if (btn) btn.disabled = true;
  fetchJson("/api/v1/retrieval/rebuild", "POST")
    .then(applyStatus)
    .catch(function (err) {
      if (btn) btn.disabled = false;
      showError(err.message);
    });
}

function onCancel() {
  fetchJson("/api/v1/retrieval/cancel", "POST")
    .then(applyStatus)
    .catch(function (err) {
      showError(err.message);
    });
}

export function openRetrievalWindow() {
  if (getWindow(WIN_ID)) {
    focusWindow(WIN_ID);
    fillWindow();
    return;
  }
  openWindow(WIN_ID, {
    title: "代码索引",
    width: 520,
    height: 560,
    minWidth: 400,
    minHeight: 360,
    footerActions: [
      { id: "cancel", label: "停止", title: "停止正在进行的索引，已处理的部分保留", onClick: onCancel },
      { id: "close", label: "关闭", onClick: function () { closeWindow(WIN_ID); } },
      { id: "rebuild", label: "建立 / 更新索引", variant: "primary", title: "把所有文件夹重新增量同步一遍", onClick: onRebuild },
    ],
    onMount: function (body) {
      body.innerHTML = '<div class="ctxwin-loading">加载中...</div>';
    },
  });
  fillWindow();
}

export function initRetrievalWindow() {
  var btn = document.getElementById("retrievalBtn");
  if (!btn) throw new Error("retrievalBtn missing");
  btn.onclick = function (e) {
    e.stopPropagation();
    openRetrievalWindow();
  };
  messageBus.on("retrieval_indexing", function (msg) {
    var data = msg && msg.data;
    if (!data) throw new Error("retrieval_indexing missing data");
    var body = getWindowBody(WIN_ID);
    if (body) renderPanel(body, data);
  });
}
