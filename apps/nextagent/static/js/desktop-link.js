var ID_KEY = "fm.desktop.id";
var desktopId = "";
var standalone = false;
var evicted = false;
var ws = null;
var reconnectTimer = 0;

export function decideDesktop(isTopWindow, urlDesktopId, storedDesktopId, generatedDesktopId) {
  if (!isTopWindow) {
    if (!urlDesktopId) throw new Error("嵌入的对话页缺少 desktopId");
    return { connect: false, desktopId: urlDesktopId };
  }
  var id = urlDesktopId || storedDesktopId || generatedDesktopId;
  if (!id) throw new Error("缺少 desktopId");
  return { connect: true, desktopId: id };
}

export function chatCloseAction(code, desktopEvicted) {
  if (desktopEvicted) return "stop";
  if (code === 1008) return "login";
  return "reconnect";
}

export function staleSocketMessage(currentSocket, messageSocket) {
  return currentSocket !== messageSocket;
}

export function sessionAlive() {
  return fetch("/api/auth/status", { cache: "no-store" })
    .then(function (res) { return res.json(); })
    .then(function (body) {
      if (!body.ok) throw new Error("登录状态读取失败");
      if (body.authenticated) return true;
      location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
      return false;
    });
}

export function initDesktopLink() {
  var fromUrl = new URLSearchParams(location.search).get("desktopId") || "";
  var stored = sessionStorage.getItem(ID_KEY) || "";
  var generated = "";
  if (window.top === window && !fromUrl && !stored) {
    generated = "d" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
    sessionStorage.setItem(ID_KEY, generated);
  }
  var decision = decideDesktop(window.top === window, fromUrl, stored, generated);
  desktopId = decision.desktopId;
  standalone = decision.connect;
  if (!decision.connect) return desktopId;
  connect();
  window.addEventListener("beforeunload", function () {
    if (ws) ws.close();
  });
  return desktopId;
}

export function getDesktopId() {
  return desktopId;
}

export function isDesktopEvicted() {
  return evicted;
}

function connect() {
  if (evicted || !standalone) return;
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  var proto = location.protocol === "https:" ? "wss:" : "ws:";
  var sock = new WebSocket(proto + "//" + location.host + "/api/desktop/screen?desktopId=" + encodeURIComponent(desktopId));
  ws = sock;
  sock.onmessage = function (ev) {
    if (staleSocketMessage(ws, sock)) return;
    var msg = JSON.parse(ev.data);
    if (msg.type === "cmd") {
      send({
        type: "result",
        reqId: msg.reqId,
        ok: false,
        error: "当前桌面是单独打开的对话页，没有窗口，不能执行 " + String(msg.op || "")
      });
    } else if (msg.type === "evicted") {
      onEvicted(msg);
    }
  };
  sock.onclose = function () {
    if (staleSocketMessage(ws, sock)) return;
    ws = null;
    if (evicted) return;
    clearTimeout(reconnectTimer);
    sessionAlive().then(function (alive) {
      if (!alive || evicted || ws) return;
      clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(connect, 1500);
    });
  };
  sock.onerror = function () {
    sock.close();
  };
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

function onEvicted(msg) {
  evicted = true;
  clearTimeout(reconnectTimer);
  if (ws) ws.close();
  window.dispatchEvent(new CustomEvent("na:desktop-evicted"));
  document.body.innerHTML = "";
  var box = document.createElement("div");
  box.className = "desktop-evicted";
  var title = document.createElement("h1");
  title.textContent = msg.reason || "桌面已在别处打开";
  var hint = document.createElement("p");
  hint.textContent = "同一时刻只允许一块桌面在线。需要在这里继续用，点下面的按钮，别处那块会被停用。";
  var btn = document.createElement("button");
  btn.type = "button";
  btn.textContent = "在这里继续使用";
  btn.addEventListener("click", function () { location.reload(); });
  box.appendChild(title);
  box.appendChild(hint);
  box.appendChild(btn);
  document.body.appendChild(box);
}
