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

function formatSpeedValue(value) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let index = 0;
  let size = value;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  if (index === 0) return Math.round(size) + " B";
  return size.toFixed(1) + " " + units[index];
}

function formatEta(seconds) {
  const whole = Math.max(1, Math.round(seconds));
  if (whole < 60) return "大约 " + whole + " 秒";
  const minutes = Math.round(whole / 60);
  if (minutes < 60) return "大约 " + minutes + " 分钟";
  const hours = Math.floor(minutes / 60);
  const rest = minutes - hours * 60;
  if (hours >= 48 && !rest) return "大约 " + Math.round(hours / 24) + " 天";
  if (!rest) return "大约 " + hours + " 小时";
  return "大约 " + hours + " 小时 " + rest + " 分钟";
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

function fileOpFraction(task) {
  if (task.totalBytes > 0) return Math.max(0, Math.min(1, task.doneBytes / task.totalBytes));
  if (task.totalFiles > 0) return Math.max(0, Math.min(1, task.doneFiles / task.totalFiles));
  return 0;
}

function fileOpNiceStep(raw) {
  const mag = 10 ** Math.floor(Math.log(raw) / Math.LN10);
  const candidates = [1, 2, 5, 10];
  for (let i = 0; i < candidates.length; i += 1) {
    if (candidates[i] * mag >= raw) return candidates[i] * mag;
  }
  return 10 * mag;
}

function layoutFileOps() {
  const nodes = [...document.querySelectorAll(".fop-dialog")];
  let offset = 0;
  nodes.forEach((node) => {
    if (node.dataset.moved === "1") return;
    const width = node.offsetWidth || 450;
    const height = node.offsetHeight || 280;
    node.style.left = Math.max(8, (window.innerWidth - width) / 2 + offset) + "px";
    node.style.top = Math.max(8, (window.innerHeight - height) / 2 + offset) + "px";
    offset += 26;
  });
}

function bindFileOpDrag(root, handle) {
  handle.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || e.target.closest("button")) return;
    const rect = root.getBoundingClientRect();
    const dx = e.clientX - rect.left;
    const dy = e.clientY - rect.top;
    root.dataset.moved = "1";
    handle.setPointerCapture(e.pointerId);
    function move(ev) {
      const width = root.offsetWidth;
      let left = ev.clientX - dx;
      let top = ev.clientY - dy;
      left = Math.min(Math.max(left, 80 - width), window.innerWidth - 80);
      top = Math.min(Math.max(top, 0), window.innerHeight - 36);
      root.style.left = left + "px";
      root.style.top = top + "px";
    }
    function up() {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
    }
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
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
      '<div class="fop-caption">',
      '<svg viewBox="0 0 16 16" aria-hidden="true"><path fill="#2f7fd1" d="M2 3h5l1 1h6v8H2z"/><path fill="#5aa6e6" d="M3 6h9v5H3z"/></svg>',
      '<span class="fop-caption-text"></span>',
      '<button type="button" class="fop-cap-btn" title="最小化"><span class="fop-min"></span></button>',
      '<button type="button" class="fop-cap-btn dim" title="最大化"><span class="fop-max"></span></button>',
      '<button type="button" class="fop-cap-btn close" data-act="close" title="关闭">×</button>',
      "</div>",
      '<div class="fop-body">',
      '<div class="fop-title"></div>',
      '<div class="fop-row">',
      '<span class="fop-percent"></span>',
      '<span class="fop-tools">',
      '<button type="button" class="fop-pause" data-act="pause" title="暂停"><i></i><i></i></button>',
      '<button type="button" class="fop-stop" data-act="close" title="取消">×</button>',
      "</span>",
      "</div>",
      '<div class="fop-meter"><canvas></canvas><div class="fop-speed" hidden></div></div>',
      '<div class="fop-detail">',
      '<div><span data-k="name">名称: </span><span data-v="name"></span></div>',
      '<div data-row="eta"><span>剩余时间: </span><span data-v="eta"></span></div>',
      '<div data-row="left"><span data-k="left">剩余项目: </span><span data-v="left"></span></div>',
      "</div>",
      "</div>",
      '<button type="button" class="fop-more" data-act="brief"><span class="fop-chev"></span><span data-brief>简略信息</span></button>',
    ].join("");
    root.dataset.id = task.id;
    document.body.appendChild(root);
    layoutFileOps();
    let current = task;
    let pollTimer = 0;
    let closeTimer = 0;
    let settled = false;
    let closed = false;
    const samples = [];
    let lastFrac = 0;
    let peak = 0;
    let axisMax = 0;
    let axisStep = 0;
    let speedClock = 0;
    let lastSpeed = 0;
    let lastEta = null;
    const captionEl = root.querySelector(".fop-caption-text");
    const titleEl = root.querySelector(".fop-title");
    const percentEl = root.querySelector(".fop-percent");
    const canvas = root.querySelector("canvas");
    const ctx = canvas.getContext("2d");
    const speedEl = root.querySelector(".fop-speed");
    const nameKey = root.querySelector('[data-k="name"]');
    const nameEl = root.querySelector('[data-v="name"]');
    const etaRow = root.querySelector('[data-row="eta"]');
    const etaEl = root.querySelector('[data-v="eta"]');
    const leftKey = root.querySelector('[data-k="left"]');
    const leftEl = root.querySelector('[data-v="left"]');
    const pauseBtn = root.querySelector('[data-act="pause"]');
    const closeBtns = [...root.querySelectorAll('[data-act="close"]')];
    const briefBtn = root.querySelector('[data-act="brief"]');
    const briefText = root.querySelector("[data-brief]");
    let brief = false;
    (task.chart || []).forEach((sample) => samples.push(sample));
    if (samples.length) lastFrac = samples[samples.length - 1].to;
    if (task.axisMax) {
      axisMax = task.axisMax;
      axisStep = task.axisStep || 0;
    }
    if (task.etaSeconds != null) lastEta = task.etaSeconds;
    if (task.speed > 0) lastSpeed = task.speed;
    else if (samples.length) lastSpeed = samples[samples.length - 1].speed;

    function drawChart(pausedNow) {
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      if (!w || !h) return;
      const ratio = window.devicePixelRatio || 1;
      if (canvas.width !== Math.round(w * ratio) || canvas.height !== Math.round(h * ratio)) {
        canvas.width = Math.round(w * ratio);
        canvas.height = Math.round(h * ratio);
      }
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, w, h);
      const scale = axisMax > 0 ? axisMax : (peak > 0 ? peak * 1.1 : 1);
      const lineY = lastSpeed > 0 ? h - Math.min(1, lastSpeed / scale) * h : h;
      const light = pausedNow ? "#f3e6a3" : "#b6e7b6";
      const dark = pausedNow ? "#d3b23a" : "#2fbe44";
      samples.forEach((sample) => {
        const x0 = Math.floor(sample.from * w);
        let x1 = Math.ceil(sample.to * w);
        const top = h - Math.min(1, sample.speed / scale) * h;
        if (x1 <= x0) x1 = x0 + 1;
        if (top < lineY) {
          ctx.fillStyle = light;
          ctx.fillRect(x0, top, x1 - x0, lineY - top);
          ctx.fillStyle = dark;
          ctx.fillRect(x0, lineY, x1 - x0, h - lineY);
        } else {
          ctx.fillStyle = dark;
          ctx.fillRect(x0, top, x1 - x0, h - top);
        }
      });
      ctx.strokeStyle = pausedNow ? "rgba(170, 140, 30, .22)" : "rgba(0, 0, 0, .10)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      if (axisStep > 0) {
        for (let level = axisStep; level < scale; level += axisStep) {
          const gy = Math.round(h - (level / scale) * h) + 0.5;
          ctx.moveTo(0, gy);
          ctx.lineTo(w, gy);
        }
      }
      const gap = w / 6;
      for (let gx = gap; gx < w - 1; gx += gap) {
        const px = Math.round(gx) + 0.5;
        ctx.moveTo(px, 0);
        ctx.lineTo(px, h);
      }
      ctx.stroke();
      if (lastSpeed > 0 && !pausedNow) {
        ctx.strokeStyle = "#1c7a2b";
        ctx.beginPath();
        const ly = Math.round(lineY) + 0.5;
        ctx.moveTo(0, ly);
        ctx.lineTo(w, ly);
        ctx.stroke();
        speedEl.hidden = false;
        let labelTop = Math.round(lineY) - 17;
        if (labelTop < 0) labelTop = Math.round(lineY) + 2;
        if (labelTop > h - 16) labelTop = h - 16;
        speedEl.style.top = labelTop + "px";
      } else {
        speedEl.hidden = true;
      }
    }

    function noteSample(next) {
      if (next.axisMax) {
        axisMax = next.axisMax;
        axisStep = next.axisStep || 0;
      }
      if (next.phase !== "run" || next.status !== "running") return;
      const frac = fileOpFraction(next);
      const speed = next.speed || 0;
      if (speed > 0) {
        const now = performance.now();
        if (!speedClock) speedClock = now;
        if (speed > peak) peak = speed;
        if (!axisMax && now - speedClock >= 1000) {
          const target = peak * 1.1;
          let step = fileOpNiceStep(target / 8);
          let max = Math.ceil(target / step) * step;
          if (max / step < 6) {
            step = fileOpNiceStep(target / 10);
            max = Math.ceil(target / step) * step;
          }
          axisStep = step;
          axisMax = max;
        }
        lastSpeed = speed;
      }
      if (frac > lastFrac && speed > 0) {
        samples.push({ from: lastFrac, to: frac, speed });
        lastFrac = frac;
      }
      if (next.etaSeconds != null) lastEta = next.etaSeconds;
    }

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
      noteSample(next);
      root.classList.toggle("is-error", next.status === "error");
      const pausedNow = next.status === "paused";
      const pct = Math.max(0, Math.min(100, Math.floor((next.percent || 0) * 100)));
      const pctText = pausedNow ? "已暂停 - 已完成 " + pct + "%" : (next.phase === "scan" ? "正在计算" : "已完成 " + pct + "%");
      captionEl.textContent = next.phase === "scan" ? "正在计算" : pctText;
      percentEl.textContent = next.phase === "scan" ? "正在计算" : pctText;
      titleEl.innerHTML = fileOpTitle(next);
      speedEl.textContent = lastSpeed > 0 ? "速度: " + formatSpeedValue(lastSpeed) + "/秒" : "速度: 正在计算";
      const failed = next.status === "error";
      nameKey.textContent = failed ? "错误: " : "名称: ";
      nameEl.textContent = failed ? (next.error || "失败") : (next.currentName || "");
      etaRow.hidden = failed;
      root.querySelector('[data-row="left"]').hidden = failed;
      if (!failed) {
        if (next.phase === "scan" || (lastEta == null && next.etaSeconds == null)) etaEl.textContent = "正在计算...";
        else if (next.etaSeconds != null && next.etaSeconds <= 0 && next.status === "running") etaEl.textContent = "即将完成";
        else etaEl.textContent = formatEta(next.etaSeconds != null ? next.etaSeconds : lastEta);
        if (next.phase === "scan") {
          leftKey.textContent = "已发现: ";
          leftEl.textContent = formatCount(next.totalFiles) + " (" + formatSize(next.totalBytes) + ")";
        } else {
          const leftFiles = Math.max(0, (next.totalFiles || 0) - (next.doneFiles || 0));
          const leftBytes = Math.max(0, (next.totalBytes || 0) - (next.doneBytes || 0));
          leftKey.textContent = "剩余项目: ";
          leftEl.textContent = formatCount(leftFiles) + " (" + formatSize(leftBytes) + ")";
        }
      }
      const alive = next.status === "scanning" || next.status === "running" || pausedNow;
      pauseBtn.hidden = !alive;
      pauseBtn.classList.toggle("resume", pausedNow);
      pauseBtn.title = pausedNow ? "继续" : "暂停";
      closeBtns.forEach((btn) => {
        btn.title = alive ? "取消" : "关闭";
      });
      drawChart(pausedNow);
    }

    function refresh() {
      const dirs = current.refreshPaths && current.refreshPaths.length
        ? current.refreshPaths.slice()
        : paths.map((path) => parentPath(path));
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
    closeBtns.forEach((btn) => {
      btn.onclick = () => {
        if (current.status === "success") {
          close();
          settle(() => resolve(current));
          return;
        }
        if (current.status === "error" || current.status === "cancelled") {
          close();
          return;
        }
        closeBtns.forEach((item) => {
          item.disabled = true;
        });
        API.post("/api/fileop/cancel", { id: current.id }).catch((err) => {
          closeBtns.forEach((item) => {
            item.disabled = false;
          });
          toast(err.message || String(err));
        });
      };
    });
    bindFileOpDrag(root, root.querySelector(".fop-caption"));
    briefBtn.onclick = () => {
      brief = !brief;
      root.classList.toggle("brief", brief);
      briefText.textContent = brief ? "详细信息" : "简略信息";
    };
    paint(task);
    pollTimer = setTimeout(poll, 200);
  },

  restore() {
    return API.get("/api/fileop/active").then((res) => {
      (res.data || []).forEach((task) => {
        if (document.querySelector('.fop-dialog[data-id="' + task.id + '"]')) return;
        this.mount(task, [], "", () => {}, (err) => toast(err.message || String(err)));
      });
    });
  },
};

window.FileOp = FileOp;
