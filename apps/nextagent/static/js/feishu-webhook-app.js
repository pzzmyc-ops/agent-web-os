import { API_BASE } from "./config.js";
import { naFetch } from "./http.js";
import { state } from "./state.js";

export function isFeishuWebhookApp() {
  return new URLSearchParams(location.search).get("feishu_webhook") === "1";
}

export function currentApp() {
  return isFeishuWebhookApp() ? "feishu_webhook" : "";
}

export function filterAppConversations(list) {
  var webhook = isFeishuWebhookApp();
  return (list || []).filter(function (c) {
    return (String(c.app || "") === "feishu_webhook") === webhook;
  });
}

export function filterAppFolders(list) {
  var app = currentApp();
  return (list || []).filter(function (f) {
    return String(f.app || "") === app;
  });
}

export function bindFeishuWebhookUi(onBound) {
  if (!isFeishuWebhookApp()) return;
  var logo = document.querySelector(".sidebar-logo");
  if (logo) logo.textContent = "飞书 Webhook";
  document.title = "飞书 Webhook";
  var save = document.getElementById("feishuWebhookSave");
  var input = document.getElementById("feishuWebhookInput");
  var err = document.getElementById("feishuWebhookError");
  if (!save || !input || !err) throw new Error("飞书 webhook 填写界面缺失");
  save.onclick = function () {
    var tid = state.currentConversationId;
    if (!tid) throw new Error("当前没有对话");
    err.hidden = true;
    err.textContent = "";
    save.disabled = true;
    naFetch(API_BASE + "/api/v1/conversations/" + encodeURIComponent(tid) + "/feishu-webhook", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: input.value }),
    }).then(function (r) {
      if (!r.ok) return r.json().then(function (body) {
        throw new Error((body && (body.error || body.detail)) || ("保存失败: " + r.status));
      });
      return r.json();
    }).then(function () {
      input.value = "";
      if (typeof onBound === "function") onBound(tid);
    }).catch(function (e) {
      err.textContent = e.message || String(e);
      err.hidden = false;
    }).then(function () {
      save.disabled = false;
    });
  };
}

export function syncFeishuWebhookChrome(conv) {
  var gate = document.getElementById("feishuWebhookGate");
  var banner = document.getElementById("dingtalkBanner");
  var composer = document.getElementById("composerSeat");
  if (!isFeishuWebhookApp()) {
    if (gate) gate.hidden = true;
    return;
  }
  var bound = !!(conv && conv.feishuWebhookBound);
  if (!gate) throw new Error("飞书 webhook 填写界面缺失");
  gate.hidden = bound;
  if (composer) composer.style.display = bound ? "" : "none";
  if (banner) {
    banner.hidden = !bound;
    if (bound) {
      banner.className = "feishu-banner";
      banner.textContent = "飞书 Webhook 已绑定，只发消息不收消息";
    }
  }
}
