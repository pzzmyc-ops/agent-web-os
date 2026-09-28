const TransferManager = {
  winId: "transferManager",
  icon: "/images/transfer.svg",
  chunkSize: 2 * 1024 * 1024,
  threads: 2,
  chunkThreads: 3,
  running: 0,
  uploadQueue: [],
  get destPath() {
    return this._destPath || window.FMEnv.workspace;
  },
  set destPath(v) {
    this._destPath = v;
  },
  _destPath: "",
  fileInput: null,
  folderInput: null,
  unsub: null,
  hubUnsub: null,
  onComplete: null,
  pendingDownloads: new Set(),
  startedDownloads: new Set(),
  downloadFrames: new Map(),
  uploadFiles: new Map(),
  activeXhrs: new Map(),
  uploadCompleteDests: new Set(),
  busyActions: new Set(),
  listStructureKey: "",
  pendingUploads: [],
  createBatch: 8,
  _pumpLock: false,
  itemHeight: 50,

  ensureDom() {
    Loader.loadCss("/css/transfer.css?v=12");
    if (!this.fileInput) {
      this.fileInput = document.createElement("input");
      this.fileInput.type = "file";
      this.fileInput.multiple = true;
      this.fileInput.hidden = true;
      document.body.appendChild(this.fileInput);
      this.fileInput.onchange = () => {
        this.enqueueUploads([...this.fileInput.files], this.destPath).catch((err) => {
          toast(err.message || String(err));
        });
        this.fileInput.value = "";
      };
    }
    if (!this.folderInput) {
      this.folderInput = document.createElement("input");
      this.folderInput.type = "file";
      this.folderInput.multiple = true;
      this.folderInput.setAttribute("webkitdirectory", "");
      this.folderInput.hidden = true;
      document.body.appendChild(this.folderInput);
      this.folderInput.onchange = () => {
        this.enqueueUploads([...this.folderInput.files], this.destPath).catch((err) => {
          toast(err.message || String(err));
        });
        this.folderInput.value = "";
      };
    }
    if (!this.hubUnsub) {
      this.hubUnsub = TransferHub.on((tasks) => this.onHubTasks(tasks));
    }
  },

  abortDownloadFrame(taskId) {
    const frame = this.downloadFrames.get(taskId);
    if (!frame) return;
    frame.src = "about:blank";
    frame.remove();
    this.downloadFrames.delete(taskId);
  },

  downloadFile(url, taskId) {
    this.abortDownloadFrame(taskId);
    const frame = document.createElement("iframe");
    frame.style.display = "none";
    frame.src = url;
    document.body.appendChild(frame);
    this.downloadFrames.set(taskId, frame);
  },

  onHubTasks(tasks) {
    (tasks || []).forEach((task) => {
      if (task.kind !== "download") return;
      if (!this.pendingDownloads.has(task.id)) return;
      if (task.status === "ready" && !this.startedDownloads.has(task.id)) {
        this.startedDownloads.add(task.id);
        this.downloadFile(`/api/transfer/download/${encodeURIComponent(task.id)}`, task.id);
        return;
      }
      if (task.status === "success" || task.status === "error" || task.status === "cancelled") {
        this.pendingDownloads.delete(task.id);
        this.startedDownloads.delete(task.id);
        this.abortDownloadFrame(task.id);
      }
    });
  },

  open(destPath) {
    this.ensureDom();
    if (destPath) this.destPath = destPath;
    TransferHub.connect();
    const exist = WM.windows.get(this.winId);
    if (exist) {
      WM.focus(this.winId);
      if (exist.minimized) WM.restore(this.winId);
      this.render(TransferHub.tasks);
      return exist;
    }
    const root = document.createElement("div");
    root.className = "transfer-manager";
    root.innerHTML = `
      <div class="tm-toolbar">
        <div class="tm-left">
          <button type="button" class="tm-btn primary" data-act="file">上传文件</button>
          <button type="button" class="tm-btn primary" data-act="folder">上传文件夹</button>
        </div>
        <div class="tm-right">
          <button type="button" class="tm-btn" data-act="clear">清空已完成</button>
        </div>
      </div>
      <div class="tm-tabs">
        <button type="button" class="tm-tab active" data-tab="all">全部</button>
        <button type="button" class="tm-tab" data-tab="upload">上传</button>
        <button type="button" class="tm-tab" data-tab="download">下载</button>
      </div>
      <div class="tm-drop">
        <div class="tm-list"></div>
      </div>
    `;
    const win = WM.create({
      id: this.winId,
      title: "传输管理器",
      icon: this.icon,
      width: 640,
      height: 460,
      className: "transfer-manager-dialog",
      titleHtml: `<span class="path-ico" style="background-image:url('${this.icon}')"></span><span>传输管理器</span>`,
      content: root,
    });
    this.root = root;
    this.listEl = root.querySelector(".tm-list");
    this.dropEl = root.querySelector(".tm-drop");
    this.tab = "all";
    this.bindListActions();
    root.querySelector('[data-act="file"]').onclick = () => this.fileInput.click();
    root.querySelector('[data-act="folder"]').onclick = () => this.folderInput.click();
    root.querySelector('[data-act="clear"]').onclick = () => {
      API.post("/api/transfer/clear", {}).catch((err) => toast(err.message || String(err)));
    };
    root.querySelectorAll(".tm-tab").forEach((btn) => {
      btn.onclick = () => {
        this.tab = btn.dataset.tab;
        root.querySelectorAll(".tm-tab").forEach((b) => b.classList.toggle("active", b === btn));
        this.render(TransferHub.tasks);
      };
    });
    this.dropEl.addEventListener("dragover", (e) => {
      e.preventDefault();
      this.dropEl.classList.add("dragover");
    });
    this.dropEl.addEventListener("dragleave", () => this.dropEl.classList.remove("dragover"));
    this.dropEl.addEventListener("drop", (e) => {
      e.preventDefault();
      this.dropEl.classList.remove("dragover");
      this.addFromDataTransfer(e.dataTransfer, this.destPath).catch((err) => {
        toast(err.message || String(err));
      });
    });
    this.dropEl.addEventListener("scroll", () => {
      this.render(TransferHub.tasks);
    }, { passive: true });
    if (this.unsub) this.unsub();
    this.unsub = TransferHub.on((tasks) => this.render(tasks));
    win.onClose = () => {
      if (this.unsub) this.unsub();
      this.unsub = null;
      this.root = null;
      this.listEl = null;
    };
    this.render(TransferHub.tasks);
    return win;
  },

  stateText(task) {
    if (task.status === "waiting") return "等待中";
    if (task.status === "packing") {
      if (task.size > 0) return `打包中 ${task.transferred || 0}/${task.size}`;
      return `打包中 ${Math.round((task.progress || 0) * 100)}%`;
    }
    if (task.kind === "download" && (task.status === "ready" || task.status === "running")) {
      return "已交给浏览器下载，进度请在浏览器下载管理器查看";
    }
    if (task.status === "ready") return "准备下载";
    if (task.status === "running") {
      const pct = `${Math.round((task.progress || 0) * 100)}%`;
      const speed = formatSpeed(task.speed);
      return speed ? `${pct} ${speed}` : pct;
    }
    if (task.status === "success") return "已完成";
    if (task.status === "cancelled" || task.status === "error") return task.error || "下载失败";
    return task.status;
  },

  async cancelTask(taskId) {
    const set = this.activeXhrs.get(taskId);
    if (set) {
      [...set].forEach((xhr) => xhr.abort());
      this.activeXhrs.delete(taskId);
    }
    this.pendingDownloads.delete(taskId);
    this.startedDownloads.delete(taskId);
    this.abortDownloadFrame(taskId);
    await API.post("/api/transfer/cancel", { id: taskId });
    await TransferHub.refresh();
  },

  bindListActions() {
    if (!this.listEl || this.listEl.dataset.bound === "1") return;
    this.listEl.dataset.bound = "1";
    this.listEl.addEventListener("pointerdown", (e) => {
      if (e.button != null && e.button !== 0) return;
      const cancelBtn = e.target.closest("[data-cancel]");
      if (cancelBtn) {
        e.preventDefault();
        e.stopPropagation();
        const id = cancelBtn.dataset.cancel;
        if (this.busyActions.has(`c:${id}`)) return;
        this.busyActions.add(`c:${id}`);
        cancelBtn.disabled = true;
        this.cancelTask(id)
          .catch((err) => toast(err.message || String(err)))
          .finally(() => this.busyActions.delete(`c:${id}`));
        return;
      }
      const retryBtn = e.target.closest("[data-retry]");
      if (retryBtn) {
        e.preventDefault();
        e.stopPropagation();
        const id = retryBtn.dataset.retry;
        if (this.busyActions.has(`r:${id}`)) return;
        this.busyActions.add(`r:${id}`);
        retryBtn.disabled = true;
        this.retry(id)
          .catch((err) => toast(err.message || String(err)))
          .finally(() => this.busyActions.delete(`r:${id}`));
      }
    });
  },

  taskSizeText(task) {
    if (task.status === "packing") return `${task.transferred || 0}/${task.size || 0} 个文件`;
    if (task.status === "running" && task.kind !== "download" && task.transferred != null) {
      return `${formatSize(task.transferred || 0)}/${formatSize(task.size || 0)}`;
    }
    return formatSize(task.size || 0);
  },

  showsProgress(task) {
    if (task.kind !== "download") return true;
    return !["ready", "running", "success"].includes(task.status);
  },

  speedText(task) {
    if (task.status !== "running" || task.kind === "download") return "";
    return formatSpeed(task.speed);
  },

  patchItem(el, task) {
    el.className = `tm-item ${task.status}`;
    const sizeEl = el.querySelector(".tm-size");
    const speedEl = el.querySelector(".tm-speed");
    const stateEl = el.querySelector(".tm-state");
    const progressEl = el.querySelector(".tm-progress");
    const bar = el.querySelector(".tm-progress > i");
    const info = el.querySelector(".tm-info");
    const showBar = this.showsProgress(task);
    if (sizeEl) sizeEl.textContent = this.taskSizeText(task);
    if (speedEl) speedEl.textContent = this.speedText(task);
    if (stateEl) stateEl.textContent = this.stateText(task);
    if (progressEl) progressEl.style.display = showBar ? "" : "none";
    if (bar) bar.style.width = showBar ? `${Math.round((task.progress || 0) * 100)}%` : "0%";
    const canCancel = ["waiting", "running", "packing", "ready"].includes(task.status);
    const canRetry = task.status === "error" || task.status === "cancelled";
    let cancelBtn = el.querySelector(".tm-cancel");
    let retryBtn = el.querySelector(".tm-retry");
    if (canRetry && !retryBtn && info) {
      info.insertAdjacentHTML("beforeend", `<button type="button" class="tm-retry" data-retry="${task.id}" title="重试"><i class="font-icon ri-refresh-line"></i></button>`);
    }
    if (!canRetry && retryBtn) retryBtn.remove();
    if (canCancel && !cancelBtn && info) {
      info.insertAdjacentHTML("beforeend", `<button type="button" class="tm-cancel" data-cancel="${task.id}" title="取消"><i class="font-icon ri-close-line"></i></button>`);
    }
    if (!canCancel && cancelBtn) cancelBtn.remove();
  },

  itemHtml(task) {
    const icon = fileIcon({
      type: "file",
      ext: (task.name.split(".").pop() || "").toLowerCase(),
      name: task.name,
    });
    const title = task.relativePath || task.path || task.name;
    const canCancel = ["waiting", "running", "packing", "ready"].includes(task.status);
    const canRetry = task.status === "error" || task.status === "cancelled";
    const speedText = this.speedText(task);
    const showBar = this.showsProgress(task);
    return `
      <div class="tm-item ${task.status}" data-id="${task.id}">
        <div class="tm-info">
          <span class="tm-kind">${task.kind === "upload" ? "上传" : "下载"}</span>
          <span class="fico" style="background-image:url('${icon}')"></span>
          <span class="tm-name" title="${escapeHtml(title)}">${escapeHtml(task.name)}</span>
          <span class="tm-client">${escapeHtml(task.client || "")}</span>
          <span class="tm-size">${escapeHtml(this.taskSizeText(task))}</span>
          <span class="tm-speed">${escapeHtml(speedText)}</span>
          <span class="tm-state">${escapeHtml(this.stateText(task))}</span>
          ${canRetry ? `<button type="button" class="tm-retry" data-retry="${task.id}" title="重试"><i class="font-icon ri-refresh-line"></i></button>` : ""}
          ${canCancel ? `<button type="button" class="tm-cancel" data-cancel="${task.id}" title="取消"><i class="font-icon ri-close-line"></i></button>` : ""}
        </div>
        <div class="tm-progress"${showBar ? "" : ' style="display:none"'}><i style="width:${showBar ? Math.round((task.progress || 0) * 100) : 0}%"></i></div>
      </div>
    `;
  },

  filteredList(tasks) {
    let list = tasks.slice().reverse();
    if (this.tab === "upload") list = list.filter((t) => t.kind === "upload");
    if (this.tab === "download") list = list.filter((t) => t.kind === "download");
    return list;
  },

  setTitle(tasks) {
    const active = tasks.filter((t) => ["running", "waiting", "packing"].includes(t.status)).length;
    const extra = this.pendingUploads.length;
    const n = active + extra;
    WM.setTitle(this.winId, n ? `传输管理器 (${n})` : "传输管理器");
  },

  render(tasks) {
    if (!this.listEl) return;
    const list = this.filteredList(tasks);
    this.dropEl.classList.toggle("has-items", list.length > 0);
    const itemH = this.itemHeight;
    const scrollTop = this.dropEl.scrollTop;
    const viewH = this.dropEl.clientHeight || 360;
    const start = Math.max(0, Math.floor(scrollTop / itemH) - 4);
    const end = Math.min(list.length, Math.ceil((scrollTop + viewH) / itemH) + 4);
    const vis = list.slice(start, end);
    const structureKey = `${this.tab}:${start}:${end}:${list.length}:` + vis.map((t) => t.id).join("|");
    if (structureKey && structureKey === this.listStructureKey) {
      const nodes = this.listEl.querySelectorAll(".tm-item");
      if (nodes.length === vis.length) {
        let ok = true;
        for (let i = 0; i < vis.length; i += 1) {
          if (nodes[i].dataset.id !== vis[i].id) {
            ok = false;
            break;
          }
          this.patchItem(nodes[i], vis[i]);
        }
        if (ok) {
          this.setTitle(tasks);
          return;
        }
      }
    }
    this.listStructureKey = structureKey;
    const top = start * itemH;
    const bottom = Math.max(0, (list.length - end) * itemH);
    this.listEl.innerHTML =
      `<div class="tm-spacer" style="height:${top}px"></div>`
      + vis.map((task) => this.itemHtml(task)).join("")
      + `<div class="tm-spacer" style="height:${bottom}px"></div>`;
    this.setTitle(tasks);
  },

  fileKeyOf(item) {
    return `${item.destPath}|${item.relativePath}|${item.size}|${item.name}`;
  },

  async enqueueUploads(fileList, destPath) {
    this.open(destPath || this.destPath);
    const items = [...fileList].map((entry) => {
      if (entry && entry.file) {
        return {
          file: entry.file,
          relativePath: entry.relativePath || entry.file.webkitRelativePath || entry.file.name,
          name: entry.file.name,
          size: entry.file.size,
          destPath: destPath || this.destPath,
        };
      }
      return {
        file: entry,
        relativePath: entry.webkitRelativePath || entry.name,
        name: entry.name,
        size: entry.size,
        destPath: destPath || this.destPath,
      };
    });
    if (!items.length) throw new Error("未选择文件");
    items.forEach((it) => this.uploadCompleteDests.add(it.destPath));
    this.pendingUploads.push(...items);
    this.setTitle(TransferHub.tasks);
    this.pumpUploads();
  },

  async fillQueue() {
    const inflight = this.uploadQueue.length + this.running;
    const need = Math.max(0, this.createBatch - inflight);
    if (need <= 0 || !this.pendingUploads.length) return;
    const batch = this.pendingUploads.slice(0, need);
    const created = await API.post("/api/transfer/uploads", {
      items: batch.map((it) => ({
        destPath: it.destPath,
        relativePath: it.relativePath,
        name: it.name,
        size: it.size,
        fileKey: this.fileKeyOf(it),
      })),
    });
    this.pendingUploads.splice(0, batch.length);
    const tasks = created.data || [];
    for (let i = 0; i < tasks.length; i += 1) {
      this.uploadFiles.set(tasks[i].id, batch[i].file);
      this.uploadQueue.push({
        taskId: tasks[i].id,
        file: batch[i].file,
        doneChunks: tasks[i].doneChunks || [],
        chunks: tasks[i].chunks,
        chunkSize: tasks[i].chunkSize || this.chunkSize,
      });
    }
  },

  async retry(taskId) {
    const task = TransferHub.tasks.find((t) => t.id === taskId);
    if (!task) throw new Error("任务不存在");
    const res = await API.post("/api/transfer/retry", { id: taskId });
    if (task.kind === "download") {
      this.startedDownloads.delete(taskId);
      this.abortDownloadFrame(taskId);
      this.pendingDownloads.add(taskId);
      await TransferHub.refresh();
      this.onHubTasks(TransferHub.tasks);
      return;
    }
    if (task.kind === "upload") {
      const file = this.uploadFiles.get(taskId);
      if (!file) throw new Error("本地文件已失效，请重新选择上传");
      this.uploadQueue.push({
        taskId,
        file,
        doneChunks: (res.data && res.data.doneChunks) || [],
        chunks: task.chunks,
        chunkSize: task.chunkSize || this.chunkSize,
      });
      this.pumpUploads();
    }
  },

  maybeComplete() {
    const busy = this.running > 0 || this.uploadQueue.length > 0 || this.pendingUploads.length > 0;
    const active = TransferHub.tasks.some((t) => t.kind === "upload" && ["waiting", "running"].includes(t.status));
    if (!busy && !active && typeof this.onComplete === "function") {
      const dests = [...this.uploadCompleteDests];
      this.uploadCompleteDests.clear();
      this.onComplete(dests);
    }
  },

  pumpUploads() {
    if (this._pumpLock) return;
    this._pumpLock = true;
    this._pumpLoop().finally(() => {
      this._pumpLock = false;
      if (this.running < this.threads && (this.uploadQueue.length || this.pendingUploads.length)) {
        this.pumpUploads();
      } else {
        this.maybeComplete();
      }
    });
  },

  async _pumpLoop() {
    while (this.running < this.threads) {
      if (!this.uploadQueue.length && this.pendingUploads.length) {
        await this.fillQueue();
      }
      const next = this.uploadQueue.shift();
      if (!next) break;
      this.running += 1;
      this.sendUploadChunked(next).finally(() => {
        this.running = Math.max(0, this.running - 1);
        const cur = TransferHub.tasks.find((t) => t.id === next.taskId);
        if (!cur || cur.status === "success") this.uploadFiles.delete(next.taskId);
        this.pumpUploads();
      });
    }
  },

  async sendUploadChunked(job) {
    const check = await API.get(`/api/transfer/upload/${encodeURIComponent(job.taskId)}/check`);
    const info = check.data;
    const chunks = info.chunks || 1;
    const chunkSize = info.chunkSize || this.chunkSize;
    const done = new Set(info.doneChunks || job.doneChunks || []);
    const pending = [];
    for (let i = 0; i < chunks; i += 1) {
      if (!done.has(i)) pending.push(i);
    }
    if (!pending.length) return;
    let cursor = 0;
    const workers = [];
    const run = async () => {
      while (cursor < pending.length) {
        const cur = TransferHub.tasks.find((t) => t.id === job.taskId);
        if (cur && (cur.status === "cancelled" || cur.status === "error")) return;
        const index = pending[cursor];
        cursor += 1;
        await this.sendOneChunk(job.taskId, job.file, index, chunks, chunkSize);
      }
    };
    const n = Math.min(this.chunkThreads, pending.length);
    for (let i = 0; i < n; i += 1) workers.push(run());
    await Promise.all(workers);
  },

  sendOneChunk(taskId, file, chunk, chunks, chunkSize) {
    return new Promise((resolve, reject) => {
      const start = chunk * chunkSize;
      const end = Math.min(file.size, start + chunkSize);
      const blob = file.slice(start, end);
      const fd = new FormData();
      fd.append("chunk", String(chunk));
      fd.append("chunks", String(chunks));
      fd.append("chunkSize", String(chunkSize));
      fd.append("file", blob, file.name);
      const xhr = new XMLHttpRequest();
      if (!this.activeXhrs.has(taskId)) this.activeXhrs.set(taskId, new Set());
      this.activeXhrs.get(taskId).add(xhr);
      const done = () => {
        const set = this.activeXhrs.get(taskId);
        if (set) {
          set.delete(xhr);
          if (!set.size) this.activeXhrs.delete(taskId);
        }
      };
      xhr.open("POST", `/api/transfer/upload/${encodeURIComponent(taskId)}/chunk`);
      xhr.onload = () => {
        done();
        let data = null;
        try {
          data = JSON.parse(xhr.responseText);
        } catch (err) {
          reject(new Error("响应解析失败"));
          return;
        }
        if (xhr.status >= 200 && xhr.status < 300 && data.code) resolve(data);
        else reject(new Error((data && data.data) || `HTTP ${xhr.status}`));
      };
      xhr.onerror = () => {
        done();
        reject(new Error("网络错误"));
      };
      xhr.onabort = () => {
        done();
        resolve({ aborted: true });
      };
      xhr.send(fd);
    });
  },

  readDirEntries(dirEntry) {
    const reader = dirEntry.createReader();
    return new Promise((resolve, reject) => {
      const all = [];
      const pump = () => {
        reader.readEntries((batch) => {
          if (!batch.length) {
            resolve(all);
            return;
          }
          all.push(...batch);
          pump();
        }, reject);
      };
      pump();
    });
  },

  walkEntry(entry, prefix) {
    const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isFile) {
      return new Promise((resolve, reject) => {
        entry.file((file) => {
          resolve({ files: [{ file, relativePath: rel }], dirs: [] });
        }, reject);
      });
    }
    if (!entry.isDirectory) throw new Error(`不支持的拖入项: ${entry.name}`);
    return this.readDirEntries(entry).then(async (children) => {
      if (!children.length) return { files: [], dirs: [rel] };
      const files = [];
      const dirs = [];
      for (let i = 0; i < children.length; i += 1) {
        const part = await this.walkEntry(children[i], rel);
        files.push(...part.files);
        dirs.push(...part.dirs);
      }
      return { files, dirs };
    });
  },

  async ensureRelDirs(destPath, relDirs) {
    const unique = [...new Set(relDirs.map((d) => d.replace(/\\/g, "/").replace(/^\/+|\/+$/g, "")).filter(Boolean))];
    unique.sort((a, b) => a.split("/").length - b.split("/").length);
    for (let i = 0; i < unique.length; i += 1) {
      const parts = unique[i].split("/").filter(Boolean);
      let cur = destPath || this.destPath;
      for (let j = 0; j < parts.length; j += 1) {
        const name = parts[j];
        try {
          await API.mkdir(cur, name);
        } catch (err) {
          const msg = err.message || String(err);
          if (!msg.includes("FileExistsError")) throw err;
        }
        cur = joinPath(cur, name);
      }
    }
  },

  async addFromDataTransfer(dataTransfer, destPath) {
    this.open(destPath || this.destPath);
    const items = dataTransfer.items ? [...dataTransfer.items] : [];
    const entries = [];
    for (let i = 0; i < items.length; i += 1) {
      const item = items[i];
      if (item.kind !== "file") continue;
      if (typeof item.webkitGetAsEntry !== "function") continue;
      const entry = item.webkitGetAsEntry();
      if (entry) entries.push(entry);
    }
    if (!entries.length) {
      const list = [...(dataTransfer.files || [])];
      if (!list.length) throw new Error("未检测到可上传的文件或文件夹");
      await this.enqueueUploads(list, destPath || this.destPath);
      return;
    }
    const files = [];
    const dirs = [];
    for (let i = 0; i < entries.length; i += 1) {
      const part = await this.walkEntry(entries[i], "");
      files.push(...part.files);
      dirs.push(...part.dirs);
    }
    if (!files.length && !dirs.length) throw new Error("未能读取拖入的文件夹内容");
    const dest = destPath || this.destPath;
    if (dirs.length) {
      await this.ensureRelDirs(dest, dirs);
      this.uploadCompleteDests.add(dest);
    }
    if (files.length) {
      await this.enqueueUploads(files, dest);
    } else if (typeof this.onComplete === "function") {
      const dests = [...this.uploadCompleteDests];
      this.uploadCompleteDests.clear();
      this.onComplete(dests.length ? dests : [dest]);
    }
  },

  async download(items) {
    this.open();
    const list = (items || []).filter(Boolean);
    if (!list.length) throw new Error("请选择要下载的项目");
    const paths = list.map((i) => i.path);
    const res = await API.post("/api/transfer/downloads", { paths });
    const { id } = res.data;
    this.startedDownloads.delete(id);
    this.pendingDownloads.add(id);
    await TransferHub.refresh();
    this.onHubTasks(TransferHub.tasks);
    return id;
  },
};

kodApp.add({
  name: "transferManager",
  title: "传输管理器",
  sort: 5,
  menu: true,
  singleton: true,
  ext: [],
  icon: "/images/transfer.svg",
  async open() {
    return TransferManager.open("/");
  },
});
