//: 这块屏幕对外的连接。后端 /api/desktop/* 不知道桌面能做什么,只负责把命令送到这里;
//: 能做什么、参数怎么校验,全在 DesktopOS 自己那张表里。所以这个文件只做搬运:
//: 收命令 -> DesktopOS.exec -> 回执,不认识任何具体操作。
const DesktopLink = {
  ID_KEY: "fm.desktop.id",
  ws: null,
  reconnectTimer: 0,
  desktopId: "",
  evicted: false,
  ready: null,
  _readyResolve: null,

  init() {
    this.desktopId = this.ensureId();
    this.connect();
    window.addEventListener("beforeunload", () => {
      if (this.ws) this.ws.close();
    });
  },

  //: 一个标签页 = 一块屏幕。sessionStorage 正好是这个语义:刷新后还在(窗口也还在,
  //: 所以 winId 继续有效),另开一个标签页则是另一块屏幕、另一个 id。
  ensureId() {
    if (this.desktopId) return this.desktopId;
    this.ready = new Promise((resolve) => {
      this._readyResolve = resolve;
    });
    let id = sessionStorage.getItem(this.ID_KEY);
    if (!id) {
      id = "d" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
      sessionStorage.setItem(this.ID_KEY, id);
    }
    this.desktopId = id;
    return id;
  },

  connect() {
    if (this.evicted) return;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(
      `${proto}//${location.host}/api/desktop/screen?desktopId=${encodeURIComponent(this.desktopId)}`
    );
    this.ws = ws;
    ws.onmessage = (ev) => {
      if (this.ws !== ws) return;
      const msg = JSON.parse(ev.data);
      if (msg.type === "cmd") this.handle(msg);
      else if (msg.type === "fs_changed") this.fsChanged(msg);
      else if (msg.type === "evicted") this.onEvicted(msg);
      else if (msg.type === "ready" && this._readyResolve) this._readyResolve();
    };
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      if (this.evicted) return;
      clearTimeout(this.reconnectTimer);
      this.sessionAlive().then((alive) => {
        if (!alive || this.evicted || this.ws) return;
        clearTimeout(this.reconnectTimer);
        this.reconnectTimer = setTimeout(() => this.connect(), 1500);
      });
    };
    ws.onerror = () => {
      ws.close();
    };
  },

  sessionAlive() {
    return fetch("/api/auth/status", { cache: "no-store" })
      .then((res) => res.json())
      .then((body) => {
        if (!body.ok) throw new Error("登录状态读取失败");
        if (body.authenticated) return true;
        location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
        return false;
      });
  },

  onEvicted(msg) {
    this.evicted = true;
    clearTimeout(this.reconnectTimer);
    if (this._readyResolve) {
      this._readyResolve();
      this._readyResolve = null;
    }
    if (this.ws) this.ws.close();
    window.FMDesktopEvicted(msg.reason || "桌面已在别处打开");
  },

  async handle(msg) {
    try {
      const result = await DesktopOS.exec({ op: msg.op, params: msg.params || {} });
      this.send({ type: "result", reqId: msg.reqId, ok: true, result });
    } catch (e) {
      this.send({ type: "result", reqId: msg.reqId, ok: false, error: String((e && e.message) || e) });
    }
  },

  fsChanged(msg) {
    const paths = Array.isArray(msg.paths) ? msg.paths : [];
    const files = Array.isArray(msg.files) ? msg.files : [];
    if (paths.length || files.length) return window.FMNotifyFsChanged(paths, files);
    return window.FMRefreshExplorer();
  },

  send(obj) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(obj));
  },
};
