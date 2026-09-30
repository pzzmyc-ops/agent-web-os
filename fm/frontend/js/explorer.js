function openExplorer(startPath = "/") {
  const id = `explorer-${Date.now()}`;
  const HOME_NAME = COMPUTER_NAME;
  const HOME_ICON = "/assets/kod/images/file_icon/icon_others/folder_win11.png";
  const root = document.createElement("div");
  root.className = "frame-main-explorer menu-show-parent";
  root.innerHTML = `
    <div class="menu-show-toggle" title="收起/展开目录"><div class="btn btn-default"><i class="font-icon ri-indent-increase"></i></div></div>
    <div class="frame-left" style="width:220px">
      <div class="tree-title">文件管理</div>
      <ul class="exp-ztree"></ul>
    </div>
    <div class="drag-resize drag-resize-tree"><span class="drag-item"></span></div>
    <div class="frame-right" style="left:220px">
      <div class="frame-header">
        <div class="header-content">
          <div class="header-left">
            <div class="btn-group">
              <button type="button" class="btn btn-default history-back" data-act="back" title="后退"><i class="font-icon ri-arrow-left-line"></i></button>
              <button type="button" class="btn btn-default history-next" data-act="forward" title="前进"><i class="font-icon ri-arrow-right-line"></i></button>
            </div>
          </div>
          <div class="header-middle">
            <button type="button" class="btn btn-default goto-father" data-act="up" title="上层"><i class="font-icon ri-arrow-up-line"></i></button>
            <div class="header-address">
              <ul class="header-address-content"></ul>
            </div>
            <div class="header-address-input hidden">
              <input type="text" class="path" spellcheck="false" />
            </div>
            <button type="button" class="btn btn-default refresh-button" data-act="refresh" title="刷新"><i class="font-icon ri-refresh-line"></i></button>
          </div>
          <div class="header-right">
            <div class="search-box">
              <input type="text" class="search" placeholder="搜索" spellcheck="false" />
              <i class="font-icon search-clear ri-close-circle-fill hidden" title="清空"></i>
              <button type="button" class="btn btn-default btn-sm start-search" data-act="search" title="搜索"><i class="font-icon ri-search-line"></i></button>
            </div>
          </div>
        </div>
      </div>
      <div class="frame-right-main">
        <div class="tools">
          <div class="tools-right">
            <div class="btn-group">
              <button type="button" class="btn btn-default list-type active" data-act="view-icon" title="图标排列"><i class="font-icon ri-grid-fill"></i></button>
              <button type="button" class="btn btn-default list-type" data-act="view-list" title="列表排列"><i class="font-icon ri-list-check"></i></button>
            </div>
          </div>
        </div>
        <div class="bodymain exp-content"></div>
        <div class="file-select-info"><span class="item-num">0 个项目</span></div>
      </div>
    </div>
  `;

  const state = {
    path: startPath,
    history: [startPath],
    histIndex: 0,
    view: "icon",
    items: [],
    selected: new Set(),
    lastIndex: -1,
    treeWidth: 220,
    treeHidden: false,
    treeExpanded: new Set(["/"]),
    treeChildren: new Map(),
    searching: false,
    iconCellW: 100,
    iconCellH: 108,
    listRowH: 37,
    sortKey: "name",
    sortDir: 1,
  };
  const SORT_COLUMNS = [
    { key: "name", label: "名称", width: "" },
    { key: "modifyTime", label: "修改时间", width: "160px" },
    { key: "type", label: "类型", width: "120px" },
    { key: "size", label: "大小", width: "100px" },
  ];
  const nameCollator = new Intl.Collator("zh", { numeric: true, sensitivity: "base" });

  const win = WM.create({
    id,
    title: HOME_NAME,
    icon: HOME_ICON,
    width: 960,
    height: 640,
    className: "explorer-dialog",
    titleHtml: `<span class="path-ico" style="background-image:url('${HOME_ICON}')"></span><span>${HOME_NAME}</span>`,
    content: root,
  });

  const content = root.querySelector(".bodymain");
  const addrCrumb = root.querySelector(".header-address-content");
  const addrWrap = root.querySelector(".header-address");
  const addrInputWrap = root.querySelector(".header-address-input");
  const addr = root.querySelector(".header-address-input .path");
  const searchInput = root.querySelector(".search-box .search");
  const searchClear = root.querySelector(".search-clear");
  const stLeft = root.querySelector(".item-num");
  const left = root.querySelector(".frame-left");
  const frameRight = root.querySelector(".frame-right");
  const treeDrag = root.querySelector(".drag-resize-tree");
  const treeToggle = root.querySelector(".menu-show-toggle");
  const treeEl = root.querySelector(".exp-ztree");
  const btnBack = root.querySelector('[data-act="back"]');
  const btnForward = root.querySelector('[data-act="forward"]');
  const btnUp = root.querySelector('[data-act="up"]');

  const view = {
    winId: id,
    getPath: () => state.path,
    async refreshPaths(dirs) {
      const set = new Set((dirs || []).map((p) => normalizeFsPath(p)));
      set.forEach((d) => state.treeChildren.delete(d));
      const treeJobs = [];
      set.forEach((d) => {
        if (state.treeExpanded.has(d) && d !== state.path) {
          treeJobs.push(
            API.list(d).then((res) => {
              state.treeChildren.set(d, res.data.folderList);
            })
          );
        }
      });
      if (treeJobs.length) await Promise.all(treeJobs);
      if (set.has(state.path)) {
        await load(state.path, false, true);
        return;
      }
      if (treeJobs.length) renderTree();
    },
  };
  window.FMExplorerViews.add(view);

  // 给 agent 的可编程接口。DesktopOS 不认识 explorer,只转发到这里 ——
  // 加能力改这一处,服务端零改动(见 desktop-os.js 的 registerInstance)。
  //
  // 选中态只在 describe() 里体现、不发事件:它有 10 多个修改点且鼠标框选会高频触发,
  // 按需拉快照比推事件划算。导航相反(低频 + 唯一漏斗),所以 load() 里发事件。
  window.DesktopOS.registerInstance(id, {
    app: "explorer",
    describe: () => ({
      path: state.path,
      selection: [...state.selected],
      viewMode: state.view,
      itemCount: state.items.length,
      searching: state.searching,
    }),
    capabilities: {
      "explorer.listItems": {
        description: "列出当前目录下的条目(用户此刻看到的那一屏)",
        params: {},
        run: () => ({
          path: state.path,
          items: state.items.map((it) => ({ name: it.name, path: it.path, type: it.type, size: it.size })),
        }),
      },
      "explorer.navigate": {
        description: "让这个窗口切换到指定目录",
        params: { path: { type: "string", required: true, description: "目录的真实路径,正斜杠,如 D:/项目" } },
        run: async ({ path }) => {
          await load(normalizeFsPath(path));
          return { path: state.path };
        },
      },
      "explorer.select": {
        description: "在当前目录里选中指定的文件(路径必须是当前目录下的条目)",
        params: { paths: { type: "array", required: true, description: "要选中的文件路径数组" } },
        run: ({ paths }) => {
          const known = new Set(state.items.map((it) => it.path));
          const want = paths.map((p) => normalizeFsPath(p));
          const missing = want.filter((p) => !known.has(p));
          if (missing.length) throw new Error(`不在当前目录里: ${missing.join(", ")}`);
          state.selected.clear();
          want.forEach((p) => state.selected.add(p));
          render();
          return { selection: [...state.selected] };
        },
      },
    },
  });

  win.onClose = () => {
    if (state.sizeWatcher) state.sizeWatcher.disconnect();
    window.FMExplorerViews.delete(view);
    window.DesktopOS.unregisterInstance(id);
  };

  function syncTreeLayout() {
    root.classList.toggle("tree-hidden", state.treeHidden);
    if (state.treeHidden) {
      frameRight.style.left = "0px";
      treeToggle.style.left = "0px";
    } else {
      left.style.width = `${state.treeWidth}px`;
      frameRight.style.left = `${state.treeWidth}px`;
      treeDrag.style.left = `${state.treeWidth}px`;
      treeToggle.style.left = `${Math.max(0, state.treeWidth - 8)}px`;
    }
  }

  function updateNavButtons() {
    btnBack.classList.toggle("disable", state.histIndex <= 0);
    btnForward.classList.toggle("disable", state.histIndex >= state.history.length - 1);
    btnUp.classList.toggle("disable", state.path === "/");
    root.querySelectorAll(".list-type").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.act === `view-${state.view}`);
    });
  }

  function updateSearchClear() {
    searchClear.classList.toggle("hidden", !searchInput.value.trim());
  }

  function renderCrumb() {
    const crumbs = pathChain(state.path).map((p) => ({
      name: pathBaseName(p),
      path: p,
      icon: p === "/" ? HOME_ICON : FOLDER_ICON,
    }));
    addrCrumb.innerHTML = crumbs.map((c, i) => {
      const first = i === 0 ? " first" : "";
      const last = i === crumbs.length - 1 ? " last" : "";
      return `<li class="header-address-item${first}${last}">
        <a href="javascript:;" data-path="${escapeHtml(c.path)}" title="${escapeHtml(c.name)}">
          <span class="path-ico" style="background-image:url('${c.icon}')"></span>
          <span class="title-name">${escapeHtml(c.name)}</span>
        </a>
      </li>`;
    }).join("") + '<li class="clear"></li>';
    addr.value = state.path;
    addrInputWrap.classList.add("hidden");
    addrWrap.style.display = "";
  }

  function editAddress() {
    addrWrap.style.display = "none";
    addrInputWrap.classList.remove("hidden");
    addr.value = state.path;
    addr.focus();
    addr.select();
  }

  function normalizePath(raw) {
    return normalizeFsPath(raw);
  }

  async function ensureTreePath(path) {
    for (const node of pathChain(path)) {
      state.treeExpanded.add(node);
      if (!state.treeChildren.has(node)) {
        const res = await API.list(node);
        state.treeChildren.set(node, res.data.folderList);
      }
    }
  }

  function renderTreeRoot(path, name, icon) {
    const folders = state.treeChildren.get(path) || [];
    const openRoot = state.treeExpanded.has(path);
    const activeRoot = state.path === path;
    let html = `<li class="level0">
      <a class="tree-node${activeRoot ? " curSelectedNode" : ""}" data-path="${escapeHtml(path)}" data-type="folder">
        <span class="space" style="width:0"></span>
        <span class="switch ${openRoot ? "noline_open" : "noline_close"}" data-switch="${escapeHtml(path)}"></span>
        <span class="tree_icon"><span class="tico" style="background-image:url('${icon}')"></span></span>
        <span class="node_name">${escapeHtml(name)}</span>
      </a>`;
    if (openRoot) {
      html += `<ul class="level0">${folders.map((f) => renderTreeNode(f, 1)).join("")}</ul>`;
    }
    html += "</li>";
    return html;
  }

  function renderTree() {
    treeEl.innerHTML = renderTreeRoot("/", HOME_NAME, HOME_ICON);
  }

  function renderTreeNode(folder, level) {
    const open = state.treeExpanded.has(folder.path);
    const kids = state.treeChildren.get(folder.path);
    const hasKids = kids ? kids.length > 0 : true;
    const active = state.path === folder.path ? " curSelectedNode" : "";
    const sw = !hasKids && kids ? "noline_docu" : (open ? "noline_open" : "noline_close");
    let html = `<li class="level${level}">
      <a class="tree-node${active}" data-path="${escapeHtml(folder.path)}" data-type="folder">
        <span class="space" style="width:${level * 15}px"></span>
        <span class="switch ${sw}" data-switch="${escapeHtml(folder.path)}"></span>
        <span class="tree_icon"><span class="tico" style="background-image:url('${fileIcon(folder)}')"></span></span>
        <span class="node_name">${escapeHtml(folder.name)}</span>
      </a>`;
    if (open && kids && kids.length) {
      html += `<ul class="level${level}">${kids.map((c) => renderTreeNode(c, level + 1)).join("")}</ul>`;
    }
    html += "</li>";
    return html;
  }

  async function toggleTree(path) {
    if (state.treeExpanded.has(path)) {
      state.treeExpanded.delete(path);
      renderTree();
      return;
    }
    if (!state.treeChildren.has(path)) {
      const res = await API.list(path);
      state.treeChildren.set(path, res.data.folderList);
    }
    state.treeExpanded.add(path);
    renderTree();
  }

  function typeLabel(item) {
    if (item.type === "folder") return "文件夹";
    const ext = String(item.ext || "").trim();
    return ext ? `${ext.toUpperCase()} 文件` : "文件";
  }

  function compareItems(a, b) {
    if (a.type !== b.type) return a.type === "folder" ? -1 : 1;
    let r = 0;
    if (state.sortKey === "name") {
      r = nameCollator.compare(a.name, b.name);
    } else if (state.sortKey === "modifyTime") {
      r = (a.modifyTime || 0) - (b.modifyTime || 0);
    } else if (state.sortKey === "type") {
      r = nameCollator.compare(typeLabel(a), typeLabel(b));
    } else if (state.sortKey === "size") {
      r = (a.size || 0) - (b.size || 0);
    }
    if (r === 0) r = nameCollator.compare(a.name, b.name);
    return r * state.sortDir;
  }

  function applySort() {
    state.items.sort(compareItems);
    state.lastIndex = -1;
  }

  function setSort(key) {
    if (state.sortKey === key) {
      state.sortDir = -state.sortDir;
    } else {
      state.sortKey = key;
      state.sortDir = 1;
    }
    applySort();
    render();
  }

  function paintSortHeader(table) {
    table.querySelectorAll("th[data-sort]").forEach((th) => {
      const active = th.dataset.sort === state.sortKey;
      th.classList.toggle("sort-asc", active && state.sortDir === 1);
      th.classList.toggle("sort-desc", active && state.sortDir === -1);
    });
  }

  async function load(path, pushHist = true, skipSync = false) {
    const target = normalizePath(path);
    state.treeChildren.delete(target);
    const res = await API.list(target);
    const from = state.path;
    state.path = res.data.current.path;
    content.dataset.dropPath = state.path;
    state.items = [...res.data.folderList, ...res.data.fileList];
    applySort();
    state.selected.clear();
    state.searching = false;
    // 导航是低频且只有这一个漏斗,所以发事件(选中态相反,见 view.getSelection)。
    if (window.DesktopOS && from !== state.path) {
      window.DesktopOS.emit({ op: "explorer.navigate", winId: id, path: state.path, from });
    }
    if (pushHist) {
      state.history = state.history.slice(0, state.histIndex + 1);
      if (state.history[state.history.length - 1] !== state.path) {
        state.history.push(state.path);
        state.histIndex = state.history.length - 1;
      }
    }
    state.treeChildren.set(state.path, res.data.folderList);
    await ensureTreePath(state.path);
    renderCrumb();
    renderTree();
    updateNavButtons();
    render();
    const title = pathBaseName(state.path);
    const icon = state.path === "/" ? HOME_ICON : FOLDER_ICON;
    win.title = title;
    win.icon = icon;
    win.el.querySelector(".win-title").innerHTML =
      `<span class="path-ico" style="background-image:url('${icon}')"></span><span>${escapeHtml(title)}</span>`;
    WM.renderTaskbar();
    if (!skipSync) {
      const jobs = [];
      window.FMExplorerViews.forEach((other) => {
        if (other.winId === id) return;
        jobs.push(Promise.resolve(other.refreshPaths([state.path])));
      });
      if (state.path === window.FMEnv.desktop && typeof window.FMRefreshDesktop === "function") {
        jobs.push(Promise.resolve(window.FMRefreshDesktop()));
      }
      if (jobs.length) await Promise.all(jobs);
    }
  }

  function itemsPaintKey() {
    const first = state.items[0] ? state.items[0].path : "";
    const last = state.items.length ? state.items[state.items.length - 1].path : "";
    return `${state.path}|${state.searching ? 1 : 0}|${state.items.length}|${first}|${last}`;
  }

  function makeFileItem(item, index) {
    const el = document.createElement("div");
    el.className = "file-item" + (state.selected.has(item.path) ? " selected" : "");
    el.dataset.path = item.path;
    el.dataset.index = String(index);
    el.dataset.type = item.type;
    el.draggable = true;
    el.innerHTML = `
      <div class="fico"></div>
      <div class="fname">${escapeHtml(item.name)}</div>
    `;
    setFileArtwork(el.querySelector(".fico"), item);
    return el;
  }

  function makeFileRow(item, index) {
    const tr = document.createElement("tr");
    tr.className = state.selected.has(item.path) ? "selected" : "";
    tr.dataset.path = item.path;
    tr.dataset.index = String(index);
    tr.dataset.type = item.type;
    tr.draggable = true;
    tr.innerHTML = `
      <td><span class="lico" style="background-image:url('${fileIcon(item)}')"></span>${escapeHtml(item.name)}</td>
      <td>${formatTime(item.modifyTime)}</td>
      <td>${escapeHtml(typeLabel(item))}</td>
      <td>${item.type === "folder" ? "-" : formatSize(item.size)}</td>
    `;
    return tr;
  }

  function iconCols() {
    return Math.max(1, Math.floor(Math.max(state.iconCellW, content.clientWidth - 20) / state.iconCellW));
  }

  function paintIconWindow(box) {
    const key = itemsPaintKey();
    if (box.dataset.paintKey !== key) {
      box.innerHTML = "";
      box.dataset.paintKey = key;
    }
    const cols = iconCols();
    const rows = Math.ceil(state.items.length / cols);
    box.style.display = "block";
    box.style.position = "relative";
    box.style.height = `${rows * state.iconCellH + 20}px`;
    const top = content.scrollTop;
    const viewH = content.clientHeight || 400;
    const rowStart = Math.max(0, Math.floor(top / state.iconCellH) - 2);
    const rowEnd = Math.min(rows, Math.ceil((top + viewH) / state.iconCellH) + 2);
    const start = rowStart * cols;
    const end = Math.min(state.items.length, rowEnd * cols);
    const keep = new Set();
    for (let i = start; i < end; i += 1) keep.add(String(i));
    [...box.querySelectorAll(".file-item")].forEach((el) => {
      if (!keep.has(el.dataset.index)) el.remove();
    });
    const have = new Map();
    [...box.querySelectorAll(".file-item")].forEach((el) => have.set(el.dataset.index, el));
    const frag = document.createDocumentFragment();
    for (let i = start; i < end; i += 1) {
      const item = state.items[i];
      let el = have.get(String(i));
      if (!el) {
        el = makeFileItem(item, i);
        frag.appendChild(el);
      } else if (el.dataset.path !== item.path) {
        el.remove();
        el = makeFileItem(item, i);
        frag.appendChild(el);
      }
      el.classList.toggle("selected", state.selected.has(item.path));
      el.style.position = "absolute";
      el.style.left = `${10 + (i % cols) * state.iconCellW}px`;
      el.style.top = `${10 + Math.floor(i / cols) * state.iconCellH}px`;
    }
    if (frag.childNodes.length) box.appendChild(frag);
  }

  function paintListWindow(table) {
    const tbody = table.querySelector("tbody");
    const top = content.scrollTop;
    const viewH = content.clientHeight || 400;
    const start = Math.max(0, Math.floor(top / state.listRowH) - 4);
    const end = Math.min(state.items.length, Math.ceil((top + viewH) / state.listRowH) + 4);
    const key = `${itemsPaintKey()}|${start}|${end}`;
    if (table.dataset.paintKey === key) {
      [...tbody.querySelectorAll("tr[data-path]")].forEach((el) => {
        el.classList.toggle("selected", state.selected.has(el.dataset.path));
      });
      return;
    }
    table.dataset.paintKey = key;
    const frag = document.createDocumentFragment();
    const topPad = document.createElement("tr");
    topPad.className = "virt-top";
    topPad.innerHTML = `<td colspan="${SORT_COLUMNS.length}" style="padding:0;border:0;height:${start * state.listRowH}px"></td>`;
    frag.appendChild(topPad);
    for (let i = start; i < end; i += 1) {
      frag.appendChild(makeFileRow(state.items[i], i));
    }
    const botPad = document.createElement("tr");
    botPad.className = "virt-bot";
    botPad.innerHTML = `<td colspan="${SORT_COLUMNS.length}" style="padding:0;border:0;height:${Math.max(0, (state.items.length - end) * state.listRowH)}px"></td>`;
    frag.appendChild(botPad);
    tbody.innerHTML = "";
    tbody.appendChild(frag);
  }

  function render() {
    if (!state.items.length) {
      content.innerHTML = `<div class="empty-tip">${state.searching ? "无搜索结果" : "文件夹为空"}</div>`;
      stLeft.textContent = state.searching ? "搜索到 0 项" : "0 个项目";
      updateNavButtons();
      return;
    }
    if (state.view === "icon") {
      let box = content.querySelector(".view-icon");
      if (!box) {
        content.innerHTML = "";
        box = document.createElement("div");
        box.className = "view-icon";
        content.appendChild(box);
      }
      paintIconWindow(box);
    } else {
      let table = content.querySelector("table.view-list");
      if (!table) {
        content.innerHTML = "";
        table = document.createElement("table");
        table.className = "view-list";
        const ths = SORT_COLUMNS.map((c) =>
          `<th data-sort="${c.key}"${c.width ? ` style="width:${c.width}"` : ""}><span class="th-label">${c.label}</span><span class="sort-mark"></span></th>`
        ).join("");
        table.innerHTML = `<thead><tr>${ths}</tr></thead><tbody></tbody>`;
        content.appendChild(table);
      }
      paintSortHeader(table);
      paintListWindow(table);
    }
    stLeft.textContent = selectionStatus();
    updateNavButtons();
  }

  function selectionStatus() {
    const base = state.searching ? `搜索到 ${state.items.length} 项` : `${state.items.length} 个项目`;
    if (!state.selected.size) return base;
    let text = `${base}，已选 ${state.selected.size}`;
    if (state.selected.size === 1) {
      const item = state.items.find((it) => state.selected.has(it.path));
      if (item && item.type === "file") text += `，${formatSize(item.size)}`;
    }
    return text;
  }

  function updateSelectionUI() {
    content.querySelectorAll(".file-item, tr[data-path]").forEach((el) => {
      el.classList.toggle("selected", state.selected.has(el.dataset.path));
    });
    stLeft.textContent = selectionStatus();
  }

  function clearDropTargets() {
    root.querySelectorAll(".drop-target").forEach((n) => n.classList.remove("drop-target"));
    content.classList.remove("dragover");
  }

  function bindPathDropZone(container, selector) {
    container.addEventListener("dragover", (e) => {
      const node = e.target.closest(selector);
      if (!node) return;
      const dest = node.dataset.path;
      if (!fmDragCanDrop(e, dest)) return;
      e.preventDefault();
      e.stopPropagation();
      e.dataTransfer.dropEffect = fmDragIsInternal(e) ? fmDragEffect(e) : "copy";
      container.querySelectorAll(".drop-target").forEach((n) => {
        if (n !== node) n.classList.remove("drop-target");
      });
      node.classList.add("drop-target");
    });
    container.addEventListener("dragleave", (e) => {
      const node = e.target.closest(selector);
      if (node) node.classList.remove("drop-target");
    });
    container.addEventListener("drop", (e) => {
      const node = e.target.closest(selector);
      if (!node) return;
      const dest = node.dataset.path;
      if (!fmDragCanDrop(e, dest)) return;
      e.preventDefault();
      e.stopPropagation();
      node.classList.remove("drop-target");
      fmDropInto(e, dest).catch((err) => toast(err.message || String(err)));
    });
  }

  function hitItem(e) {
    const el = e.target.closest(".file-item, tr[data-path]");
    if (!el || !content.contains(el)) return null;
    const index = Number(el.dataset.index);
    const item = state.items[index];
    if (!item) return null;
    return { el, item, index };
  }

  function scheduleRender() {
    if (content._virtRaf) return;
    content._virtRaf = requestAnimationFrame(() => {
      content._virtRaf = 0;
      render();
    });
  }

  function bindListDelegation() {
    content.addEventListener("scroll", scheduleRender, { passive: true });
    const sizeWatcher = new ResizeObserver(() => {
      if (!state.items.length) return;
      scheduleRender();
    });
    sizeWatcher.observe(content);
    state.sizeWatcher = sizeWatcher;
    content.addEventListener("click", (e) => {
      const th = e.target.closest("th[data-sort]");
      if (th && content.contains(th)) {
        e.stopPropagation();
        setSort(th.dataset.sort);
        return;
      }
      const hit = hitItem(e);
      if (!hit) return;
      if (e.button !== 0) return;
      e.stopPropagation();
      selectByClick(hit.index, e);
      updateSelectionUI();
    });
    content.addEventListener("dblclick", (e) => {
      if (fmIsTouchMode()) return;
      const hit = hitItem(e);
      if (!hit) return;
      e.preventDefault();
      e.stopPropagation();
      openItem(hit.item);
    });
    content.addEventListener("contextmenu", (e) => {
      if (fmIsTouchMode()) {
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      const hit = hitItem(e);
      if (!hit) return;
      e.preventDefault();
      e.stopPropagation();
      if (!state.selected.has(hit.item.path)) {
        state.selected.clear();
        state.selected.add(hit.item.path);
        state.lastIndex = hit.index;
        updateSelectionUI();
      }
      showContextMenu(e.clientX, e.clientY, selectedItems());
    });
    content.addEventListener("dragstart", (e) => {
      if (fmIsTouchMode()) {
        e.preventDefault();
        return;
      }
      const hit = hitItem(e);
      if (!hit) return;
      if (!state.selected.has(hit.item.path)) {
        state.selected.clear();
        state.selected.add(hit.item.path);
        state.lastIndex = hit.index;
        updateSelectionUI();
      }
      fmDragStart(e, [...state.selected]);
    });
    content.addEventListener("dragend", () => {
      window.FMDragState.paths = [];
      clearDropTargets();
    });
  }

  function selectByClick(index, e) {
    const item = state.items[index];
    if (e.ctrlKey || e.metaKey) {
      if (state.selected.has(item.path)) state.selected.delete(item.path);
      else state.selected.add(item.path);
      state.lastIndex = index;
      return;
    }
    if (e.shiftKey && state.lastIndex >= 0) {
      state.selected.clear();
      const a = Math.min(state.lastIndex, index);
      const b = Math.max(state.lastIndex, index);
      for (let i = a; i <= b; i += 1) state.selected.add(state.items[i].path);
      return;
    }
    state.selected.clear();
    state.selected.add(item.path);
    state.lastIndex = index;
  }

  function selectedItems() {
    return state.items.filter((it) => state.selected.has(it.path));
  }

  async function openItem(item) {
    if (item.type === "folder") {
      await load(item.path);
      return;
    }
    await openFile(item);
  }

  async function action(name, lockedItems) {
    try {
      const items = lockedItems || selectedItems();
      const paths = items.map((it) => it.path);
      if (name === "back") {
        if (state.histIndex <= 0) return;
        state.histIndex -= 1;
        await load(state.history[state.histIndex], false);
        return;
      }
      if (name === "forward") {
        if (state.histIndex >= state.history.length - 1) return;
        state.histIndex += 1;
        await load(state.history[state.histIndex], false);
        return;
      }
      if (name === "up") {
        await load(parentPath(state.path));
        return;
      }
      if (name === "refresh") {
        await FMNotifyFsChanged([state.path]);
        return;
      }
      if (name === "view-icon") {
        state.view = "icon";
        render();
        return;
      }
      if (name === "view-list") {
        state.view = "list";
        render();
        return;
      }
      if (name === "go") {
        const p = normalizePath(addr.value);
        await load(p);
        return;
      }
      if (name === "mkdir") {
        const name = await promptModal("新建文件夹", "新建文件夹");
        if (!name) return;
        await API.mkdir(state.path, name);
        await FMNotifyFsChanged([state.path]);
        return;
      }
      if (name === "mkfile") {
        const name = await promptModal("新建文件", "新建文件.txt");
        if (!name) return;
        await API.mkfile(state.path, name, "");
        await FMNotifyFsChanged([state.path]);
        return;
      }
      if (name === "upload") {
        Uploader.open(state.path);
        return;
      }
      if (name === "download") {
        if (!items.length) throw new Error("请选择要下载的项目");
        await Downloader.download(items);
        return;
      }
      if (name === "copy") {
        if (!paths.length) throw new Error("请先选择项目");
        AppClipboard.mode = "copy";
        AppClipboard.paths = paths;
        toast(`已复制 ${paths.length} 项`);
        return;
      }
      if (name === "cut") {
        if (!paths.length) throw new Error("请先选择项目");
        AppClipboard.mode = "cut";
        AppClipboard.paths = paths;
        toast(`已剪切 ${paths.length} 项`);
        return;
      }
      if (name === "paste") {
        await pasteIntoPath(state.path);
        return;
      }
      if (name === "copypath") {
        fmCopyPaths(items);
        return;
      }
      if (name === "props") {
        await fmShowProps(items);
        return;
      }
      if (name === "rename") {
        if (items.length !== 1) throw new Error("请选择一个项目进行重命名");
        const name = await promptModal("重命名", items[0].name);
        if (!name || name === items[0].name) return;
        await API.rename(items[0].path, name);
        await FMNotifyFsChanged([parentPath(items[0].path)]);
        return;
      }
      if (name === "delete") {
        if (!paths.length) throw new Error("请先选择项目");
        const okDel = await confirmModal("删除", `确定删除选中的 ${paths.length} 项吗？此操作不可恢复。`);
        if (!okDel) return;
        await API.remove(paths);
        await FMNotifyFsChanged([state.path]);
        return;
      }
      if (name === "zip") {
        if (!paths.length) throw new Error("请先选择要压缩的项目");
        const name = await promptModal("压缩为", "archive.zip");
        if (!name) return;
        await API.zip(paths, state.path, name);
        await FMNotifyFsChanged([state.path]);
        return;
      }
      if (name === "unzip") {
        if (items.length !== 1 || items[0].type !== "file" || !fmIsArchiveName(items[0].name)) throw new Error("请选择一个压缩包");
        if (!window.FMEnv.has7z) throw new Error("7z is not installed");
        await FMArchiveExtract(items[0], state.path);
        return;
      }
      if (name === "search") {
        const keyword = searchInput.value.trim();
        if (!keyword) {
          await load(state.path, false);
          return;
        }
        const res = await API.search(state.path, keyword);
        state.items = [...res.data.folderList, ...res.data.fileList];
        applySort();
        state.selected.clear();
        state.searching = true;
        render();
      }
    } catch (err) {
      toast(err.message || String(err));
    }
  }

  function showContextMenu(x, y, items) {
    const menu = document.getElementById("ctx-menu");
    items = items || [];
    let buttons = [];
    if (!items.length) {
      buttons = [
        ["刷新", () => action("refresh")],
        ["粘贴", () => action("paste")],
        ["-", null],
        ["新建文件夹", () => action("mkdir")],
        ["新建文件", () => action("mkfile")],
        ["上传文件", () => action("upload")],
        ["-", null],
        ["在此处打开终端", () => FMTerminal.open(state.path)],
        ["-", null],
        ["图标视图", () => action("view-icon")],
        ["列表视图", () => action("view-list")],
      ];
    } else if (items.length === 1) {
      buttons.push(["打开", () => openItem(items[0])]);
      if (items[0].type === "folder") {
        buttons.push(["在此处打开终端", () => FMTerminal.open(items[0].path)]);
      }
      if (items[0].type === "file") {
        const apps = kodApp.listByExt(items[0].ext);
        if (apps.length) {
          buttons.push(["-", null]);
          apps.forEach((app) => {
            buttons.push([`用${app.title}打开`, () => openFile(items[0], app.name)]);
          });
        }
      }
      buttons.push(
        ["-", null],
        ["下载", () => action("download", items)],
        ["-", null],
        ["复制", () => action("copy", items)],
        ["剪切", () => action("cut", items)],
        ["粘贴", () => action("paste")],
        ["复制路径", () => action("copypath", items)],
        ["-", null],
        ["重命名", () => action("rename", items)],
        ["删除", () => action("delete", items)],
        ["-", null],
        ["压缩", () => action("zip", items)],
      );
      if (window.FMEnv.has7z && items[0].type === "file" && fmIsArchiveName(items[0].name)) {
        buttons.push(["解压", () => action("unzip", items)]);
      }
      buttons.push(["-", null], ["属性", () => action("props", items)]);
    } else {
      buttons = [
        ["复制", () => action("copy", items)],
        ["剪切", () => action("cut", items)],
        ["复制路径", () => action("copypath", items)],
        ["下载", () => action("download", items)],
        ["-", null],
        ["删除", () => action("delete", items)],
        ["-", null],
        ["压缩", () => action("zip", items)],
        ["-", null],
        ["属性", () => action("props", items)],
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
        await fn();
      };
      menu.appendChild(btn);
    });
    menu.classList.remove("hidden");
    const rect = menu.getBoundingClientRect();
    const leftPos = Math.min(x, window.innerWidth - rect.width - 4);
    const top = Math.min(y, window.innerHeight - rect.height - 4);
    menu.style.left = `${leftPos}px`;
    menu.style.top = `${top}px`;
  }

  root.querySelector(".frame-header").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn || btn.classList.contains("disable")) return;
    action(btn.dataset.act).catch(() => {});
  });
  root.querySelector(".tools").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    action(btn.dataset.act).catch(() => {});
  });

  addrCrumb.addEventListener("click", (e) => {
    const item = e.target.closest("[data-path]");
    if (!item) return;
    e.preventDefault();
    e.stopPropagation();
    load(item.dataset.path).catch((err) => toast(err.message || String(err)));
  });
  addrWrap.addEventListener("click", (e) => {
    if (e.target.closest("[data-path]")) return;
    editAddress();
  });
  addr.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      action("go").catch(() => {});
    }
    if (e.key === "Escape") renderCrumb();
  });
  addr.addEventListener("blur", () => {
    if (!addrInputWrap.classList.contains("hidden")) renderCrumb();
  });

  searchInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") action("search").catch(() => {});
  });
  searchInput.addEventListener("input", updateSearchClear);
  searchClear.addEventListener("click", () => {
    searchInput.value = "";
    updateSearchClear();
    load(state.path, false).catch((err) => toast(err.message || String(err)));
  });

  treeToggle.onclick = () => {
    state.treeHidden = !state.treeHidden;
    syncTreeLayout();
  };
  treeDrag.onpointerdown = (e) => {
    e.preventDefault();
    treeDrag.setPointerCapture(e.pointerId);
    const startX = e.clientX;
    const startW = state.treeWidth;
    const onMove = (ev) => {
      state.treeWidth = Math.max(160, Math.min(400, startW + (ev.clientX - startX)));
      syncTreeLayout();
    };
    const onUp = () => {
      treeDrag.removeEventListener("pointermove", onMove);
      treeDrag.removeEventListener("pointerup", onUp);
      treeDrag.removeEventListener("pointercancel", onUp);
    };
    treeDrag.addEventListener("pointermove", onMove);
    treeDrag.addEventListener("pointerup", onUp);
    treeDrag.addEventListener("pointercancel", onUp);
  };

  treeEl.addEventListener("click", async (e) => {
    try {
      const sw = e.target.closest("[data-switch]");
      if (sw) {
        e.preventDefault();
        e.stopPropagation();
        await toggleTree(sw.dataset.switch);
        return;
      }
      const node = e.target.closest(".tree-node");
      if (!node) return;
      await load(node.dataset.path);
    } catch (err) {
      toast(err.message || String(err));
    }
  });

  content.addEventListener("contextmenu", (e) => {
    if (fmIsTouchMode()) {
      e.preventDefault();
      return;
    }
    if (e.target.closest(".file-item, tr[data-path]")) return;
    e.preventDefault();
    state.selected.clear();
    updateSelectionUI();
    showContextMenu(e.clientX, e.clientY);
  });
  let skipBlankClick = false;
  content.addEventListener("click", (e) => {
    if (e.target.closest(".file-item, tr[data-path], thead")) return;
    if (skipBlankClick) {
      skipBlankClick = false;
      return;
    }
    state.selected.clear();
    updateSelectionUI();
  });

  function rectsIntersect(a, b) {
    return !(a.right < b.left || a.left > b.right || a.bottom < b.top || a.top > b.bottom);
  }

  function bindMarqueeSelect() {
    content.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return;
      if (e.target.closest(".file-item, tr[data-path], thead, .select-container")) return;
      const startClientX = e.clientX;
      const startClientY = e.clientY;
      const crect0 = content.getBoundingClientRect();
      const startX = startClientX - crect0.left + content.scrollLeft;
      const startY = startClientY - crect0.top + content.scrollTop;
      const keep = e.ctrlKey || e.metaKey;
      const base = keep ? new Set(state.selected) : new Set();
      let moved = false;
      let boxEl = null;
      const onMove = (ev) => {
        const dx = ev.clientX - startClientX;
        const dy = ev.clientY - startClientY;
        if (!moved && Math.abs(dx) < 4 && Math.abs(dy) < 4) return;
        if (!moved) {
          moved = true;
          if (!keep) {
            state.selected.clear();
          }
          boxEl = document.createElement("div");
          boxEl.className = "select-container";
          content.appendChild(boxEl);
          content.classList.add("selecting");
        }
        const crect = content.getBoundingClientRect();
        const curX = ev.clientX - crect.left + content.scrollLeft;
        const curY = ev.clientY - crect.top + content.scrollTop;
        const left = Math.min(startX, curX);
        const top = Math.min(startY, curY);
        const width = Math.abs(curX - startX);
        const height = Math.abs(curY - startY);
        boxEl.style.left = `${left}px`;
        boxEl.style.top = `${top}px`;
        boxEl.style.width = `${width}px`;
        boxEl.style.height = `${height}px`;
        const selRect = {
          left: crect.left + left - content.scrollLeft,
          top: crect.top + top - content.scrollTop,
          right: crect.left + left - content.scrollLeft + width,
          bottom: crect.top + top - content.scrollTop + height,
        };
        const next = new Set(base);
        if (state.view === "icon") {
          const cols = iconCols();
          state.items.forEach((item, i) => {
            const r = {
              left: crect.left + 10 + (i % cols) * state.iconCellW - content.scrollLeft,
              top: crect.top + 10 + Math.floor(i / cols) * state.iconCellH - content.scrollTop,
            };
            r.right = r.left + 96;
            r.bottom = r.top + 104;
            if (rectsIntersect(selRect, r)) {
              if (keep && base.has(item.path)) next.delete(item.path);
              else next.add(item.path);
            }
          });
        } else {
          state.items.forEach((item, i) => {
            const r = {
              left: crect.left,
              top: crect.top + 33 + i * state.listRowH - content.scrollTop,
            };
            r.right = r.left + crect.width;
            r.bottom = r.top + state.listRowH;
            if (rectsIntersect(selRect, r)) {
              if (keep && base.has(item.path)) next.delete(item.path);
              else next.add(item.path);
            }
          });
        }
        state.selected = next;
        updateSelectionUI();
      };
      const onUp = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        if (boxEl) boxEl.remove();
        content.classList.remove("selecting");
        if (moved) skipBlankClick = true;
      };
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    });
  }
  bindMarqueeSelect();

  function bindTouch() {
    const touch = {
      mode: "idle",
      hit: null,
      startX: 0,
      startY: 0,
      lastX: 0,
      lastY: 0,
      timer: null,
      ghost: null,
      lastTapPath: "",
      lastTapTime: 0,
    };
    const resetTouch = (keepTap) => {
      if (touch.timer) clearTimeout(touch.timer);
      if (touch.ghost) touch.ghost.remove();
      if (touch.hit) touch.hit.el.classList.remove("is-touch-armed");
      fmTouchHighlight(null);
      touch.mode = "idle";
      touch.hit = null;
      touch.timer = null;
      touch.ghost = null;
      if (!keepTap) {
        touch.lastTapPath = "";
        touch.lastTapTime = 0;
      }
    };
    content.addEventListener("touchstart", (e) => {
      fmMarkTouchInput();
      if (e.touches.length !== 1) {
        resetTouch(false);
        return;
      }
      document.getElementById("ctx-menu").classList.add("hidden");
      resetTouch(true);
      const t = e.touches[0];
      const hit = hitItem(e);
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
          if (!state.selected.has(hit.item.path)) {
            state.selected.clear();
            state.selected.add(hit.item.path);
            state.lastIndex = hit.index;
            updateSelectionUI();
          }
          hit.el.classList.add("is-touch-armed");
        }
        if (navigator.vibrate) navigator.vibrate(30);
      }, 500);
    }, { passive: true });
    content.addEventListener("touchmove", (e) => {
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
        touch.ghost = fmTouchGhost(touch.hit.item.name, state.selected.size);
      }
      if (touch.mode === "dragging") {
        fmTouchMoveGhost(touch.ghost, t.clientX, t.clientY);
        const drop = fmTouchDropHitAt(t.clientX, t.clientY);
        fmTouchHighlight(drop.path && fmPathsCanDrop([...state.selected], drop.path) ? drop : null);
      }
    }, { passive: false });
    content.addEventListener("touchend", (e) => {
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
        if (hit && touch.lastTapPath === hit.item.path && now - touch.lastTapTime < 350) {
          resetTouch(false);
          openItem(hit.item).catch((err) => toast(err.message || String(err)));
          return;
        }
        state.selected.clear();
        if (hit) {
          state.selected.add(hit.item.path);
          state.lastIndex = hit.index;
        }
        updateSelectionUI();
        resetTouch(false);
        if (hit) {
          touch.lastTapPath = hit.item.path;
          touch.lastTapTime = now;
        }
        return;
      }
      if (mode === "armed") {
        if (!hit) {
          state.selected.clear();
          updateSelectionUI();
        }
        resetTouch(false);
        showContextMenu(x, y, hit ? selectedItems() : []);
        return;
      }
      const paths = [...state.selected];
      const dest = fmTouchDropTargetAt(x, y);
      resetTouch(false);
      if (dest && fmPathsCanDrop(paths, dest)) {
        fmTransferPaths(paths, dest, false).catch((err) => toast(err.message || String(err)));
      }
    }, { passive: false });
    content.addEventListener("touchcancel", () => {
      fmMarkTouchInput();
      resetTouch(false);
    }, { passive: true });
  }
  bindTouch();

  content.addEventListener("dragover", (e) => {
    const hit = hitItem(e);
    if (hit && hit.item.type === "folder" && fmDragCanDrop(e, hit.item.path)) {
      e.preventDefault();
      e.stopPropagation();
      e.dataTransfer.dropEffect = fmDragIsInternal(e) ? fmDragEffect(e) : "copy";
      content.classList.remove("dragover");
      content.querySelectorAll(".drop-target").forEach((n) => {
        if (n !== hit.el) n.classList.remove("drop-target");
      });
      hit.el.classList.add("drop-target");
      return;
    }
    if (!fmDragCanDrop(e, state.path)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = fmDragIsInternal(e) ? fmDragEffect(e) : "copy";
    content.classList.add("dragover");
  });
  content.addEventListener("dragleave", () => content.classList.remove("dragover"));
  content.addEventListener("drop", (e) => {
    const hit = hitItem(e);
    if (hit && hit.item.type === "folder" && fmDragCanDrop(e, hit.item.path)) {
      e.preventDefault();
      e.stopPropagation();
      hit.el.classList.remove("drop-target");
      fmDropInto(e, hit.item.path).catch((err) => toast(err.message || String(err)));
      return;
    }
    if (!fmDragCanDrop(e, state.path)) return;
    e.preventDefault();
    content.classList.remove("dragover");
    fmDropInto(e, state.path).catch((err) => {
      toast(err.message || String(err));
    });
  });
  bindListDelegation();
  bindPathDropZone(treeEl, ".tree-node[data-path]");
  bindPathDropZone(addrCrumb, "a[data-path]");

  root.addEventListener("mousedown", () => root.focus());
  root.addEventListener("paste", (e) => {
    if (e.target.matches("input, textarea")) return;
    e.preventDefault();
    pasteIntoPath(state.path, e.clipboardData).catch((err) => {
      toast(err.message || String(err));
    });
  });

  root.addEventListener("keydown", (e) => {
    if (e.target.matches("input, textarea")) return;
    if (e.key === "F5") {
      e.preventDefault();
      action("refresh").catch(() => {});
    }
    if (e.key === "Backspace") {
      e.preventDefault();
      action("up").catch(() => {});
    }
    if (e.key === "Delete") action("delete").catch(() => {});
    if (e.key === "F2") action("rename").catch(() => {});
    if (e.ctrlKey && e.key.toLowerCase() === "c") action("copy").catch(() => {});
    if (e.ctrlKey && e.key.toLowerCase() === "x") action("cut").catch(() => {});
    if (e.ctrlKey && e.key.toLowerCase() === "a") {
      state.items.forEach((it) => state.selected.add(it.path));
      updateSelectionUI();
    }
  });
  root.tabIndex = 0;
  root.focus();
  syncTreeLayout();
  updateSearchClear();
  load(startPath).catch((err) => toast(err.message || String(err)));
  return win;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
