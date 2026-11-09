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
import { fetchModelsCatalog } from "./model-controls.js";

var WIN_ID = "upstreams";
var _items = [];
var _editing = -1;
var _draft = null;
var _probeText = "";
var _error = "";

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

function escapeText(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
  });
}

function maskKey(key) {
  var k = String(key || "");
  if (!k) return "（无密钥）";
  if (k.length <= 8) return "****";
  return k.slice(0, 4) + "…" + k.slice(-4);
}

function emptyDraft() {
  return { name: "", base_url: "", api_key: "", models: "", headers: "" };
}

function draftFrom(item) {
  return {
    name: item.name,
    base_url: item.base_url,
    api_key: item.api_key,
    models: (item.models || []).join("\n"),
    headers: Object.keys(item.headers || {}).length ? JSON.stringify(item.headers, null, 2) : "",
  };
}

function draftToItem(d) {
  var headers = {};
  var text = String(d.headers || "").trim();
  if (text) {
    var parsed = JSON.parse(text);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("额外请求头必须是 JSON 对象");
    headers = parsed;
  }
  return {
    name: String(d.name || "").trim(),
    base_url: String(d.base_url || "").trim(),
    api_key: String(d.api_key || "").trim(),
    models: String(d.models || "").split(/[\n,]/).map(function (s) { return s.trim(); }).filter(Boolean),
    headers: headers,
  };
}

function listHtml() {
  if (!_items.length) {
    return '<div class="rtv-empty">还没有登记任何上游接口。点「新增」填入接口地址、密钥和模型名。</div>';
  }
  return _items.map(function (u, i) {
    return '<div class="rtv-source">'
      + '<div class="rtv-source-main">'
      + '<div class="rtv-source-path" title="' + escapeText(u.base_url) + '">' + escapeText(u.name) + ' <span class="ups-url">' + escapeText(u.base_url) + "</span></div>"
      + '<div class="rtv-source-meta">' + escapeText((u.models || []).join("，")) + " · 密钥 " + escapeText(maskKey(u.api_key)) + "</div>"
      + "</div>"
      + '<button type="button" class="rtv-source-remove ups-edit" data-index="' + i + '">编辑</button>'
      + '<button type="button" class="rtv-source-remove ups-remove" data-index="' + i + '">删除</button>'
      + "</div>";
  }).join("");
}

function field(id, label, value, placeholder, textarea) {
  var input = textarea
    ? '<textarea id="' + id + '" class="ctxwin-input ups-textarea" rows="3" placeholder="' + escapeText(placeholder) + '">' + escapeText(value) + "</textarea>"
    : '<input id="' + id + '" class="ctxwin-input" type="text" placeholder="' + escapeText(placeholder) + '" value="' + escapeText(value) + '">';
  return '<div class="ctxwin-label">' + label + "</div>" + input;
}

function formHtml() {
  var d = _draft;
  return '<div class="ups-form">'
    + '<div class="ctxwin-label">' + (_editing >= 0 ? "编辑上游接口" : "新增上游接口") + "</div>"
    + field("upsName", "名称（对话里显示的模型名）", d.name, "比如 deepseek / claude fable 5 / 公司网关")
    + field("upsBase", "接口地址（base_url）", d.base_url, "例如 https://api.deepseek.com/v1 或 https://api.anthropic.com")
    + field("upsKey", "API Key", d.api_key, "没有鉴权的本地服务可留空")
    + field("upsModels", "模型名（一行一个）", d.models, "例如 deepseek-chat\ndeepseek-reasoner", true)
    + field("upsHeaders", "额外请求头（JSON，可选）", d.headers, '例如 {"X-API-Key": "xxx"}', true)
    + '<div class="rtv-add">'
    + '<button type="button" id="upsProbeBtn" class="wm-footer-btn">测试连接</button>'
    + '<button type="button" id="upsSaveBtn" class="wm-footer-btn wm-footer-primary">保存</button>'
    + '<button type="button" id="upsCancelBtn" class="wm-footer-btn">取消</button>'
    + "</div>"
    + '<pre id="upsProbeOut" class="ups-probe"' + (_probeText ? "" : " hidden") + ">" + escapeText(_probeText) + "</pre>"
    + "</div>";
}

function render() {
  var body = getWindowBody(WIN_ID);
  if (!body) return;
  body.innerHTML =
    '<div class="ctxwin">'
    + '<p class="ctxwin-hint">这里登记的接口就是对话可选的模型来源。协议不用选:程序按接口地址和模型名自动判断是 OpenAI chat、OpenAI Responses 还是 Anthropic Messages,判错时会自动换另一种协议重试并记住结果。「名称」就是对话里显示的模型名；同一个模型名可以在多个上游各登记一次,按名称区分。</p>'
    + '<div class="rtv-sources">' + listHtml() + "</div>"
    + (_draft ? formHtml() : "")
    + '<p id="upsWinError" class="ctxwin-error"' + (_error ? "" : " hidden") + ">" + escapeText(_error) + "</p>"
    + "</div>";
  bind(body);
  var addBtn = getWindowAction(WIN_ID, "add");
  if (addBtn) addBtn.disabled = !!_draft;
}

function readDraft(body) {
  _draft.name = body.querySelector("#upsName").value;
  _draft.base_url = body.querySelector("#upsBase").value;
  _draft.api_key = body.querySelector("#upsKey").value;
  _draft.models = body.querySelector("#upsModels").value;
  _draft.headers = body.querySelector("#upsHeaders").value;
}

function bind(body) {
  body.querySelectorAll(".ups-edit").forEach(function (btn) {
    btn.onclick = function () {
      _editing = parseInt(btn.getAttribute("data-index"), 10);
      _draft = draftFrom(_items[_editing]);
      _probeText = "";
      _error = "";
      render();
    };
  });
  body.querySelectorAll(".ups-remove").forEach(function (btn) {
    btn.onclick = function () {
      var i = parseInt(btn.getAttribute("data-index"), 10);
      var u = _items[i];
      if (!window.confirm("删除上游接口 " + u.name + "？用它的模型的会话下次发送会回落到默认模型。")) return;
      var next = _items.slice();
      next.splice(i, 1);
      save(next);
    };
  });
  var probe = body.querySelector("#upsProbeBtn");
  var saveBtn = body.querySelector("#upsSaveBtn");
  var cancel = body.querySelector("#upsCancelBtn");
  if (probe) probe.onclick = function () { readDraft(body); onProbe(probe); };
  if (saveBtn) saveBtn.onclick = function () { readDraft(body); onSave(); };
  if (cancel) cancel.onclick = function () {
    _draft = null;
    _editing = -1;
    _probeText = "";
    _error = "";
    render();
  };
}

function showError(message) {
  _error = message;
  var body = getWindowBody(WIN_ID);
  var el = body && body.querySelector("#upsWinError");
  if (el) {
    el.textContent = message;
    el.hidden = !message;
  }
}

function onProbe(btn) {
  var item;
  try {
    item = draftToItem(_draft);
  } catch (err) {
    showError(err.message);
    return;
  }
  btn.disabled = true;
  _probeText = "正在连接…";
  render();
  fetchJson("/api/v1/upstreams/probe", "POST", item)
    .then(function (res) {
      var p = res.probe;
      var lines = ["GET " + p.url + " -> HTTP " + p.status + (p.ok ? " 连接正常" : " 失败")];
      var protos = p.protocols || {};
      Object.keys(protos).forEach(function (m) { lines.push("模型 " + m + " 首选协议: " + protos[m]); });
      if (p.models && p.models.length) lines.push("对方列出的模型: " + p.models.join(", "));
      if (!p.ok) lines.push(p.body || "");
      _probeText = lines.join("\n");
      render();
    })
    .catch(function (err) {
      _probeText = "";
      render();
      showError(err.message);
    });
}

function onSave() {
  var item;
  try {
    item = draftToItem(_draft);
  } catch (err) {
    showError(err.message);
    return;
  }
  var next = _items.slice();
  if (_editing >= 0) next[_editing] = item;
  else next.push(item);
  save(next);
}

function save(next) {
  fetchJson("/api/v1/upstreams", "PUT", { upstreams: next })
    .then(function (res) {
      _items = res.upstreams || [];
      _draft = null;
      _editing = -1;
      _probeText = "";
      _error = "";
      render();
      return fetchModelsCatalog();
    })
    .catch(function (err) {
      showError(err.message);
    });
}

function fillWindow() {
  return fetchJson("/api/v1/upstreams").then(function (res) {
    _items = res.upstreams || [];
    _error = "";
    render();
  }).catch(function (err) {
    render();
    showError(err.message);
  });
}

function onAdd() {
  _editing = -1;
  _draft = emptyDraft();
  _probeText = "";
  _error = "";
  render();
}

export function openUpstreamsWindow() {
  if (getWindow(WIN_ID)) {
    focusWindow(WIN_ID);
    fillWindow();
    return;
  }
  openWindow(WIN_ID, {
    title: "上游接口",
    width: 600,
    height: 640,
    minWidth: 440,
    minHeight: 400,
    footerActions: [
      { id: "close", label: "关闭", onClick: function () { closeWindow(WIN_ID); } },
      { id: "add", label: "新增", variant: "primary", onClick: onAdd },
    ],
    onMount: function (body) {
      body.innerHTML = '<div class="ctxwin-loading">加载中...</div>';
    },
  });
  fillWindow();
}

export function initUpstreamsWindow() {
  var btn = document.getElementById("upstreamsBtn");
  if (!btn) throw new Error("upstreamsBtn missing");
  btn.onclick = function (e) {
    e.stopPropagation();
    openUpstreamsWindow();
  };
  messageBus.on("models_changed", function () {
    fetchModelsCatalog().catch(function (err) {
      console.error("[upstreams] 模型列表刷新失败:", err);
    });
    if (getWindow(WIN_ID) && !_draft) fillWindow();
  });
}
