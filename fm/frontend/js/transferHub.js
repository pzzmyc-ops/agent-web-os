const TransferHub = {
  tasks: [],
  ws: null,
  listeners: [],
  reconnectTimer: 0,
  connected: false,

  init() {
    this.connect();
    window.addEventListener("beforeunload", () => {
      if (this.ws) this.ws.close();
    });
  },

  on(fn) {
    this.listeners.push(fn);
    return () => {
      this.listeners = this.listeners.filter((x) => x !== fn);
    };
  },

  emit() {
    const tasks = this.tasks;
    this.listeners.forEach((fn) => fn(tasks));
    this.updateNotify(tasks);
  },

  applySnapshot(tasks) {
    this.tasks = Array.isArray(tasks) ? tasks : [];
    this.emit();
  },

  applyPatch(tasks, removed) {
    const map = {};
    this.tasks.forEach((t) => {
      map[t.id] = t;
    });
    (removed || []).forEach((id) => {
      delete map[id];
    });
    (tasks || []).forEach((t) => {
      map[t.id] = t;
    });
    this.tasks = Object.keys(map).map((k) => map[k]);
    this.tasks.sort((a, b) => (a.createdAt || 0) - (b.createdAt || 0));
    this.emit();
  },

  connect() {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}/api/transfer/ws`);
    this.ws = ws;
    ws.onopen = () => {
      this.connected = true;
    };
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "snapshot") this.applySnapshot(msg.tasks);
      else if (msg.type === "patch") this.applyPatch(msg.tasks, msg.removed);
    };
    ws.onclose = () => {
      this.connected = false;
      this.ws = null;
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = setTimeout(() => this.connect(), 1500);
    };
    ws.onerror = () => {
      ws.close();
    };
  },

  async refresh() {
    const res = await API.get("/api/transfer/tasks");
    this.applySnapshot(res.data);
  },

  updateNotify(tasks) {
    const active = tasks.filter((t) => ["running", "waiting", "packing"].includes(t.status));
    let box = document.querySelector(".transfer-notify");
    if (!box) {
      box = document.createElement("div");
      box.className = "transfer-notify";
      box.addEventListener("click", () => {
        if (typeof TransferManager !== "undefined") TransferManager.open();
      });
      document.body.appendChild(box);
    }
    if (!active.length) {
      box.classList.remove("show");
      return;
    }
    Loader.loadCss("/css/transfer.css?v=12");
    const uploading = active.filter((t) => t.kind === "upload");
    const downloading = active.filter((t) => t.kind === "download");
    const measured = active.filter((t) => !(t.kind === "download" && t.status === "running"));
    const showBar = measured.length > 0;
    const avg = showBar ? measured.reduce((s, t) => s + (t.progress || 0), 0) / measured.length : 0;
    let title = "传输管理器";
    let line = "";
    if (uploading.length && downloading.length) {
      line = `上传 ${uploading.length} / 下载 ${downloading.length}`;
    } else if (uploading.length) {
      title = "文件上传";
      line = uploading[0].name;
    } else if (!showBar) {
      title = "文件下载";
      line = "浏览器下载中";
    } else {
      title = downloading[0].status === "packing" ? "打包中" : "文件下载";
      line = downloading[0].name;
    }
    const notifyKey = `${active.length}|${title}|${line}|${showBar ? Math.round(avg * 20) : -1}`;
    if (box.dataset.k === notifyKey) {
      box.classList.add("show");
      return;
    }
    box.dataset.k = notifyKey;
    box.classList.add("show");
    box.innerHTML = `
      <div class="tn-title"><i class="font-icon ri-exchange-line"></i><span>${escapeHtml(title)}</span></div>
      <div class="tn-line">进行中 ${active.length} 项</div>
      <div class="tn-line">${escapeHtml(line)}</div>
      ${showBar ? `<div class="tn-bar"><i style="width:${Math.round(avg * 100)}%"></i></div>` : ""}
    `;
  },
};
