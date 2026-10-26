//: 窗口存档放在服务器的 desktop_state.json 里(DesktopState),同一时刻只有一块桌面在线,
//: 新页面顶掉旧页面后从同一份存档恢复窗口。
const EMBEDDED_APP_DEFS = {
  hermes: HermesApp,
  deepseek: DeepSeekApp,
  comfyui: ComfyUIApp,
  remote: RemoteApp,
  onlyoffice: OnlyOfficeApp,
};

async function registerEmbeddedApps() {
  const res = await API.get("/api/embedded-apps");
  const apps = res.data && res.data.apps;
  if (!Array.isArray(apps)) {
    throw new Error("嵌入程序列表格式不对");
  }
  const live = new Set();
  for (const item of apps) {
    const def = EMBEDDED_APP_DEFS[item.name];
    if (!def) {
      throw new Error("未知嵌入程序: " + item.name);
    }
    live.add(item.name);
    kodApp.add(def);
  }
  for (const name of Object.keys(EMBEDDED_APP_DEFS)) {
    if (!live.has(name) && kodApp.get(name)) {
      kodApp.remove(name);
    }
  }
}
const SESSION_SAVE_OPS = [
  "window.create",
  "window.close",
  "window.focus",
  "window.minimize",
  "window.restore",
  "window.maximize",
  "app.open",
  "app.launch",
];

const Session = {
  paths: new Map(),
  restoring: false,

  init() {
    const trace = [];
    DesktopOS.on((ev) => {
      trace.push(`${Date.now() % 100000} ${ev.op}:${ev.phase}:${ev.winId || ev.app || ""}:${ev.path || ""}`);
      sessionStorage.setItem("fm.session.trace", JSON.stringify(trace));
    });
    DesktopOS.on((ev) => {
      if (ev.phase !== "complete") return;
      if (ev.op === "app.open" && ev.winId && ev.path) this.paths.set(ev.winId, ev.path);
      if (ev.op === "window.close" && ev.winId) this.paths.delete(ev.winId);
      if (SESSION_SAVE_OPS.includes(ev.op)) this.save();
    });
    window.addEventListener("beforeunload", () => {
      if (DesktopLink.evicted) return;
      this.save();
      DesktopState.push().catch((err) => console.error("[desktop-state] 退出前保存失败:", err));
    });
  },

  rectOf(win) {
    if (win.maximized && win.restoreRect) return { ...win.restoreRect };
    return {
      left: win.el.style.left,
      top: win.el.style.top,
      width: win.el.style.width,
      height: win.el.style.height,
    };
  },

  entryOf(info) {
    const win = WM.windows.get(info.winId);
    if (!win) return null;
    const entry = {
      app: info.app,
      z: info.z,
      minimized: info.minimized,
      maximized: info.maximized,
      active: info.active,
      rect: this.rectOf(win),
      paths: [],
      activePath: "",
    };
    if (info.app === "explorer") {
      entry.paths = [info.state && info.state.path ? info.state.path : "/"];
      return entry;
    }
    if (info.app === "aceEditor" && info.state && info.state.tabs) {
      entry.paths = info.state.tabs.map((t) => t.path).filter(Boolean);
      entry.activePath = info.state.activePath || "";
      return entry;
    }
    if (info.app === "terminal") {
      if (!info.state || !info.state.sessionId) return null;
      entry.paths = [info.state.path];
      entry.session = info.state.sessionId;
      return entry;
    }
    const path = this.paths.get(info.winId);
    if (path) entry.paths = [path];
    return entry;
  },

  save() {
    if (this.restoring || DesktopLink.evicted) return;
    const list = [];
    DesktopOS.listWindows().forEach((info) => {
      const entry = this.entryOf(info);
      if (entry) list.push(entry);
    });
    DesktopState.set("windows", list);
  },

  async reopen(entry) {
    if (entry.app === "terminal") {
      const win = await FMTerminal.reattach(entry.session, entry.paths[0]);
      return win.id;
    }
    if (entry.app === "explorer") {
      const res = await DesktopOS.exec({ op: "explorer.open", params: { path: entry.paths[0] || "/" } });
      return res.winId;
    }
    if (entry.paths && entry.paths.length) {
      let winId = "";
      for (const path of entry.paths) {
        const res = await DesktopOS.exec({ op: "file.open", params: { path, app: entry.app } });
        winId = res.winId;
      }
      if (entry.activePath && entry.activePath !== entry.paths[entry.paths.length - 1]) {
        await DesktopOS.exec({ op: "file.open", params: { path: entry.activePath, app: entry.app } });
      }
      return winId;
    }
    const res = await DesktopOS.exec({ op: "app.launch", params: { app: entry.app } });
    return res.winId;
  },

  applyRect(winId, entry) {
    const win = WM.windows.get(winId);
    if (!win) return;
    if (win.maximized) WM.toggleMaximize(winId);
    win.el.style.left = entry.rect.left;
    win.el.style.top = entry.rect.top;
    win.el.style.width = entry.rect.width;
    win.el.style.height = entry.rect.height;
    if (entry.maximized && !win.maximized) WM.toggleMaximize(winId);
    if (entry.minimized && !win.minimized) WM.minimize(winId);
  },

  async restore() {
    const list = DesktopState.get("windows").slice().sort((a, b) => a.z - b.z);
    if (!list.length) return;
    this.restoring = true;
    let activeId = "";
    for (const entry of list) {
      try {
        const winId = await this.reopen(entry);
        if (!winId) continue;
        this.applyRect(winId, entry);
        if (entry.active) activeId = winId;
      } catch (err) {
        toast(`恢复窗口失败: ${entry.paths[0] || entry.app}（${err.message || err}）`);
      }
    }
    this.restoring = false;
    if (activeId && WM.windows.has(activeId)) WM.focus(activeId);
    this.save();
  },
};

window.FMDesktopEvicted = function (reason) {
  document.body.innerHTML = "";
  const box = document.createElement("div");
  box.className = "desktop-evicted";
  const title = document.createElement("h1");
  title.textContent = reason;
  const hint = document.createElement("p");
  hint.textContent = "同一时刻只允许一块桌面在线。需要在这里继续用,点下面的按钮,别处那块会被停用。";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.textContent = "在这里继续使用";
  btn.addEventListener("click", () => location.reload());
  box.appendChild(title);
  box.appendChild(hint);
  box.appendChild(btn);
  document.body.appendChild(box);
};

document.addEventListener("DOMContentLoaded", async () => {
  DesktopLink.ensureId();
  await DesktopState.load();
  WM.init();
  // 必须在创建任何窗口/启动任何 app 之前装好打点,否则早期事件会漏。
  DesktopOS.install();
  TransferHub.init();
  await FMLoadEnv();
  FileOp.restore().catch((err) => toast(err.message || String(err)));
  Desktop.init();
  StartMenu.init();
  Session.init();
  DesktopLink.init();
  await registerEmbeddedApps();
  StartMenu.render();
  Session.restore();
});
