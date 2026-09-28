(function () {
  const EXT = ["png", "jpg", "jpeg", "gif", "bmp", "webp", "svg", "ico"];
  const ICON = `${ICON_BASE}/icon_file/jpg.png`;
  const MIN_ZOOM = 0.1;
  const MAX_ZOOM = 8;

  async function listSiblings(item) {
    const parent = parentPath(item.path);
    const res = await API.list(parent);
    const files = res.data.fileList.filter((f) => EXT.includes((f.ext || "").toLowerCase()));
    return files.length ? files : [item];
  }

  function fitWindow(win, imgW, imgH) {
    if (!win || !imgW || !imgH || win.maximized) return;
    const chromeH = 34 + 40;
    const maxW = window.innerWidth * 0.9;
    const maxH = window.innerHeight * 0.9;
    let width = imgW;
    let height = imgH + chromeH;
    if (height > maxH) {
      const scale = (maxH - chromeH) / imgH;
      width = Math.floor(imgW * scale);
      height = Math.floor(maxH);
    }
    if (width > maxW) {
      const scale = maxW / width;
      width = Math.floor(maxW);
      height = Math.floor(height * scale);
    }
    width = Math.max(480, width);
    height = Math.max(320, height);
    win.el.style.width = `${width}px`;
    win.el.style.height = `${height}px`;
    win.el.style.left = `${Math.max(20, Math.floor((window.innerWidth - width) / 2))}px`;
    win.el.style.top = `${Math.max(20, Math.floor((window.innerHeight - height) / 2))}px`;
  }

  function openViewer(item, id) {
    const fileIco = fileIcon(item);
    const root = document.createElement("div");
    root.className = "image-viewer-app";
    root.tabIndex = 0;
    root.innerHTML = `
      <div class="image-viewer-stage">
        <img class="image-viewer-img" alt="" draggable="false" />
      </div>
      <div class="image-viewer-bar">
        <button type="button" data-act="prev" title="上一张"><i class="font-icon ri-arrow-left-line"></i></button>
        <span class="image-viewer-index"></span>
        <button type="button" data-act="next" title="下一张"><i class="font-icon ri-arrow-right-line"></i></button>
        <span class="sep"></span>
        <button type="button" data-act="zoom-out" title="缩小"><i class="font-icon ri-subtract-line"></i></button>
        <span class="image-viewer-zoom">100%</span>
        <button type="button" data-act="zoom-in" title="放大"><i class="font-icon ri-add-line"></i></button>
        <button type="button" data-act="fit" title="适应窗口"><i class="font-icon ri-fullscreen-line"></i></button>
        <button type="button" data-act="rotate" title="旋转"><i class="font-icon ri-refresh-line"></i></button>
        <span class="sep"></span>
        <button type="button" data-act="download" title="下载"><i class="font-icon ri-download-fill-2"></i></button>
        <button type="button" data-act="remove" title="删除"><i class="font-icon ri-close-circle-fill"></i></button>
      </div>
    `;

    const stage = root.querySelector(".image-viewer-stage");
    const img = root.querySelector(".image-viewer-img");
    const zoomEl = root.querySelector(".image-viewer-zoom");
    const indexEl = root.querySelector(".image-viewer-index");

    const win = WM.create({
      id,
      title: item.name,
      icon: fileIco,
      className: "image-viewer-dialog",
      titleHtml: `<span class="path-ico" style="background-image:url('${fileIco}')"></span><span>${escapeHtml(item.name)}</span>`,
      width: Math.floor(window.innerWidth * 0.7),
      height: Math.floor(window.innerHeight * 0.6),
      content: root,
    });

    const state = {
      item,
      siblings: [],
      index: 0,
      natW: 0,
      natH: 0,
      scale: 1,
      fitScale: 1,
      rotate: 0,
      x: 0,
      y: 0,
      fitted: false,
    };

    function applyTransform() {
      img.style.transform = `translate(-50%, -50%) translate(${state.x}px, ${state.y}px) rotate(${state.rotate}deg) scale(${state.scale})`;
      const base = state.fitScale || 1;
      zoomEl.textContent = `${Math.round((state.scale / base) * 100)}%`;
    }

    function computeFitScale() {
      if (!state.natW || !state.natH) return 1;
      const cw = stage.clientWidth;
      const ch = stage.clientHeight;
      if (!cw || !ch) return 1;
      const rad = ((state.rotate % 360) + 360) % 360;
      const swapped = rad === 90 || rad === 270;
      const w = swapped ? state.natH : state.natW;
      const h = swapped ? state.natW : state.natH;
      return Math.min(cw / w, ch / h);
    }

    function fitImage() {
      state.fitScale = computeFitScale();
      state.scale = state.fitScale || 1;
      state.x = 0;
      state.y = 0;
      applyTransform();
    }

    function setZoom(next, cx, cy) {
      const prev = state.scale;
      const base = state.fitScale || 1;
      const clamped = Math.max(MIN_ZOOM * base, Math.min(MAX_ZOOM * base, next));
      if (clamped === prev) return;
      if (cx != null && cy != null && prev) {
        const rect = stage.getBoundingClientRect();
        const px = cx - rect.left - rect.width / 2;
        const py = cy - rect.top - rect.height / 2;
        const k = clamped / prev;
        state.x = px - (px - state.x) * k;
        state.y = py - (py - state.y) * k;
      }
      state.scale = clamped;
      applyTransform();
    }

    function updateIndex() {
      const n = state.siblings.length;
      indexEl.textContent = n ? `${state.index + 1} / ${n}` : "1 / 1";
    }

    function showItem(next) {
      state.item = next;
      state.rotate = 0;
      state.x = 0;
      state.y = 0;
      const ico = fileIcon(next);
      win.title = next.name;
      win.icon = ico;
      win.el.querySelector(".win-title").innerHTML =
        `<span class="path-ico" style="background-image:url('${ico}')"></span><span>${escapeHtml(next.name)}</span>`;
      WM.renderTaskbar();
      img.src = API.mediaUrl(next);
      updateIndex();
    }

    async function refreshSiblings() {
      state.siblings = await listSiblings(state.item);
      state.index = state.siblings.findIndex((f) => f.path === state.item.path);
      if (state.index < 0) state.index = 0;
      updateIndex();
    }

    async function openSibling(delta) {
      if (!state.siblings.length) await refreshSiblings();
      if (state.siblings.length < 2) return;
      const n = state.siblings.length;
      const nextIndex = (state.index + delta + n) % n;
      const next = state.siblings[nextIndex];
      if (next.path === state.item.path) return;
      state.index = nextIndex;
      showItem(next);
    }

    img.onload = () => {
      state.natW = img.naturalWidth;
      state.natH = img.naturalHeight;
      if (!state.fitted) {
        fitWindow(win, state.natW, state.natH);
        state.fitted = true;
      }
      requestAnimationFrame(fitImage);
    };
    img.src = API.mediaUrl(item);

    const mediaView = {
      hasPath: (path) => path === normalizeFsPath(state.item.path),
      update(fresh) {
        state.item = fresh;
        const target = normalizeFsPath(fresh.path);
        state.siblings = state.siblings.map((x) => (normalizeFsPath(x.path) === target ? fresh : x));
        img.src = API.mediaUrl(fresh);
      },
    };
    window.FMMediaViews.add(mediaView);

    refreshSiblings().catch((err) => toast(err.message || String(err)));

    root.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-act]");
      if (!btn) return;
      const act = btn.dataset.act;
      if (act === "prev") openSibling(-1).catch((err) => toast(err.message || String(err)));
      if (act === "next") openSibling(1).catch((err) => toast(err.message || String(err)));
      if (act === "zoom-in") setZoom(state.scale * 1.25);
      if (act === "zoom-out") setZoom(state.scale / 1.25);
      if (act === "fit") fitImage();
      if (act === "rotate") {
        state.rotate = (state.rotate + 90) % 360;
        fitImage();
      }
      if (act === "download") {
        const a = document.createElement("a");
        a.href = API.downloadUrl(state.item.path);
        a.download = state.item.name || "image";
        document.body.appendChild(a);
        a.click();
        a.remove();
      }
      if (act === "remove") {
        confirmModal("删除", `确定删除 ${state.item.name} 吗？`).then(async (okDel) => {
          if (!okDel) return;
          const path = state.item.path;
          await API.remove([path]);
          WM.close(win.id);
          toast("已删除");
          await FMNotifyFsChanged([parentPath(path)]);
        }).catch((err) => toast(err.message || String(err)));
      }
    });

    stage.addEventListener("wheel", (e) => {
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
      setZoom(state.scale * factor, e.clientX, e.clientY);
    }, { passive: false });

    stage.addEventListener("dblclick", (e) => {
      const ratio = state.scale / (state.fitScale || 1);
      if (ratio > 1.05 || ratio < 0.95) fitImage();
      else setZoom((state.fitScale || 1) * 2, e.clientX, e.clientY);
    });

    let drag = null;
    stage.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      drag = { x: e.clientX, y: e.clientY, ox: state.x, oy: state.y, pid: e.pointerId };
      stage.setPointerCapture(e.pointerId);
      stage.classList.add("is-dragging");
    });
    stage.addEventListener("pointermove", (e) => {
      if (!drag || e.pointerId !== drag.pid) return;
      state.x = drag.ox + (e.clientX - drag.x);
      state.y = drag.oy + (e.clientY - drag.y);
      applyTransform();
    });
    const endDrag = (e) => {
      if (!drag || e.pointerId !== drag.pid) return;
      drag = null;
      stage.classList.remove("is-dragging");
    };
    stage.addEventListener("pointerup", endDrag);
    stage.addEventListener("pointercancel", endDrag);

    root.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        openSibling(-1).catch((err) => toast(err.message || String(err)));
      }
      if (e.key === "ArrowRight") {
        e.preventDefault();
        openSibling(1).catch((err) => toast(err.message || String(err)));
      }
      if (e.key === "+" || e.key === "=") setZoom(state.scale * 1.25);
      if (e.key === "-" || e.key === "_") setZoom(state.scale / 1.25);
      if (e.key === "0") fitImage();
      if (e.key === "r" || e.key === "R") {
        state.rotate = (state.rotate + 90) % 360;
        fitImage();
      }
    });
    root.addEventListener("mousedown", () => root.focus());

    const obs = new ResizeObserver(() => {
      const prevFit = state.fitScale || 1;
      const nextFit = computeFitScale();
      if (!nextFit) return;
      const user = state.scale / prevFit;
      state.fitScale = nextFit;
      state.scale = nextFit * user;
      applyTransform();
    });
    obs.observe(stage);
    win.onClose = () => {
      window.FMMediaViews.delete(mediaView);
      obs.disconnect();
    };

    root.focus();
    return win;
  }

  kodApp.add({
    name: "imageViewer",
    title: "图片查看器",
    sort: 20,
    ext: EXT,
    icon: ICON,
    async open(item, id) {
      if (!item) {
        const root = document.createElement("div");
        root.className = "app-empty";
        root.textContent = "在文件管理器中双击图片以预览";
        return WM.create({
          id: id || "imageViewer:empty",
          title: "图片查看器",
          icon: ICON,
          className: "image-viewer-dialog",
          titleHtml: `<span class="path-ico" style="background-image:url('${ICON}')"></span><span>图片查看器</span>`,
          width: 520,
          height: 360,
          content: root,
        });
      }
      return openViewer(item, id);
    },
  });
})();
