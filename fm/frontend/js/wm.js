const WM_RECT_KEY = "fm.wm.rects";

const WM = {
  z: 100,
  windows: new Map(),
  layer: null,
  taskbar: null,
  _resizeOverlay: null,
  _shieldsHidden: false,
  _rects: {},

  init() {
    this.layer = document.getElementById("window-layer");
    this.taskbar = document.getElementById("taskbar-apps");
    const raw = localStorage.getItem(WM_RECT_KEY);
    this._rects = raw ? JSON.parse(raw) : {};
    this._overlay = document.createElement("div");
    this._overlay.className = "wm-overlay";
    this._overlay.style.cssText =
      "position:fixed;inset:0;z-index:999999;display:none;cursor:default;background:transparent;";
    document.body.appendChild(this._overlay);
    document.addEventListener("pointerdown", (e) => {
      const winEl = e.target.closest(".win");
      if (!winEl) return;
      const id = winEl.dataset.id;
      if (!id) return;
      this.focus(id);
    }, true);
    document.addEventListener("dragstart", () => this._setShieldsHidden(true));
    document.addEventListener("dragend", () => this._setShieldsHidden(false));
  },

  _setShieldsHidden(hidden) {
    this._shieldsHidden = hidden;
    this.layer.querySelectorAll(".win-shield").forEach((shield) => {
      shield.style.display = hidden ? "none" : "block";
    });
  },

  _syncShields(activeId) {
    this.windows.forEach((w) => {
      const shield = w.body.querySelector(":scope > .win-shield");
      if (w.id === activeId || !w.body.querySelector("iframe")) {
        if (shield) shield.remove();
        return;
      }
      if (shield) return;
      const s = document.createElement("div");
      s.className = "win-shield";
      s.style.cssText = "position:absolute;inset:0;z-index:50;background:transparent;";
      if (this._shieldsHidden) s.style.display = "none";
      s.addEventListener("pointerdown", () => this.focus(w.id));
      s.addEventListener("wheel", () => this.focus(w.id), { passive: true });
      w.body.appendChild(s);
    });
  },

  _appOf(id) {
    return DesktopOS._inferApp(id);
  },

  _rememberRect(win) {
    const rect = win.maximized && win.restoreRect ? win.restoreRect : {
      left: win.el.style.left,
      top: win.el.style.top,
      width: win.el.style.width,
      height: win.el.style.height,
    };
    this._rects[this._appOf(win.id)] = {
      left: parseInt(rect.left, 10),
      top: parseInt(rect.top, 10),
      width: parseInt(rect.width, 10),
      height: parseInt(rect.height, 10),
      maximized: !!win.maximized,
    };
    localStorage.setItem(WM_RECT_KEY, JSON.stringify(this._rects));
  },

  _recalledRect(id) {
    const saved = this._rects[this._appOf(id)];
    if (!saved) return null;
    const vw = window.innerWidth, vh = window.innerHeight;
    const width = Math.min(saved.width, vw);
    const height = Math.min(saved.height, vh);
    let left = Math.min(Math.max(saved.left, -width + 80), vw - 80);
    let top = Math.min(Math.max(saved.top, 0), vh - 80);
    const occupied = (l, t) => {
      let hit = false;
      this.windows.forEach((w) => {
        if (!w.minimized && w.el.offsetLeft === l && w.el.offsetTop === t) hit = true;
      });
      return hit;
    };
    while (occupied(left, top) && left + 28 < vw - 80 && top + 28 < vh - 80) {
      left += 28;
      top += 28;
    }
    return { left, top, width, height, maximized: saved.maximized };
  },

  _armOverlay(cursor) {
    this._overlay.style.display = "block";
    this._overlay.style.cursor = cursor || "default";
    document.body.style.userSelect = "none";
  },

  _disarmOverlay() {
    this._overlay.style.display = "none";
    this._overlay.style.cursor = "default";
    document.body.style.userSelect = "";
  },

  nextZ() {
    this.z += 1;
    return this.z;
  },

  create({ id, title, icon, width = 900, height = 560, x, y, content, className = "", titleHtml = "" }) {
    if (this.windows.has(id)) {
      this.focus(id);
      const exist = this.windows.get(id);
      if (exist.minimized) this.restore(id);
      return exist;
    }
    const el = document.createElement("div");
    el.className = ("win " + className).trim();
    el.dataset.id = id;
    const recalled = x == null && y == null ? this._recalledRect(id) : null;
    if (recalled) {
      width = recalled.width;
      height = recalled.height;
    }
    const left = recalled ? recalled.left : x != null ? x : Math.max(40, 60 + this.windows.size * 28);
    const top = recalled ? recalled.top : y != null ? y : Math.max(30, 40 + this.windows.size * 28);
    el.style.width = `${width}px`;
    el.style.height = `${height}px`;
    el.style.left = `${left}px`;
    el.style.top = `${top}px`;
    el.style.zIndex = String(this.nextZ());
    const titleInner = titleHtml || String(title)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
    el.innerHTML = `
      <div class="win-titlebar">
        <div class="win-title">${titleInner}</div>
        <div class="win-controls">
          <button type="button" class="win-btn min" title="最小化"><span class="win-ico"></span></button>
          <button type="button" class="win-btn max" title="最大化"><span class="win-ico"></span></button>
          <button type="button" class="win-btn close" title="关闭"><span class="win-ico"></span></button>
        </div>
      </div>
      <div class="win-body"></div>
      <div class="win-resize n" data-dir="n"></div>
      <div class="win-resize s" data-dir="s"></div>
      <div class="win-resize e" data-dir="e"></div>
      <div class="win-resize w" data-dir="w"></div>
      <div class="win-resize ne" data-dir="ne"></div>
      <div class="win-resize nw" data-dir="nw"></div>
      <div class="win-resize se" data-dir="se"></div>
      <div class="win-resize sw" data-dir="sw"></div>
    `;
    const body = el.querySelector(".win-body");
    if (typeof content === "string") body.innerHTML = content;
    else if (content instanceof Node) body.appendChild(content);

    const win = {
      id, el, title, icon, body,
      minimized: false,
      maximized: false,
      restoreRect: null,
      onClose: null,
      lastTouchAt: 0,
    };
    this.windows.set(id, win);
    this.layer.appendChild(el);
    this.bindWindow(win);
    if (recalled && recalled.maximized) this.toggleMaximize(id);
    this.renderTaskbar();
    this.focus(id);
    return win;
  },

  bindWindow(win) {
    const { el } = win;
    const bar = el.querySelector(".win-titlebar");
    el.querySelector(".win-btn.min").onclick = (e) => {
      e.stopPropagation();
      this.minimize(win.id);
    };
    el.querySelector(".win-btn.max").onclick = (e) => {
      e.stopPropagation();
      this.toggleMaximize(win.id);
    };
    el.querySelector(".win-btn.close").onclick = (e) => {
      e.stopPropagation();
      this.close(win.id);
    };
    bar.addEventListener("dblclick", () => {
      if (Date.now() - win.lastTouchAt < 300) return;
      this.toggleMaximize(win.id);
    });
    this.enableDrag(win, bar);
    el.querySelectorAll(".win-resize").forEach((handle) => {
      this.enableResize(win, handle, handle.dataset.dir);
    });
  },

  enableDrag(win, bar) {
    let sx = 0, sy = 0, ox = 0, oy = 0, pid = null, moved = false;
    let tapAt = 0, tapX = 0, tapY = 0;
    const onMove = (e) => {
      if (e.pointerId !== pid) return;
      if (Math.abs(e.clientX - sx) >= 10 || Math.abs(e.clientY - sy) >= 10) moved = true;
      if (win.maximized) return;
      win.el.style.left = `${Math.max(-win.el.offsetWidth + 80, ox + (e.clientX - sx))}px`;
      win.el.style.top = `${Math.max(0, oy + (e.clientY - sy))}px`;
    };
    const onUp = (e) => {
      if (e.pointerId !== pid) return;
      pid = null;
      win.el.classList.remove("dragging");
      this._disarmOverlay();
      if (moved && !win.maximized) this._rememberRect(win);
      if (e.pointerType !== "touch") return;
      const now = Date.now();
      win.lastTouchAt = now;
      if (e.type !== "pointerup" || moved) { tapAt = 0; return; }
      if (now - tapAt < 350 && Math.abs(e.clientX - tapX) < 10 && Math.abs(e.clientY - tapY) < 10) {
        tapAt = 0;
        this.toggleMaximize(win.id);
        return;
      }
      tapAt = now; tapX = e.clientX; tapY = e.clientY;
    };
    bar.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || pid !== null) return;
      if (e.target.closest(".win-btn")) return;
      if (e.pointerType === "touch") { e.preventDefault(); win.lastTouchAt = Date.now(); }
      this.focus(win.id);
      pid = e.pointerId;
      moved = false;
      sx = e.clientX; sy = e.clientY;
      ox = win.el.offsetLeft; oy = win.el.offsetTop;
      bar.setPointerCapture(pid);
      if (win.maximized) return;
      win.el.classList.add("dragging");
      this._armOverlay("move");
    });
    bar.addEventListener("pointermove", onMove);
    bar.addEventListener("pointerup", onUp);
    bar.addEventListener("pointercancel", onUp);
  },

  enableResize(win, handle, dir) {
    let sx = 0, sy = 0, rect = null, pid = null;
    const onMove = (e) => {
      if (e.pointerId !== pid) return;
      if (win.maximized) return;
      const dx = e.clientX - sx, dy = e.clientY - sy;
      let left = rect.left, top = rect.top, width = rect.width, height = rect.height;
      if (dir.includes("e")) width = Math.max(480, rect.width + dx);
      if (dir.includes("s")) height = Math.max(320, rect.height + dy);
      if (dir.includes("w")) { width = Math.max(480, rect.width - dx); left = rect.left + rect.width - width; }
      if (dir.includes("n")) { height = Math.max(320, rect.height - dy); top = rect.top + rect.height - height; }
      win.el.style.left = `${left}px`; win.el.style.top = `${Math.max(0, top)}px`;
      win.el.style.width = `${width}px`; win.el.style.height = `${height}px`;
    };
    const onUp = (e) => {
      if (e.pointerId !== pid) return;
      pid = null;
      win.el.classList.remove("resizing");
      this._disarmOverlay();
      if (!win.maximized) this._rememberRect(win);
    };
    handle.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || pid !== null) return;
      e.preventDefault(); e.stopPropagation();
      this.focus(win.id);
      pid = e.pointerId;
      sx = e.clientX; sy = e.clientY;
      rect = { left: win.el.offsetLeft, top: win.el.offsetTop, width: win.el.offsetWidth, height: win.el.offsetHeight };
      handle.setPointerCapture(pid);
      win.el.classList.add("resizing");
      this._armOverlay(handle.style.cursor || "se-resize");
    });
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
    handle.addEventListener("pointercancel", onUp);
  },

  focus(id) {
    this.windows.forEach((w) => w.el.classList.remove("active"));
    const win = this.windows.get(id);
    if (!win) return;
    win.el.classList.add("active");
    win.el.style.zIndex = String(this.nextZ());
    this._syncShields(id);
    if (win.minimized) this.restore(id);
    this.renderTaskbar();
  },

  minimize(id) {
    const win = this.windows.get(id);
    if (!win) return;
    win.minimized = true;
    win.el.classList.add("minimized");
    win.el.classList.remove("active");
    this._syncShields(null);
    this.renderTaskbar();
  },

  restore(id) {
    const win = this.windows.get(id);
    if (!win) return;
    win.minimized = false;
    win.el.classList.remove("minimized");
    this.focus(id);
  },

  _paintMaxBtn(win) {
    var btn = win.el.querySelector(".win-btn.max");
    if (!btn) return;
    var restored = !!win.maximized;
    btn.title = restored ? "还原" : "最大化";
    btn.classList.toggle("is-restore", restored);
  },

  toggleMaximize(id) {
    const win = this.windows.get(id);
    if (!win) return;
    if (win.maximized) {
      win.maximized = false;
      win.el.classList.remove("maximized");
      if (win.restoreRect) {
        win.el.style.left = win.restoreRect.left;
        win.el.style.top = win.restoreRect.top;
        win.el.style.width = win.restoreRect.width;
        win.el.style.height = win.restoreRect.height;
      }
    } else {
      win.restoreRect = {
        left: win.el.style.left, top: win.el.style.top,
        width: win.el.style.width, height: win.el.style.height,
      };
      win.maximized = true;
      win.el.classList.add("maximized");
    }
    this._paintMaxBtn(win);
    this._rememberRect(win);
    this.focus(id);
  },

  close(id) {
    const win = this.windows.get(id);
    if (!win) return;
    if (typeof win.onClose === "function") {
      const ok = win.onClose();
      if (ok === false) return;
    }
    this._rememberRect(win);
    win.el.remove();
    this.windows.delete(id);
    this.renderTaskbar();
  },

  setTitle(id, title) {
    const win = this.windows.get(id);
    if (!win) return;
    win.title = title;
    win.el.querySelector(".win-title").textContent = title;
    this.renderTaskbar();
  },

  showTaskbarMenu(x, y, id) {
    const menu = document.getElementById("ctx-menu");
    menu.innerHTML = "";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = "关闭";
    btn.onclick = () => {
      menu.classList.add("hidden");
      this.close(id);
    };
    menu.appendChild(btn);
    menu.classList.remove("hidden");
    const rect = menu.getBoundingClientRect();
    menu.style.left = `${Math.min(x, window.innerWidth - rect.width - 4)}px`;
    menu.style.top = `${Math.min(y, window.innerHeight - rect.height - 4)}px`;
  },

  renderTaskbar() {
    this.taskbar.innerHTML = "";
    this.windows.forEach((win) => {
      const item = document.createElement("div");
      item.className = "task-item" + (win.el.classList.contains("active") && !win.minimized ? " active" : "");
      item.innerHTML = `<span class="tico" style="background-image:url('${win.icon}')"></span><span>${win.title}</span>`;
      item.onclick = () => {
        if (win.minimized) this.restore(win.id);
        else if (win.el.classList.contains("active")) this.minimize(win.id);
        else this.focus(win.id);
      };
      item.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.showTaskbarMenu(e.clientX, e.clientY, win.id);
      });
      this.taskbar.appendChild(item);
    });
  },
};
