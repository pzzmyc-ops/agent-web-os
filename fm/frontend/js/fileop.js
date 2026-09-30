function escapeFileOp(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatCount(value) {
  return String(Math.max(0, Math.round(value || 0))).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

function formatEta(seconds) {
  const whole = Math.max(1, Math.round(seconds));
  if (whole < 60) return "大约 " + whole + " 秒";
  const minutes = Math.round(whole / 60);
  if (minutes < 60) return "大约 " + minutes + " 分钟";
  const hours = Math.round(minutes / 60);
  if (hours < 48) return "大约 " + hours + " 小时";
  return "大约 " + Math.round(hours / 24) + " 天";
}

function fileOpTitle(task) {
  if (task.phase === "scan") {
    if (task.op === "delete") return "正在计算要删除的项目";
    if (task.op === "move") return "正在计算要移动的项目";
    return "正在计算要复制的项目";
  }
  const count = formatCount(task.totalFiles);
  if (task.op === "delete") return "正在删除 " + count + " 个项目";
  const verb = task.op === "move" ? "移动" : "复制";
  return "正在将 " + count + " 个项目从 <span class=\"fop-place\">"
    + escapeFileOp(task.srcLabel) + "</span> " + verb + "到 <span class=\"fop-place\">"
    + escapeFileOp(task.destLabel) + "</span>";
}

function fileOpSpeed(task) {
  if (task.status === "paused") return "速度: 已暂停";
  if (task.status === "error") return "";
  if (task.phase !== "run" || !(task.speed > 0)) return "速度: 正在计算";
  return "速度: " + formatSize(task.speed) + "/秒";
}

function fileOpEta(task) {
  if (task.status === "paused") return "已暂停";
  if (task.status === "error") return "";
  const leftBytes = Math.max(0, (task.totalBytes || 0) - (task.doneBytes || 0));
  const leftFiles = Math.max(0, (task.totalFiles || 0) - (task.doneFiles || 0));
  if (task.phase === "scan" || !(task.speed > 0)) return "正在计算...";
  if (leftBytes <= 0 && leftFiles <= 0) return "即将完成";
  const basis = leftBytes > 0 ? leftBytes : task.speed;
  return formatEta(basis / task.speed);
}

function layoutFileOps() {
  const nodes = [...document.querySelectorAll(".fop-dialog")].filter((node) => node.dataset.moved !== "1");
  nodes.forEach((node, index) => {
    node.style.transform = "translate(-50%, -50%)";
    node.style.left = "calc(50% + " + (index * 18) + "px)";
    node.style.top = "calc(40% + " + (index * 26) + "px)";
  });
}

const FileOp = {
  run(op, paths, dest) {
    const list = paths || [];
    const target = dest || "";
    return new Promise((resolve, reject) => {
      API.post("/api/fileop/start", { op, paths: list, dest: target }).then((res) => {
        this.mount(res.data, list, target, resolve, reject);
      }).catch(reject);
    });
  },

  mount(task, paths, dest, resolve, reject) {
    const root = document.createElement("div");
    root.className = "fop-dialog";
    root.innerHTML = [
      '<div class="fop-title"></div>',
      '<div class="fop-head">',
      '<span class="fop-percent"></span>',
      '<span class="fop-actions">',
      '<button type="button" class="fop-pause" data-act="pause" title="暂停"><i></i><i></i></button>',
      '<button type="button" class="fop-close" data-act="close" title="取消">×</button>',
      "</span>",
      "</div>",
      '<div class="fop-bar-row"><div class="fop-track"><i></i></div><span class="fop-speed"></span></div>',
      '<div class="fop-detail">',
      '<div><span class="k" data-k="name">名称:</span> <span data-v="name"></span></div>',
      '<div data-row="eta"><span class="k">剩余时间:</span> <span data-v="eta"></span></div>',
      '<div data-row="left"><span class="k" data-k="left">剩余项目:</span> <span data-v="left"></span></div>',
      "</div>",
      '<button type="button" class="fop-more" data-act="brief">简略信息</button>',
    ].join("");
    document.body.appendChild(root);
    layoutFileOps();
    let current = task;
    let pollTimer = 0;
    let closeTimer = 0;
    let settled = false;
    let closed = false;
    const titleEl = root.querySelector(".fop-title");
    const percentEl = root.querySelector(".fop-percent");
    const barEl = root.querySelector(".fop-track > i");
    const speedEl = root.querySelector(".fop-speed");
    const nameKey = root.querySelector('[data-k="name"]');
    const nameEl = root.querySelector('[data-v="name"]');
    const etaRow = root.querySelector('[data-row="eta"]');
    const etaEl = root.querySelector('[data-v="eta"]');
    const leftKey = root.querySelector('[data-k="left"]');
    const leftEl = root.querySelector('[data-v="left"]');
    const pauseBtn = root.querySelector('[data-act="pause"]');
    const closeBtn = root.querySelector('[data-act="close"]');
    const briefBtn = root.querySelector('[data-act="brief"]');
    let brief = false;

    function settle(fn) {
      if (settled) return;
      settled = true;
      fn();
    }

    function close() {
      if (closed) return;
      closed = true;
      if (pollTimer) clearTimeout(pollTimer);
      if (closeTimer) clearTimeout(closeTimer);
      root.remove();
      layoutFileOps();
    }

    function paint(next) {
      current = next;
      root.classList.toggle("is-error", next.status === "error");
      titleEl.innerHTML = fileOpTitle(next);
      const pct = Math.max(0, Math.min(100, Math.floor((next.percent || 0) * 100)));
      percentEl.textContent = "已完成 " + pct + "%";
      barEl.style.width = pct + "%";
      speedEl.textContent = fileOpSpeed(next);
      const failed = next.status === "error";
      nameKey.textContent = failed ? "错误:" : "名称:";
      nameEl.textContent = failed ? (next.error || "失败") : (next.currentName || "");
      etaRow.hidden = failed;
      root.querySelector('[data-row="left"]').hidden = failed;
      if (!failed) {
        etaEl.textContent = fileOpEta(next);
        if (next.phase === "scan") {
          leftKey.textContent = "已发现:";
          leftEl.textContent = formatCount(next.totalFiles) + " (" + formatSize(next.totalBytes) + ")";
        } else {
          const leftFiles = Math.max(0, (next.totalFiles || 0) - (next.doneFiles || 0));
          const leftBytes = Math.max(0, (next.totalBytes || 0) - (next.doneBytes || 0));
          leftKey.textContent = "剩余项目:";
          leftEl.textContent = formatCount(leftFiles) + " (" + formatSize(leftBytes) + ")";
        }
      }
      const alive = next.status === "scanning" || next.status === "running" || next.status === "paused";
      pauseBtn.hidden = !alive;
      pauseBtn.classList.toggle("resume", next.status === "paused");
      pauseBtn.title = next.status === "paused" ? "继续" : "暂停";
      closeBtn.title = alive ? "取消" : "关闭";
    }

    function refresh() {
      const dirs = paths.map((path) => parentPath(path));
      if (dest) dirs.push(dest);
      return FMNotifyFsChanged(dirs);
    }

    function finish(next) {
      paint(next);
      refresh().then(() => {
        if (next.status === "success") {
          closeTimer = setTimeout(() => {
            close();
            settle(() => resolve(next));
          }, 350);
          return;
        }
        if (next.status === "cancelled") {
          close();
          settle(() => reject(new Error("已取消")));
          return;
        }
        settle(() => reject(new Error(next.error || "失败")));
      }).catch((err) => {
        settle(() => reject(err));
      });
    }

    function poll() {
      if (closed || settled) return;
      API.get("/api/fileop/status?id=" + encodeURIComponent(current.id)).then((res) => {
        if (closed) return;
        const next = res.data;
        if (next.status === "success" || next.status === "cancelled" || next.status === "error") {
          finish(next);
          return;
        }
        paint(next);
        pollTimer = setTimeout(poll, 200);
      }).catch((err) => {
        paint({
          ...current,
          status: "error",
          error: err.message || String(err),
        });
        settle(() => reject(err));
      });
    }

    pauseBtn.onclick = () => {
      if (pauseBtn.hidden || pauseBtn.disabled) return;
      const action = current.status === "paused" ? "resume" : "pause";
      pauseBtn.disabled = true;
      API.post("/api/fileop/" + action, { id: current.id }).then((res) => {
        paint(res.data);
      }).catch((err) => {
        toast(err.message || String(err));
      }).finally(() => {
        pauseBtn.disabled = false;
      });
    };
    closeBtn.onclick = () => {
      if (current.status === "success") {
        close();
        settle(() => resolve(current));
        return;
      }
      if (current.status === "error" || current.status === "cancelled") {
        close();
        return;
      }
      closeBtn.disabled = true;
      API.post("/api/fileop/cancel", { id: current.id }).catch((err) => {
        closeBtn.disabled = false;
        toast(err.message || String(err));
      });
    };
    briefBtn.onclick = () => {
      brief = !brief;
      root.classList.toggle("brief", brief);
      briefBtn.textContent = brief ? "详细信息" : "简略信息";
    };
    titleEl.addEventListener("pointerdown", (e) => {
      if (e.button != null && e.button !== 0) return;
      const rect = root.getBoundingClientRect();
      const ox = e.clientX - rect.left;
      const oy = e.clientY - rect.top;
      root.dataset.moved = "1";
      root.style.transform = "none";
      root.style.left = rect.left + "px";
      root.style.top = rect.top + "px";
      titleEl.setPointerCapture(e.pointerId);
      function move(ev) {
        if (ev.pointerId !== e.pointerId) return;
        let left = ev.clientX - ox;
        let top = ev.clientY - oy;
        const maxL = Math.max(0, window.innerWidth - root.offsetWidth);
        const maxT = Math.max(0, window.innerHeight - root.offsetHeight);
        if (left < 0) left = 0;
        if (top < 0) top = 0;
        if (left > maxL) left = maxL;
        if (top > maxT) top = maxT;
        root.style.left = left + "px";
        root.style.top = top + "px";
      }
      function up(ev) {
        if (ev.pointerId !== e.pointerId) return;
        titleEl.removeEventListener("pointermove", move);
        titleEl.removeEventListener("pointerup", up);
      }
      titleEl.addEventListener("pointermove", move);
      titleEl.addEventListener("pointerup", up);
    });
    paint(task);
    pollTimer = setTimeout(poll, 200);
  },
};

window.FileOp = FileOp;
