//: 这块屏幕对外的连接。后端 /api/desktop/* 不知道桌面能做什么,只负责把命令送到这里;
//: 能做什么、参数怎么校验,全在 DesktopOS 自己那张表里。所以这个文件只做搬运:
//: 收命令 -> DesktopOS.exec -> 回执,不认识任何具体操作。
const DesktopLink = {
  ID_KEY: "fm.desktop.id",
  ws: null,
  reconnectTimer: 0,
  desktopId: "",

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
    let id = sessionStorage.getItem(this.ID_KEY);
    if (!id) {
      id = "d" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
      sessionStorage.setItem(this.ID_KEY, id);
    }
    return id;
  },

  connect() {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(
      `${proto}//${location.host}/api/desktop/screen?desktopId=${encodeURIComponent(this.desktopId)}`
    );
    this.ws = ws;
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "cmd") this.handle(msg);
      else if (msg.type === "fs_changed") this.fsChanged(msg);
    };
    ws.onclose = () => {
      this.ws = null;
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = setTimeout(() => this.connect(), 1500);
    };
    ws.onerror = () => {
      ws.close();
    };
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
