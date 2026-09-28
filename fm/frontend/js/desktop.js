const DESK_POS_KEY = "fm.desktop.iconPos";
const DESK_KEYS_TYPE = "application/x-fm-desk-keys";
const DESK_CELL_W = 96;
const DESK_CELL_H = 100;

const Desktop = {
  path: "",
  homeName: "我的文件",
  selected: new Set(),
  positions: {},
  dragMove: null,

  async init() {
    this.box = document.getElementById("desktop-icons");
    this.layer = document.getElementById("desktop");
    this.path = window.FMEnv.desktop;
    this.loadPositions();
    await this.refresh();
    this.bindEvents();
    Uploader.onComplete = (dests) => {
      const paths = dests && dests.length ? dests : [this.path];
      return FMNotifyFsChanged(paths);
    };
    window.FMRefreshDesktop = () => this.refresh();
    const clock = document.getElementById("taskbar-clock");
    const tick = () => {
      const d = new Date();
      const p = (x) => String(x).padStart(2, "0");
      clock.textContent = `${p(d.getHours())}:${p(d.getMinutes())}`;
    };
    tick();
    setInterval(tick, 10000);
  },

  loadPositions() {
    const raw = localStorage.getItem(DESK_POS_KEY);
    this.positions = raw ? JSON.parse(raw) : {};
  },

  savePositions() {
    localStorage.setItem(DESK_POS_KEY, JSON.stringify(this.positions));
  },

  iconEl(key) {
    return [...this.box.querySelectorAll(".desk-icon")].find(
      (n) => (n.dataset.special === "home" ? "home" : n.dataset.path) === key
    );
  },

  clampPos(x, y) {
    const rect = this.box.getBoundingClientRect();
    const maxX = Math.max(0, rect.width - DESK_CELL_W);
    const maxY = Math.max(0, rect.height - DESK_CELL_H);
    return [
      Math.min(Math.max(0, Math.round(x)), maxX),
      Math.min(Math.max(0, Math.round(y)), maxY),
    ];
  },

  dropPoint(e) {
    const rect = this.box.getBoundingClientRect();
    return this.clampPos(e.clientX - rect.left - DESK_CELL_W / 2, e.clientY - rect.top - DESK_CELL_H / 2);
  },

  layout(keys) {
    const rect = this.box.getBoundingClientRect();
    const rows = Math.max(1, Math.floor(rect.height / DESK_CELL_H));
    const used = new Set();
    keys.forEach((key) => {
      const p = this.positions[key];
      if (p) used.add(`${Math.round(p[0] / DESK_CELL_W)}:${Math.round(p[1] / DESK_CELL_H)}`);
    });
    const spots = {};
    let cursor = 0;
    keys.forEach((key) => {
      const p = this.positions[key];
      if (p) {
        spots[key] = this.clampPos(p[0], p[1]);
        return;
      }
      while (used.has(`${Math.floor(cursor / rows)}:${cursor % rows}`)) cursor += 1;
      const col = Math.floor(cursor / rows);
      const row = cursor % rows;
      used.add(`${col}:${row}`);
      cursor += 1;
      spots[key] = [col * DESK_CELL_W, row * DESK_CELL_H];
    });
    return spots;
  },

  prunePositions(keys) {
    const alive = new Set(keys);
    const stale = Object.keys(this.positions).filter((k) => !alive.has(k));
    if (!stale.length) return;
    stale.forEach((k) => delete this.positions[k]);
    this.savePositions();
  },

  moveIcons(keys, e) {
    const start = this.dragMove;
    if (!start) return;
    const dx = e.clientX - start.clientX;
    const dy = e.clientY - start.clientY;
    keys.forEach((key) => {
      const origin = start.origins[key];
      this.positions[key] = this.clampPos(origin[0] + dx, origin[1] + dy);
    });
    this.dragMove = null;
    this.savePositions();
    this.render();
  },

  async refresh() {
    const res = await API.list(this.path);
    this.items = [...res.data.folderList, ...res.data.fileList];
    this.selected.clear();
    this.render();
  },

  render() {
    this.box.innerHTML = "";
    const keys = ["home", ...this.items.map((it) => it.path)];
    this.prunePositions(keys);
    const spots = this.layout(keys);
    this._spots = spots;
    const vr = this.box.getBoundingClientRect();
    const buf = 120;
    const inView = (x, y) => (
      x + DESK_CELL_W > -buf
      && y + DESK_CELL_H > -buf
      && x < vr.width + buf
      && y < vr.height + buf
    );

    if (inView(spots.home[0], spots.home[1])) {
      const home = document.createElement("div");
      home.className = "desk-icon desk-home";
      home.dataset.special = "home";
      home.draggable = true;
      home.style.left = `${spots.home[0]}px`;
      home.style.top = `${spots.home[1]}px`;
      home.innerHTML = `
        <div class="ico" style="background-image:url('${FOLDER_ICON}')"></div>
        <div class="name">${this.homeName}</div>
      `;
      this.box.appendChild(home);
    }

    this.items.forEach((item, index) => {
      const pos = spots[item.path];
      if (!pos || !inView(pos[0], pos[1])) return;
      const el = document.createElement("div");
      el.className = "desk-icon" + (this.selected.has(item.path) ? " selected" : "");
      el.dataset.path = item.path;
      el.dataset.index = String(index);
      el.dataset.type = item.type;
      el.draggable = true;
      el.style.left = `${pos[0]}px`;
      el.style.top = `${pos[1]}px`;
      el.innerHTML = `
        <div class="ico"></div>
        <div class="name">${escapeHtml(item.name)}</div>
      `;
      setFileArtwork(el.querySelector(".ico"), item);
      this.box.appendChild(el);
    });
  },

  iconByEvent(e) {
    const el = e.target.closest(".desk-icon");
    if (!el || !this.box.contains(el)) return null;
    if (el.dataset.special === "home") return { el, item: null, isHome: true };
    const item = (this.items || []).find((it) => it.path === el.dataset.path);
    if (!item) return null;
    return { el, item, isHome: false };
  },

  iconOrigin(key) {
    const node = this.iconEl(key);
    if (node) return [parseFloat(node.style.left), parseFloat(node.style.top)];
    const spot = (this._spots || {})[key];
    return spot ? [spot[0], spot[1]] : [0, 0];
  },

  selectedItems() {
    return (this.items || []).filter((it) => this.selected.has(it.path));
  },

  updateSelectionUI() {
    this.box.querySelectorAll(".desk-icon").forEach((n) => {
      if (n.dataset.special === "home") {
        n.classList.remove("selected");
        return;
      }
      n.classList.toggle("selected", this.selected.has(n.dataset.path));
    });
  },

  async openIcon(hit) {
    if (hit.isHome) {
      openExplorer(window.FMEnv.workspace);
      return;
    }
    if (hit.item.type === "folder") {
      openExplorer(hit.item.path);
      return;
    }
    await openFile(hit.item);
  },

  showMenu(x, y, onItem) {
    const menu = document.getElementById("ctx-menu");
    const items = onItem ? this.selectedItems() : [];
    const paths = items.map((it) => it.path);
    let buttons = [];
    if (!onItem) {
      buttons = [
        ["打开我的文件", () => openExplorer(window.FMEnv.workspace)],
        ["打开此电脑", () => openExplorer("/")],
        ["刷新", () => this.refresh()],
        ["粘贴", () => pasteIntoPath(this.path)],
        ["-", null],
        ["新建文件夹", async () => {
          const name = await promptModal("新建文件夹", "新建文件夹");
          if (!name) return;
          await API.mkdir(this.path, name);
          await FMNotifyFsChanged([this.path]);
        }],
        ["新建文件", async () => {
          const name = await promptModal("新建文件", "新建文件.txt");
          if (!name) return;
          await API.mkfile(this.path, name, "");
          await FMNotifyFsChanged([this.path]);
        }],
        ["上传文件", () => Uploader.open(this.path)],
      ];
    } else if (items.length === 1) {
      buttons = [
        ["打开", async () => {
          const it = items[0];
          if (it.type === "folder") openExplorer(it.path);
          else await openFile(it);
        }],
        ["-", null],
        ["下载", () => Downloader.download(items)],
      ];
      if (window.FMEnv.has7z && items[0].type === "file" && fmIsArchiveName(items[0].name)) {
        buttons.push(["解压", async () => {
          await FMArchiveExtract(items[0], this.path);
        }]);
      }
      buttons.push(
        ["-", null],
        ["复制", () => {
          AppClipboard.mode = "copy";
          AppClipboard.paths = paths;
          toast("已复制");
        }],
        ["剪切", () => {
          AppClipboard.mode = "cut";
          AppClipboard.paths = paths;
          toast("已剪切");
        }],
        ["复制路径", () => fmCopyPaths(items)],
        ["-", null],
        ["重命名", async () => {
          const name = await promptModal("重命名", items[0].name);
          if (!name || name === items[0].name) return;
          await API.rename(items[0].path, name);
          await FMNotifyFsChanged([parentPath(items[0].path)]);
        }],
        ["删除", async () => {
          const okDel = await confirmModal("删除", `确定删除选中的 ${paths.length} 项吗？`);
          if (!okDel) return;
          await API.remove(paths);
          await FMNotifyFsChanged([this.path]);
        }],
        ["-", null],
        ["属性", () => fmShowProps(items)],
      );
    } else if (items.length > 1) {
      buttons = [
        ["复制", () => {
          AppClipboard.mode = "copy";
          AppClipboard.paths = paths;
          toast("已复制");
        }],
        ["剪切", () => {
          AppClipboard.mode = "cut";
          AppClipboard.paths = paths;
          toast("已剪切");
        }],
        ["复制路径", () => fmCopyPaths(items)],
        ["下载", () => Downloader.download(items)],
        ["-", null],
        ["删除", async () => {
          const okDel = await confirmModal("删除", `确定删除选中的 ${paths.length} 项吗？`);
          if (!okDel) return;
          await API.remove(paths);
          await FMNotifyFsChanged([this.path]);
        }],
        ["-", null],
        ["属性", () => fmShowProps(items)],
      ];
    }
    menu.innerHTML = "";
    buttons.forEach((row) => {
      const [label, fn, disabled] = row;
      if (label === "-") {
        menu.appendChild(document.createElement("hr"));
        return;
      }
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = label;
      btn.disabled = !!disabled;
      btn.onclick = async () => {
        menu.classList.add("hidden");
        try {
          await fn();
        } catch (err) {
          toast(err.message || String(err));
        }
      };
      menu.appendChild(btn);
    });
    menu.classList.remove("hidden");
    const rect = menu.getBoundingClientRect();
    menu.style.left = `${Math.min(x, window.innerWidth - rect.width - 4)}px`;
    menu.style.top = `${Math.min(y, window.innerHeight - rect.height - 4)}px`;
  },

  bindEvents() {
    this.box.addEventListener("click", (e) => {
      const hit = this.iconByEvent(e);
      if (!hit || e.button !== 0) return;
      if (!e.ctrlKey && !e.metaKey) this.selected.clear();
      if (hit.isHome) {
        this.box.querySelectorAll(".desk-icon").forEach((n) => n.classList.remove("selected"));
        hit.el.classList.add("selected");
        return;
      }
      if (this.selected.has(hit.item.path)) this.selected.delete(hit.item.path);
      else this.selected.add(hit.item.path);
      this.updateSelectionUI();
    });
    this.box.addEventListener("dblclick", (e) => {
      if (fmIsTouchMode()) return;
      const hit = this.iconByEvent(e);
      if (!hit) return;
      e.preventDefault();
      this.openIcon(hit).catch((err) => toast(err.message || String(err)));
    });
    this.box.addEventListener("contextmenu", (e) => {
      if (fmIsTouchMode()) {
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      const hit = this.iconByEvent(e);
      if (!hit) return;
      e.preventDefault();
      e.stopPropagation();
      if (!hit.isHome) {
        if (!this.selected.has(hit.item.path)) {
          this.selected.clear();
          this.selected.add(hit.item.path);
          this.updateSelectionUI();
        }
        this.showMenu(e.clientX, e.clientY, true);
      } else {
        this.showMenu(e.clientX, e.clientY, false);
      }
    });
    this.box.addEventListener("dragstart", (e) => {
      if (fmIsTouchMode()) {
        e.preventDefault();
        return;
      }
      const hit = this.iconByEvent(e);
      if (!hit) return;
      if (hit.isHome) {
        this.selected.clear();
        this.updateSelectionUI();
      } else if (!this.selected.has(hit.item.path)) {
        this.selected.clear();
        this.selected.add(hit.item.path);
        this.updateSelectionUI();
      }
      const keys = hit.isHome ? ["home"] : [...this.selected];
      this.dragMove = { clientX: e.clientX, clientY: e.clientY, origins: {} };
      keys.forEach((key) => {
        this.dragMove.origins[key] = this.iconOrigin(key);
      });
      e.dataTransfer.setData(DESK_KEYS_TYPE, JSON.stringify(keys));
      if (hit.isHome) e.dataTransfer.effectAllowed = "move";
      else fmDragStart(e, keys);
    });
    this.box.addEventListener("dragend", () => {
      window.FMDragState.paths = [];
      this.box.querySelectorAll(".drop-target").forEach((n) => n.classList.remove("drop-target"));
    });
    this.box.addEventListener("dragover", (e) => {
      const hit = this.iconByEvent(e);
      if (!hit) return;
      const dest = hit.isHome ? window.FMEnv.workspace : (hit.item.type === "folder" ? hit.item.path : "");
      if (!dest || !fmDragCanDrop(e, dest)) return;
      e.preventDefault();
      e.stopPropagation();
      e.dataTransfer.dropEffect = fmDragIsInternal(e) ? fmDragEffect(e) : "copy";
      hit.el.classList.add("drop-target");
      this.layer.classList.remove("desktop-dragover");
    });
    this.box.addEventListener("dragleave", (e) => {
      const hit = this.iconByEvent(e);
      if (hit) hit.el.classList.remove("drop-target");
    });
    this.box.addEventListener("drop", (e) => {
      const hit = this.iconByEvent(e);
      if (!hit) return;
      const dest = hit.isHome ? window.FMEnv.workspace : (hit.item.type === "folder" ? hit.item.path : "");
      if (!dest || !fmDragCanDrop(e, dest)) return;
      e.preventDefault();
      e.stopPropagation();
      hit.el.classList.remove("drop-target");
      fmDropInto(e, dest).catch((err) => toast(err.message || String(err)));
    });
    let skipBlankClick = false;
    const rectsIntersect = (a, b) => !(a.right < b.left || a.left > b.right || a.bottom < b.top || a.top > b.bottom);

    this.layer.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return;
      if (e.target.closest(".desk-icon, .win, #taskbar, #start-menu, .ctx-menu, .modal-mask, .select-container")) return;
      document.getElementById("ctx-menu").classList.add("hidden");
      const startClientX = e.clientX;
      const startClientY = e.clientY;
      const lrect0 = this.layer.getBoundingClientRect();
      const startX = startClientX - lrect0.left;
      const startY = startClientY - lrect0.top;
      const keep = e.ctrlKey || e.metaKey;
      const base = keep ? new Set(this.selected) : new Set();
      let moved = false;
      let boxEl = null;
      const onMove = (ev) => {
        const dx = ev.clientX - startClientX;
        const dy = ev.clientY - startClientY;
        if (!moved && Math.abs(dx) < 4 && Math.abs(dy) < 4) return;
        if (!moved) {
          moved = true;
          if (!keep) this.selected.clear();
          boxEl = document.createElement("div");
          boxEl.className = "select-container desktop-select";
          this.layer.appendChild(boxEl);
        }
        const lrect = this.layer.getBoundingClientRect();
        const curX = ev.clientX - lrect.left;
        const curY = ev.clientY - lrect.top;
        const left = Math.min(startX, curX);
        const top = Math.min(startY, curY);
        const width = Math.abs(curX - startX);
        const height = Math.abs(curY - startY);
        boxEl.style.left = `${left}px`;
        boxEl.style.top = `${top}px`;
        boxEl.style.width = `${width}px`;
        boxEl.style.height = `${height}px`;
        const selRect = {
          left: lrect.left + left,
          top: lrect.top + top,
          right: lrect.left + left + width,
          bottom: lrect.top + top + height,
        };
        const next = new Set(base);
        const spots = this._spots || {};
        (this.items || []).forEach((item) => {
          const p = spots[item.path];
          if (!p) return;
          const r = {
            left: lrect.left + p[0],
            top: lrect.top + p[1],
            right: lrect.left + p[0] + DESK_CELL_W,
            bottom: lrect.top + p[1] + DESK_CELL_H,
          };
          if (rectsIntersect(selRect, r)) {
            if (keep && base.has(item.path)) next.delete(item.path);
            else next.add(item.path);
          }
        });
        this.selected = next;
        this.updateSelectionUI();
      };
      const onUp = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        if (boxEl) boxEl.remove();
        if (moved) skipBlankClick = true;
        else if (!keep) {
          this.selected.clear();
          this.updateSelectionUI();
        }
      };
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    });
    this.layer.addEventListener("click", (e) => {
      if (e.target.closest(".desk-icon, .win, #taskbar, #start-menu, .ctx-menu, .modal-mask")) return;
      if (skipBlankClick) {
        skipBlankClick = false;
        return;
      }
    });
    this.layer.addEventListener("contextmenu", (e) => {
      if (e.target.closest(".desk-icon, .win, #taskbar, #start-menu, .ctx-menu, .modal-mask")) return;
      if (fmIsTouchMode()) {
        e.preventDefault();
        return;
      }
      e.preventDefault();
      this.selected.clear();
      this.updateSelectionUI();
      this.showMenu(e.clientX, e.clientY, false);
    });
    document.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".ctx-menu")) return;
      document.getElementById("ctx-menu").classList.add("hidden");
    }, true);
    window.addEventListener("resize", () => this.render());
    this.layer.addEventListener("dragover", (e) => {
      if (e.target.closest(".win, #taskbar")) return;
      if ([...e.dataTransfer.types].includes(DESK_KEYS_TYPE)) {
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        return;
      }
      if (!fmDragCanDrop(e, this.path)) return;
      e.preventDefault();
      if (fmDragIsInternal(e)) {
        e.dataTransfer.dropEffect = fmDragEffect(e);
        return;
      }
      e.dataTransfer.dropEffect = "copy";
      this.layer.classList.add("desktop-dragover");
    });
    this.layer.addEventListener("dragleave", (e) => {
      if (e.target === this.layer) this.layer.classList.remove("desktop-dragover");
    });
    this.layer.addEventListener("drop", (e) => {
      if (e.target.closest(".win, #taskbar")) return;
      const deskKeys = e.dataTransfer.getData(DESK_KEYS_TYPE);
      if (deskKeys) {
        e.preventDefault();
        this.moveIcons(JSON.parse(deskKeys), e);
        return;
      }
      if (!fmDragCanDrop(e, this.path)) return;
      e.preventDefault();
      this.layer.classList.remove("desktop-dragover");
      if (fmDragIsInternal(e)) {
        const names = fmDragPaths(e).map((p) => pathBaseName(p));
        const spot = this.dropPoint(e);
        fmDropInto(e, this.path).then(() => {
          names.forEach((name, i) => {
            this.positions[joinPath(this.path, name)] = this.clampPos(spot[0] + i * 16, spot[1] + i * 16);
          });
          this.savePositions();
          this.render();
        }).catch((err) => {
          toast(err.message || String(err));
        });
        return;
      }
      fmDropInto(e, this.path).catch((err) => {
        toast(err.message || String(err));
      });
    });
    document.addEventListener("paste", (e) => {
      if (e.target.closest("input, textarea, [contenteditable], .win")) return;
      e.preventDefault();
      pasteIntoPath(this.path, e.clipboardData).catch((err) => {
        toast(err.message || String(err));
      });
    });
    this.bindTouchEvents();
  },

  bindTouchEvents() {
    const touch = {
      mode: "idle",
      hit: null,
      keys: [],
      startX: 0,
      startY: 0,
      lastX: 0,
      lastY: 0,
      timer: null,
      ghost: null,
      lastTapKey: "",
      lastTapTime: 0,
    };
    const tapKey = (hit) => (hit.isHome ? "home" : hit.item.path);
    const resetTouch = (keepTap) => {
      if (touch.timer) clearTimeout(touch.timer);
      if (touch.ghost) touch.ghost.remove();
      if (touch.hit) touch.hit.el.classList.remove("is-touch-armed");
      fmTouchHighlight(null);
      touch.mode = "idle";
      touch.hit = null;
      touch.keys = [];
      touch.timer = null;
      touch.ghost = null;
      if (!keepTap) {
        touch.lastTapKey = "";
        touch.lastTapTime = 0;
      }
    };
    const resolveDrop = (x, y) => {
      const drop = fmTouchDropHitAt(x, y);
      const paths = touch.keys.filter((k) => k !== "home");
      const canTransfer = paths.length === touch.keys.length
        && drop.cls !== "desktop-dragover"
        && !!drop.path
        && fmPathsCanDrop(paths, drop.path);
      return { drop, paths, canTransfer };
    };
    this.layer.addEventListener("touchstart", (e) => {
      fmMarkTouchInput();
      if (e.target.closest(".win, #taskbar, #start-menu, .ctx-menu, .modal-mask")) return;
      if (e.touches.length !== 1) {
        resetTouch(false);
        return;
      }
      document.getElementById("ctx-menu").classList.add("hidden");
      resetTouch(true);
      const t = e.touches[0];
      const hit = this.iconByEvent(e);
      touch.mode = "pending_hold";
      touch.hit = hit;
      touch.startX = t.clientX;
      touch.startY = t.clientY;
      touch.lastX = t.clientX;
      touch.lastY = t.clientY;
      touch.timer = setTimeout(() => {
        touch.timer = null;
        if (touch.mode !== "pending_hold") return;
        touch.mode = "armed";
        touch.startX = touch.lastX;
        touch.startY = touch.lastY;
        if (hit) {
          if (!hit.isHome && !this.selected.has(hit.item.path)) {
            this.selected.clear();
            this.selected.add(hit.item.path);
            this.updateSelectionUI();
          }
          hit.el.classList.add("is-touch-armed");
        }
        if (navigator.vibrate) navigator.vibrate(30);
      }, 500);
    }, { passive: true });
    this.layer.addEventListener("touchmove", (e) => {
      fmMarkTouchInput();
      if (touch.mode === "idle" || e.touches.length !== 1) return;
      const t = e.touches[0];
      touch.lastX = t.clientX;
      touch.lastY = t.clientY;
      const dist = Math.hypot(t.clientX - touch.startX, t.clientY - touch.startY);
      if (touch.mode === "pending_hold") {
        if (dist > 10) {
          clearTimeout(touch.timer);
          touch.timer = null;
          touch.mode = "scrolling";
        }
        return;
      }
      if (touch.mode === "scrolling") return;
      e.preventDefault();
      if (touch.mode === "armed" && touch.hit && dist > 24) {
        touch.mode = "dragging";
        touch.hit.el.classList.remove("is-touch-armed");
        touch.keys = touch.hit.isHome ? ["home"] : [...this.selected];
        this.dragMove = { clientX: touch.startX, clientY: touch.startY, origins: {} };
        touch.keys.forEach((key) => {
          this.dragMove.origins[key] = this.iconOrigin(key);
        });
        const name = touch.hit.isHome ? this.homeName : touch.hit.item.name;
        touch.ghost = fmTouchGhost(name, touch.keys.length);
      }
      if (touch.mode === "dragging") {
        fmTouchMoveGhost(touch.ghost, t.clientX, t.clientY);
        const r = resolveDrop(t.clientX, t.clientY);
        fmTouchHighlight(r.canTransfer ? r.drop : null);
      }
    }, { passive: false });
    this.layer.addEventListener("touchend", (e) => {
      fmMarkTouchInput();
      const mode = touch.mode;
      const hit = touch.hit;
      const x = touch.lastX;
      const y = touch.lastY;
      if (mode === "idle") return;
      if (mode === "scrolling") {
        resetTouch(true);
        return;
      }
      e.preventDefault();
      if (mode === "pending_hold") {
        const now = Date.now();
        if (hit && touch.lastTapKey === tapKey(hit) && now - touch.lastTapTime < 350) {
          resetTouch(false);
          this.openIcon(hit).catch((err) => toast(err.message || String(err)));
          return;
        }
        this.selected.clear();
        if (hit && !hit.isHome) this.selected.add(hit.item.path);
        this.updateSelectionUI();
        if (hit && hit.isHome) hit.el.classList.add("selected");
        resetTouch(false);
        if (hit) {
          touch.lastTapKey = tapKey(hit);
          touch.lastTapTime = now;
        }
        return;
      }
      if (mode === "armed") {
        if (!hit) {
          this.selected.clear();
          this.updateSelectionUI();
        }
        resetTouch(false);
        this.showMenu(x, y, !!hit && !hit.isHome);
        return;
      }
      const keys = touch.keys;
      const r = resolveDrop(x, y);
      resetTouch(false);
      if (r.canTransfer) {
        this.dragMove = null;
        fmTransferPaths(r.paths, r.drop.path, false).catch((err) => toast(err.message || String(err)));
        return;
      }
      if (r.drop.el && this.layer.contains(r.drop.el) && !r.drop.el.closest(".win, #taskbar")) {
        this.moveIcons(keys, { clientX: x, clientY: y });
        return;
      }
      this.dragMove = null;
    }, { passive: false });
    this.layer.addEventListener("touchcancel", () => {
      fmMarkTouchInput();
      this.dragMove = null;
      resetTouch(false);
    }, { passive: true });
  },
};
