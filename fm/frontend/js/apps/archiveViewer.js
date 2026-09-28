(function () {
  const EXT = ["zip", "7z", "rar", "tar", "gz", "tgz", "bz2", "xz", "tbz2", "txz"];
  const ICON = "/assets/kod/images/file_icon/icon_file/zip.png";

  function esc(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function normArc(p) {
    return String(p || "").replace(/\\/g, "/").replace(/^\/+/, "").replace(/\/+$/, "");
  }

  function parentArc(p) {
    const n = normArc(p);
    if (!n) return "";
    const i = n.lastIndexOf("/");
    return i < 0 ? "" : n.slice(0, i);
  }

  function nameOf(p) {
    const n = normArc(p);
    const i = n.lastIndexOf("/");
    return i < 0 ? n : n.slice(i + 1);
  }

  function fileExt(name) {
    const parts = String(name || "").split(".");
    return parts.length > 1 ? parts.pop().toLowerCase() : "";
  }

  function toItem(entry) {
    return {
      type: entry.dir ? "folder" : "file",
      name: entry.name || nameOf(entry.path),
      path: normArc(entry.path),
      ext: entry.dir ? "" : fileExt(entry.name || nameOf(entry.path)),
      size: entry.size || 0,
      modified: entry.modified || "",
      dir: !!entry.dir,
    };
  }

  function ensureFolders(entries) {
    const map = new Map();
    (entries || []).forEach(function (raw) {
      const item = toItem(raw);
      if (!item.path) return;
      map.set(item.path, item);
    });
    [...map.keys()].forEach(function (path) {
      let p = parentArc(path);
      while (p) {
        if (!map.has(p)) {
          map.set(p, {
            type: "folder",
            name: nameOf(p),
            path: p,
            ext: "",
            size: 0,
            modified: "",
            dir: true,
          });
        } else {
          const prev = map.get(p);
          prev.dir = true;
          prev.type = "folder";
        }
        p = parentArc(p);
      }
    });
    return [...map.values()];
  }

  function listAt(all, cwd) {
    const cur = normArc(cwd);
    const prefix = cur ? cur + "/" : "";
    const collator = new Intl.Collator("zh", { numeric: true, sensitivity: "base" });
    return all.filter(function (item) {
      if (item.path === cur) return false;
      if (cur) {
        if (item.path.indexOf(prefix) !== 0) return false;
        return item.path.slice(prefix.length).indexOf("/") < 0;
      }
      return item.path.indexOf("/") < 0;
    }).sort(function (a, b) {
      if (a.dir !== b.dir) return a.dir ? -1 : 1;
      return collator.compare(a.name, b.name);
    });
  }

  function typeLabel(item) {
    if (item.dir) return "文件夹";
    const ext = String(item.ext || "").trim();
    return ext ? ext.toUpperCase() + " 文件" : "文件";
  }

  function openViewer(item, id, opts) {
    opts = opts || {};
    const exist = WM.windows.get(id);
    if (exist) {
      WM.focus(id);
      if (exist.minimized) WM.restore(id);
      if (opts.autoExtract && typeof exist.startArchiveExtract === "function") {
        exist.startArchiveExtract(opts.dest);
      }
      return exist;
    }
    const fileIco = fileIcon(item);
    const root = document.createElement("div");
    root.className = "archive-viewer-app";
    root.innerHTML =
      '<div class="archive-bar">' +
        '<button type="button" data-act="extract">解压</button>' +
        '<button type="button" data-act="open-out" hidden>打开文件夹</button>' +
        '<span class="archive-bar-path"></span>' +
      "</div>" +
      '<div class="archive-progress-wrap" hidden>' +
        '<div class="archive-progress"><span></span></div>' +
        '<div class="archive-progress-meta"><span data-role="pct">0%</span><span data-role="file"></span></div>' +
      "</div>" +
      '<div class="archive-status"></div>' +
      '<div class="frame-main-explorer tree-hidden archive-explorer">' +
        '<div class="frame-right" style="left:0">' +
          '<div class="frame-header"><div class="header-content">' +
            '<div class="header-left"><div class="btn-group">' +
              '<button type="button" class="btn btn-default" data-act="back" title="后退"><i class="font-icon ri-arrow-left-line"></i></button>' +
              '<button type="button" class="btn btn-default" data-act="forward" title="前进"><i class="font-icon ri-arrow-right-line"></i></button>' +
            "</div></div>" +
            '<div class="header-middle">' +
              '<button type="button" class="btn btn-default goto-father" data-act="up" title="上层"><i class="font-icon ri-arrow-up-line"></i></button>' +
              '<div class="header-address"><ul class="header-address-content"></ul></div>' +
            "</div>" +
          "</div></div>" +
          '<div class="frame-right-main">' +
            '<div class="bodymain exp-content">' +
              '<table class="view-list">' +
                "<thead><tr><th>名称</th><th style=\"width:160px\">修改时间</th><th style=\"width:120px\">类型</th><th style=\"width:100px\">大小</th></tr></thead>" +
                "<tbody></tbody>" +
              "</table>" +
              '<div class="empty-tip" hidden>文件夹为空</div>' +
            "</div>" +
            '<div class="file-select-info"><span class="item-num">0 个项目</span></div>' +
          "</div>" +
        "</div>" +
      "</div>";

    const win = WM.create({
      id: id,
      title: item.name,
      icon: fileIco,
      className: "archive-viewer-dialog",
      titleHtml: '<span class="path-ico" style="background-image:url(\'' + fileIco + "')\"></span><span>" + esc(item.name) + "</span>",
      width: Math.min(760, Math.floor(window.innerWidth * 0.7)),
      height: Math.min(560, Math.floor(window.innerHeight * 0.75)),
      content: root,
    });

    const extractBtn = root.querySelector('[data-act="extract"]');
    const openOutBtn = root.querySelector('[data-act="open-out"]');
    const pathEl = root.querySelector(".archive-bar-path");
    const statusEl = root.querySelector(".archive-status");
    const progressWrap = root.querySelector(".archive-progress-wrap");
    const progressBar = root.querySelector(".archive-progress span");
    const pctEl = root.querySelector('[data-role="pct"]');
    const fileEl = root.querySelector('[data-role="file"]');
    const content = root.querySelector(".bodymain");
    const table = content.querySelector("table.view-list");
    const tbody = table.querySelector("tbody");
    const emptyEl = content.querySelector(".empty-tip");
    const countEl = root.querySelector(".item-num");
    const addrCrumb = root.querySelector(".header-address-content");
    const btnBack = root.querySelector('[data-act="back"]');
    const btnForward = root.querySelector('[data-act="forward"]');
    const btnUp = root.querySelector('[data-act="up"]');
    pathEl.textContent = item.path;

    let busy = false;
    let outPath = "";
    let ac = null;
    let done = false;
    let allEntries = [];
    let cwd = "";
    let history = [""];
    let histIndex = 0;
    let visible = [];

    function setProgress(percent, file) {
      const pct = Math.max(0, Math.min(100, Number(percent) || 0));
      progressWrap.hidden = false;
      progressBar.style.width = pct + "%";
      pctEl.textContent = pct + "%";
      fileEl.textContent = file || "";
    }

    function updateNav() {
      btnBack.classList.toggle("disable", histIndex <= 0);
      btnForward.classList.toggle("disable", histIndex >= history.length - 1);
      btnUp.classList.toggle("disable", !cwd);
    }

    function renderCrumb() {
      const parts = cwd ? cwd.split("/") : [];
      const crumbs = [{ name: item.name, path: "", icon: fileIco }];
      let acc = "";
      parts.forEach(function (seg) {
        acc = acc ? acc + "/" + seg : seg;
        crumbs.push({ name: seg, path: acc, icon: FOLDER_ICON });
      });
      addrCrumb.innerHTML = crumbs.map(function (c, i) {
        const first = i === 0 ? " first" : "";
        const last = i === crumbs.length - 1 ? " last" : "";
        return '<li class="header-address-item' + first + last + '">' +
          '<a href="javascript:;" data-path="' + esc(c.path) + '" title="' + esc(c.name) + '">' +
          '<span class="path-ico" style="background-image:url(\'' + c.icon + "')\"></span>" +
          '<span class="title-name">' + esc(c.name) + "</span></a></li>";
      }).join("") + '<li class="clear"></li>';
    }

    function renderLevel() {
      visible = listAt(allEntries, cwd);
      tbody.innerHTML = "";
      if (!visible.length) {
        table.hidden = true;
        emptyEl.hidden = false;
      } else {
        table.hidden = false;
        emptyEl.hidden = true;
        visible.forEach(function (row, index) {
          const tr = document.createElement("tr");
          tr.dataset.path = row.path;
          tr.dataset.index = String(index);
          tr.dataset.type = row.type;
          tr.innerHTML =
            '<td><span class="lico" style="background-image:url(\'' + fileIcon(row) + "')\"></span>" + esc(row.name) + "</td>" +
            "<td>" + esc(row.modified || "-") + "</td>" +
            "<td>" + esc(typeLabel(row)) + "</td>" +
            "<td>" + (row.dir ? "-" : formatSize(row.size)) + "</td>";
          tbody.appendChild(tr);
        });
      }
      countEl.textContent = visible.length + " 个项目";
      renderCrumb();
      updateNav();
    }

    function go(path, push) {
      cwd = normArc(path);
      if (push !== false) {
        history = history.slice(0, histIndex + 1);
        if (history[history.length - 1] !== cwd) {
          history.push(cwd);
          histIndex = history.length - 1;
        }
      }
      renderLevel();
    }

    function hitItem(e) {
      const el = e.target.closest("tr[data-path]");
      if (!el || !content.contains(el)) return null;
      const index = Number(el.dataset.index);
      const row = visible[index];
      if (!row) return null;
      return { el: el, item: row, index: index };
    }

    async function loadList() {
      if (!busy && !done) statusEl.textContent = "正在读取列表";
      const res = await API.archiveList(item.path);
      allEntries = ensureFolders((res.data && res.data.entries) || []);
      go(cwd, false);
      if (!busy && !done) statusEl.textContent = allEntries.length + " 个项目";
    }

    async function startExtract(dest, members) {
      members = members || [];
      if (busy) return;
      if (!members.length && done) return;
      busy = true;
      extractBtn.disabled = true;
      openOutBtn.hidden = true;
      progressWrap.hidden = false;
      setProgress(0, "");
      statusEl.textContent = members.length ? "正在解压选中项" : "正在解压";
      ac = new AbortController();
      try {
        await API.archiveExtract(item.path, dest || parentPath(item.path), function (ev) {
          if (ev.type === "start") {
            outPath = ev.out || "";
            return;
          }
          if (ev.type === "progress") {
            setProgress(ev.percent, ev.file);
            return;
          }
          if (ev.type === "done") {
            if (!members.length) done = true;
            outPath = ev.out || outPath;
            setProgress(100, "");
            statusEl.textContent = "解压完成: " + outPath;
            openOutBtn.hidden = !outPath;
            extractBtn.disabled = done;
            if (typeof FMNotifyFsChanged === "function") {
              FMNotifyFsChanged([dest || parentPath(item.path)]);
            }
            return;
          }
          if (ev.type === "error") {
            throw new Error(ev.message || "解压失败");
          }
        }, ac.signal, members);
      } catch (err) {
        if (err && err.name === "AbortError") return;
        extractBtn.disabled = done;
        statusEl.textContent = err.message || String(err);
      } finally {
        busy = false;
        ac = null;
      }
    }

    async function openInner(row) {
      if (row.dir) {
        go(row.path, true);
        return;
      }
      if (busy) return;
      busy = true;
      extractBtn.disabled = true;
      progressWrap.hidden = false;
      setProgress(0, row.name);
      statusEl.textContent = "正在打开 " + row.name;
      ac = new AbortController();
      try {
        let opened = null;
        await API.archiveOpen(item.path, row.path, function (ev) {
          if (ev.type === "progress") {
            setProgress(ev.percent, ev.file);
            return;
          }
          if (ev.type === "done") {
            opened = ev.item;
            setProgress(100, "");
            return;
          }
          if (ev.type === "error") {
            throw new Error(ev.message || "打开失败");
          }
        }, ac.signal);
        extractBtn.disabled = done;
        statusEl.textContent = "已打开 " + row.name;
        if (opened) await openFile(opened);
      } catch (err) {
        if (err && err.name === "AbortError") return;
        extractBtn.disabled = done;
        statusEl.textContent = err.message || String(err);
      } finally {
        busy = false;
        ac = null;
      }
    }

    function showCtx(x, y, row) {
      const menu = document.getElementById("ctx-menu");
      const buttons = [
        ["打开", function () { return openInner(row); }],
        ["解压", function () { return startExtract(opts.dest || parentPath(item.path), [row.path]); }],
      ];
      menu.innerHTML = "";
      buttons.forEach(function (pair) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = pair[0];
        btn.onclick = async function () {
          menu.classList.add("hidden");
          try {
            await pair[1]();
          } catch (err) {
            toast(err.message || String(err));
          }
        };
        menu.appendChild(btn);
      });
      menu.classList.remove("hidden");
      const rect = menu.getBoundingClientRect();
      menu.style.left = Math.min(x, window.innerWidth - rect.width - 4) + "px";
      menu.style.top = Math.min(y, window.innerHeight - rect.height - 4) + "px";
    }

    extractBtn.onclick = function () {
      startExtract(opts.dest || parentPath(item.path));
    };
    openOutBtn.onclick = function () {
      if (!outPath) return;
      openExplorer(outPath);
    };
    btnBack.onclick = function () {
      if (histIndex <= 0) return;
      histIndex -= 1;
      cwd = history[histIndex];
      renderLevel();
    };
    btnForward.onclick = function () {
      if (histIndex >= history.length - 1) return;
      histIndex += 1;
      cwd = history[histIndex];
      renderLevel();
    };
    btnUp.onclick = function () {
      if (!cwd) return;
      go(parentArc(cwd), true);
    };
    addrCrumb.addEventListener("click", function (e) {
      const a = e.target.closest("a[data-path]");
      if (!a) return;
      e.preventDefault();
      go(a.getAttribute("data-path") || "", true);
    });
    content.addEventListener("click", function (e) {
      const hit = hitItem(e);
      if (!hit) return;
      content.querySelectorAll("tr.selected").forEach(function (n) { n.classList.remove("selected"); });
      hit.el.classList.add("selected");
    });
    content.addEventListener("dblclick", function (e) {
      if (typeof fmIsTouchMode === "function" && fmIsTouchMode()) return;
      const hit = hitItem(e);
      if (!hit) return;
      e.preventDefault();
      openInner(hit.item);
    });
    content.addEventListener("contextmenu", function (e) {
      if (typeof fmIsTouchMode === "function" && fmIsTouchMode()) {
        e.preventDefault();
        return;
      }
      const hit = hitItem(e);
      if (!hit) return;
      e.preventDefault();
      e.stopPropagation();
      content.querySelectorAll("tr.selected").forEach(function (n) { n.classList.remove("selected"); });
      hit.el.classList.add("selected");
      showCtx(e.clientX, e.clientY, hit.item);
    });
    win.startArchiveExtract = startExtract;
    win.onClose = function () {
      if (ac) ac.abort();
      return true;
    };

    loadList().catch(function (err) {
      statusEl.textContent = err.message || String(err);
    });
    if (opts.autoExtract) startExtract(opts.dest);
    return win;
  }

  window.FMArchiveExtract = function (item, dest) {
    if (!item || !item.path) throw new Error("需要压缩包");
    return openViewer(item, "archiveViewer:" + item.path, { autoExtract: true, dest: dest });
  };

  kodApp.add({
    name: "archiveViewer",
    title: "压缩包",
    sort: 90,
    menu: false,
    ext: EXT,
    icon: ICON,
    async open(item, id) {
      if (!item) {
        const empty = document.createElement("div");
        empty.className = "app-empty";
        empty.textContent = "在文件管理器中双击压缩包以查看内容";
        return WM.create({
          id: id || "archiveViewer:empty",
          title: "压缩包",
          icon: ICON,
          className: "archive-viewer-dialog",
          titleHtml: '<span class="path-ico" style="background-image:url(\'' + ICON + "')\"></span><span>压缩包</span>",
          width: 640,
          height: 420,
          content: empty,
        });
      }
      return openViewer(item, id, {});
    },
  });
})();
