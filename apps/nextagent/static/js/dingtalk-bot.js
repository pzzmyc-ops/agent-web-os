const badge = document.getElementById("badge");
const errorBox = document.getElementById("error");
const binds = document.getElementById("binds");
const logs = document.getElementById("logs");
const modelSel = document.getElementById("model");
const btnStop = document.getElementById("btnStop");
let modelsReady = false;

function fmtTime(ms) {
  if (!ms) return "-";
  const d = new Date(ms);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function render(data) {
  const running = !!data.ready;
  if (!data.logged_in) {
    badge.textContent = "未登录";
    badge.className = "badge wait";
  } else {
    badge.textContent = data.error && !running ? "出错" : running ? "监听中" : data.running ? "启动中" : "未启动";
    badge.className = "badge" + (running ? " on" : data.error ? " err" : "");
  }
  if (data.error) {
    errorBox.hidden = false;
    errorBox.textContent = data.error;
  } else {
    errorBox.hidden = true;
    errorBox.textContent = "";
  }
  if (modelsReady && data.model) modelSel.value = data.model;
  btnStop.disabled = !data.busy;
  badge.textContent = data.busy ? "处理中" : badge.textContent;
  const rows = data.bindings || [];
  binds.innerHTML = rows.length
    ? rows.map((b) => (
      "<tr><td>" + escapeHtml(b.group_name || "") +
      "</td><td>" + escapeHtml(b.thread_id || "") +
      "</td></tr>"
    )).join("")
    : "<tr><td colspan='2'>还没有被@过</td></tr>";
  logs.textContent = (data.logs || []).map((item) => {
    return fmtTime(item.ts) + "  " + item.level + "  " + item.text;
  }).join("\n");
  logs.scrollTop = logs.scrollHeight;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

async function request(url, method, body) {
  const opts = { method: method || "GET" };
  if (body !== undefined) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(url, opts);
  const data = await resp.json();
  if (!resp.ok) {
    throw new Error(data.error || data.detail || ("HTTP " + resp.status));
  }
  return data;
}

async function loadModels() {
  const data = await request("/api/chat/models");
  const rows = data.models || [];
  modelSel.innerHTML = rows.map((m) => {
    const id = m.id || "";
    const name = m.displayName || id;
    return "<option value=\"" + escapeHtml(id) + "\">" + escapeHtml(name) + "</option>";
  }).join("");
  modelsReady = true;
}

async function refresh() {
  const data = await request("/api/v1/dingtalk-bot/status");
  render(data);
  return data;
}

btnStop.addEventListener("click", async () => {
  btnStop.disabled = true;
  try {
    render(await request("/api/v1/dingtalk-bot/cancel", "POST"));
  } catch (e) {
    errorBox.hidden = false;
    errorBox.textContent = e.message;
    await refresh();
  }
});

modelSel.addEventListener("change", async () => {
  try {
    render(await request("/api/v1/dingtalk-bot/model", "POST", { model: modelSel.value }));
  } catch (e) {
    errorBox.hidden = false;
    errorBox.textContent = e.message;
    await refresh();
  }
});

(async function boot() {
  await loadModels();
  await refresh();
  setInterval(() => {
    refresh().catch((e) => {
      errorBox.hidden = false;
      errorBox.textContent = e.message;
    });
  }, 800);
})();
