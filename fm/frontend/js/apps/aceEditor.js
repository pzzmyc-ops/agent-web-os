(function () {
  const ACE_BASE = "/assets/kod/app/vender/ace/src-min-noconflict/";
  const MD_BASE = "/assets/kod/app/vender/markdown/";
  const EXT = [
    "txt", "md", "json", "xml", "html", "htm", "css", "js", "ts", "py", "java",
    "c", "cpp", "h", "hpp", "go", "rs", "php", "rb", "sh", "bat", "ps1", "ini",
    "conf", "log", "csv", "yml", "yaml", "toml", "sql", "vue", "jsx", "tsx",
    "gitignore", "env",
  ];
  const THEMES = [
    "tomorrow", "chrome", "github", "textmate", "xcode", "eclipse",
    "monokai", "dracula", "twilight", "ambiance", "tomorrow_night", "solarized_dark",
  ];
  const FONT_SIZES = [12, 13, 14, 16, 18, 20, 22];
  const MD_SNIPPETS = {
    bold: ["**", "**"],
    italic: ["*", "*"],
    strikethrough: ["~~", "~~"],
    h1: ["# ", ""],
    line: ["\n------\n", ""],
    quote: ["> ", ""],
    list_order: ["1. ", ""],
    list_unorder: ["- ", ""],
    link: ["[", "](url)"],
    image: ["![", "](url)"],
    file: ["[", "](path)"],
    code: ["```\n", "\n```"],
    table: ["\n| A | B |\n| --- | --- |\n| 1 | 2 |\n", ""],
    math: ["$$\n", "\n$$"],
    flowchart: ["```mermaid\nflowchart TD\n  A-->B\n```\n", ""],
    sequence: ["```mermaid\nsequenceDiagram\n  A->>B: hi\n```\n", ""],
    gantt: ["```mermaid\ngantt\n  title Demo\n  section A\n  Task: 2024-01-01, 3d\n```\n", ""],
    classDiagram: ["```mermaid\nclassDiagram\n  class Animal\n```\n", ""],
  };

  let aceReady = null;
  let mdReady = null;
  let editorUI = null;

  function esc(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function ensureAce() {
    if (aceReady) return aceReady;
    aceReady = (async () => {
      Loader.loadCss("/css/editor.css?v=12");
      Loader.loadCss("/assets/kod/style/lib/font-icon/style.css");
      await Loader.loadJs(`${ACE_BASE}ace.js`);
      window.ace.config.set("basePath", ACE_BASE);
      await Promise.all([
        Loader.loadJs(`${ACE_BASE}ext-language_tools.js`),
        Loader.loadJs(`${ACE_BASE}ext-modelist.js`),
        Loader.loadJs(`${ACE_BASE}ext-searchbox.js`),
      ]);
      return window.ace;
    })();
    return aceReady;
  }

  async function ensureMarkdown() {
    if (mdReady) return mdReady;
    mdReady = (async () => {
      await Loader.loadJs(`${MD_BASE}markdown-it.min.js`);
      await Loader.loadJs(`${MD_BASE}highlight.min.js`);
      const md = window.markdownit({
        html: false,
        linkify: true,
        breaks: true,
        highlight(str, lang) {
          if (window.hljs && lang && window.hljs.getLanguage(lang)) {
            return window.hljs.highlight(str, { language: lang }).value;
          }
          return "";
        },
      });
      return md;
    })();
    return mdReady;
  }

  function detectMode(aceLib, name) {
    const modelist = aceLib.require("ace/ext/modelist");
    var mode = modelist.getModeForPath(name).mode;
    // ACE 内置 modelist 不识别 .jsonl / .mdx 等后缀,手动补
    if (mode === "ace/mode/text") {
      var lower = String(name || "").toLowerCase();
      if (lower.endsWith(".jsonl")) mode = "ace/mode/json";
    }
    return mode;
  }

  function displayPath(path) {
    if (!path || path === "/") return "/";
    return path;
  }

  function createEditorUI(aceLib) {
    const root = document.createElement("div");
    root.className = "frame-main-editor menu-show-parent";
    root.innerHTML = `
      <div class="menu-show-toggle" title="收起/展开目录"><i class="font-icon ri-indent-increase"></i></div>
      <div class="frame-left" style="width:220px">
        <div class="tree-header"><div class="header-address-content"></div></div>
        <ul class="editor-tree"></ul>
      </div>
      <div class="drag-resize drag-resize-tree"><span class="drag-item"></span></div>
      <div class="frame-right">
        <div class="edit-main code-theme-light">
          <div class="tools">
            <div class="left top-toolbar">
              <a href="javascript:;" data-act="save" title="保存(Ctrl-S)"><i class="font-icon ri-save-line-3"></i></a>
              <a href="javascript:;" data-act="saveAll" title="保存全部"><i class="font-icon ri-save-fill-3"></i></a>
              <span class="line"></span>
              <a href="javascript:;" data-act="historyBack" title="后退" class="disable opacity-20"><i class="font-icon ri-arrow-left-line"></i></a>
              <a href="javascript:;" data-act="historyNext" title="前进" class="disable opacity-20"><i class="font-icon ri-arrow-right-line"></i></a>
              <span class="line"></span>
              <a href="javascript:;" data-act="undo" title="撤销(Ctrl-Z)"><i class="font-icon ri-arrow-go-back-line"></i></a>
              <a href="javascript:;" data-act="redo" title="反撤销(Ctrl-Y)"><i class="font-icon ri-arrow-go-forward-line"></i></a>
              <a href="javascript:;" data-act="refresh" title="刷新(F5)"><i class="font-icon ri-refresh-line"></i></a>
              <span class="line"></span>
              <a href="javascript:;" data-act="goto" title="跳转到行(Ctrl-L)"><i class="font-icon ri-map-pin-line"></i></a>
              <a href="javascript:;" data-act="search" title="搜索(Ctrl-F)"><i class="font-icon ri-search-line"></i></a>
              <a href="javascript:;" data-act="searchReplace" title="替换"><i class="font-icon ri-find-replace-line"></i></a>
              <span class="line"></span>
              <a href="javascript:;" class="toolbar-menu" data-menu="font" title="字体大小"><i class="font-icon ri-font-size"></i><i class="font-icon ri-arrow-down-s-fill"></i></a>
              <span class="line"></span>
              <a href="javascript:;" class="toolbar-menu" data-menu="theme" title="代码风格"><i class="font-icon ri-magic-line"></i><i class="font-icon ri-arrow-down-s-fill"></i></a>
              <a href="javascript:;" class="toolbar-menu" data-menu="edit" title="编辑"><i class="font-icon ri-edit-line"></i><i class="font-icon ri-arrow-down-s-fill"></i></a>
              <a href="javascript:;" class="toolbar-menu" data-menu="setting" title="设置"><i class="font-icon ri-settings-line-5"></i><i class="font-icon ri-arrow-down-s-fill"></i></a>
            </div>
            <div class="right">
              <button type="button" data-act="close" class="btn btn-xs btn-default" title="关闭"><i class="font-icon ri-close-line"></i></button>
              <button type="button" data-act="fullscreen" class="btn btn-xs btn-default" title="全屏"><i class="font-icon ri-fullscreen-line"></i></button>
            </div>
            <div class="toolbar-dropdown hidden" data-drop="font"></div>
            <div class="toolbar-dropdown hidden" data-drop="theme"></div>
            <div class="toolbar-dropdown hidden" data-drop="edit"></div>
            <div class="toolbar-dropdown hidden" data-drop="setting"></div>
          </div>
          <div class="frame-editor-left">
            <div class="edit-tab"><div class="tabs"></div></div>
            <div class="edit-body">
              <pre class="ace-editor-content"></pre>
              <div class="drag-resize editor-preview-resize"><span class="drag-item"></span></div>
              <div class="edit-right-frame" style="width:40%">
                <div class="editor-preview preview-markdown-frame">
                  <div class="preview-markdown-tool">
                    <div class="toolbar"><div class="content md-tools-bar"></div></div>
                    <div class="box"><div class="content">
                      <a href="javascript:;" data-md="toggle-markdown" title="预览"><i class="font-icon ri-layout-column-line"></i></a>
                      <a href="javascript:;" data-md="toggle-edit" title="仅预览"><i class="font-icon ri-eye-line"></i></a>
                    </div></div>
                  </div>
                  <div class="markdown-preview-content"><div class="markdown-preview"></div></div>
                </div>
              </div>
            </div>
          </div>
          <div class="bottom-toolbar">
            <a href="javascript:;" class="editor_position">1:1</a>
            <a href="javascript:;" class="file-mode">Text</a>
            <a href="javascript:;" class="file-charset">utf-8</a>
            <a href="javascript:;" class="config-tab">Tabs:4</a>
          </div>
        </div>
      </div>
    `;

    const mdBar = root.querySelector(".md-tools-bar");
    const mdTools = [
      ["bold", "ri-bold"], ["italic", "ri-italic"], ["strikethrough", "ri-strikethrough"],
      "|", ["h1", "ri-heading"], ["line", "ri-subtract-line"], ["quote", "ri-double-quotes-l"],
      ["list_order", "ri-list-ordered"], ["list_unorder", "ri-list-unordered"],
      ["link", "ri-link"], ["image", "ri-image-line"], ["file", "ri-file-line"],
      ["code", "ri-code-s-slash-line"], ["table", "ri-table-2"],
      "|", ["math", "ri-functions"], ["flowchart", "ri-mind-map"],
      ["sequence", "ri-flow-chart"], ["gantt", "ri-bar-chart-horizontal-fill"],
      ["classDiagram", "ri-map-line-2"],
    ];
    mdBar.innerHTML = mdTools.map((t) => {
      if (t === "|") return '<span class="md-tools md-tools-split">|</span>';
      return `<a href="javascript:;" class="md-tools" data-md-snip="${t[0]}"><i class="font-icon ${t[1]}"></i></a>`;
    }).join("");

    const fontDrop = root.querySelector('[data-drop="font"]');
    fontDrop.innerHTML = FONT_SIZES.map((n) => `<a href="javascript:;" data-font="${n}">${n}px</a>`).join("");
    const themeDrop = root.querySelector('[data-drop="theme"]');
    themeDrop.innerHTML = THEMES.map((t) => `<a href="javascript:;" data-theme="${t}">${t}</a>`).join("");
    root.querySelector('[data-drop="edit"]').innerHTML = `
      <a href="javascript:;" data-edit="wrap">自动换行</a>
      <a href="javascript:;" data-edit="download">下载当前文件</a>
    `;
    root.querySelector('[data-drop="setting"]').innerHTML = `
      <a href="javascript:;" data-set="tab2">Tab 宽度 2</a>
      <a href="javascript:;" data-set="tab4">Tab 宽度 4</a>
      <a href="javascript:;" data-set="softTab">空格缩进</a>
      <a href="javascript:;" data-set="hardTab">Tab 缩进</a>
    `;

    const aceEl = root.querySelector(".ace-editor-content");
    const editor = aceLib.edit(aceEl);
    editor.setTheme("ace/theme/tomorrow");
    editor.setShowPrintMargin(false);
    editor.setOptions({
      fontSize: "14px",
      wrap: true,
      enableBasicAutocompletion: true,
      enableLiveAutocompletion: true,
      enableSnippets: true,
      showLineNumbers: true,
      tabSize: 4,
    });

    const state = {
      root,
      editor,
      aceLib,
      win: null,
      tabs: [],
      activePath: null,
      treePath: "",
      history: [],
      histIndex: -1,
      histLock: false,
      previewRatio: 0.4,
      previewOn: true,
      editOnly: false,
      treeHidden: false,
      treeWidth: 220,
      wrap: true,
      tabSize: 4,
      useSoftTabs: true,
      theme: "tomorrow",
      fontSize: 14,
      md: null,
      previewTimer: 0,
    };

    bindUI(state);
    return state;
  }

  function bindUI(state) {
    const { root, editor } = state;
    const left = root.querySelector(".frame-left");
    const rightFrame = root.querySelector(".edit-right-frame");
    const acePre = root.querySelector(".ace-editor-content");
    const previewResize = root.querySelector(".editor-preview-resize");

    const treeToggle = root.querySelector(".menu-show-toggle");
    const syncTogglePos = () => {
      treeToggle.style.left = state.treeHidden ? "0px" : `${Math.max(0, state.treeWidth - 8)}px`;
    };
    treeToggle.onclick = () => {
      state.treeHidden = !state.treeHidden;
      root.classList.toggle("tree-hidden", state.treeHidden);
      syncTogglePos();
      editor.resize();
    };

    const treeDrag = root.querySelector(".drag-resize-tree");
    treeDrag.onmousedown = (e) => {
      e.preventDefault();
      const startX = e.clientX;
      const startW = state.treeWidth;
      const onMove = (ev) => {
        state.treeWidth = Math.max(140, Math.min(420, startW + (ev.clientX - startX)));
        left.style.width = `${state.treeWidth}px`;
        root.querySelector(".frame-right").style.left = `${state.treeWidth}px`;
        treeDrag.style.left = `${state.treeWidth}px`;
        syncTogglePos();
        editor.resize();
      };
      const onUp = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      };
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    };

    previewResize.onmousedown = (e) => {
      e.preventDefault();
      const body = root.querySelector(".edit-body");
      const onMove = (ev) => {
        const rect = body.getBoundingClientRect();
        const fromRight = (rect.right - ev.clientX) / rect.width;
        state.previewRatio = Math.max(0.2, Math.min(0.7, fromRight));
        applyPreviewLayout(state);
      };
      const onUp = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      };
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    };

    function placeDrop(name, anchor) {
      const drop = root.querySelector(`[data-drop="${name}"]`);
      const tools = root.querySelector(".tools");
      const tr = tools.getBoundingClientRect();
      const ar = anchor.getBoundingClientRect();
      drop.style.left = `${Math.max(4, ar.left - tr.left)}px`;
      drop.style.right = "auto";
      drop.style.top = "30px";
    }

    function toggleDrop(name, anchor) {
      root.querySelectorAll(".toolbar-dropdown").forEach((el) => {
        if (el.dataset.drop === name) {
          const willShow = el.classList.contains("hidden");
          el.classList.toggle("hidden");
          if (willShow) placeDrop(name, anchor);
        } else {
          el.classList.add("hidden");
        }
      });
    }

    root.querySelector(".tools").addEventListener("click", async (e) => {
      if (e.target.closest(".toolbar-dropdown")) {
        e.stopPropagation();
        return;
      }
      const dropLink = e.target.closest("[data-menu]");
      if (dropLink) {
        e.preventDefault();
        e.stopPropagation();
        toggleDrop(dropLink.dataset.menu, dropLink);
        return;
      }
      const actEl = e.target.closest("[data-act]");
      if (!actEl) return;
      hideDrops(root);
      const act = actEl.dataset.act;
      if (act === "save") await saveActive(state);
      if (act === "saveAll") await saveAll(state);
      if (act === "historyBack") historyGo(state, -1);
      if (act === "historyNext") historyGo(state, 1);
      if (act === "undo") editor.undo();
      if (act === "redo") editor.redo();
      if (act === "refresh") await refreshActive(state);
      if (act === "goto") editor.execCommand("gotoline");
      if (act === "search") editor.execCommand("find");
      if (act === "searchReplace") editor.execCommand("replace");
      if (act === "close") {
        if (state.activePath) await closeTab(state, state.activePath);
      }
      if (act === "fullscreen") WM.toggleMaximize(state.win.id);
    });

    root.querySelector('[data-drop="font"]').onclick = (e) => {
      e.stopPropagation();
      const a = e.target.closest("[data-font]");
      if (!a) return;
      state.fontSize = Number(a.dataset.font);
      editor.setFontSize(`${state.fontSize}px`);
      hideDrops(root);
    };
    root.querySelector('[data-drop="theme"]').onclick = (e) => {
      e.stopPropagation();
      const a = e.target.closest("[data-theme]");
      if (!a) return;
      state.theme = a.dataset.theme;
      editor.setTheme(`ace/theme/${state.theme}`);
      hideDrops(root);
    };
    root.querySelector('[data-drop="edit"]').onclick = (e) => {
      e.stopPropagation();
      const a = e.target.closest("[data-edit]");
      if (!a) return;
      if (a.dataset.edit === "wrap") {
        state.wrap = !state.wrap;
        editor.setOption("wrap", state.wrap);
        toast(state.wrap ? "已开启自动换行" : "已关闭自动换行");
      }
      if (a.dataset.edit === "download") {
        const tab = getActive(state);
        if (!tab) throw new Error("没有打开的文件");
        const link = document.createElement("a");
        link.href = API.downloadUrl(tab.item.path);
        link.download = tab.item.name;
        link.click();
      }
      hideDrops(root);
    };
    root.querySelector('[data-drop="setting"]').onclick = (e) => {
      e.stopPropagation();
      const a = e.target.closest("[data-set]");
      if (!a) return;
      if (a.dataset.set === "tab2") state.tabSize = 2;
      if (a.dataset.set === "tab4") state.tabSize = 4;
      if (a.dataset.set === "softTab") state.useSoftTabs = true;
      if (a.dataset.set === "hardTab") state.useSoftTabs = false;
      editor.session.setTabSize(state.tabSize);
      editor.session.setUseSoftTabs(state.useSoftTabs);
      root.querySelector(".config-tab").textContent = `Tabs:${state.tabSize}`;
      toast(a.dataset.set === "softTab" || a.dataset.set === "hardTab"
        ? (state.useSoftTabs ? "空格缩进" : "Tab 缩进")
        : `Tab 宽度 ${state.tabSize}`);
      hideDrops(root);
    };
    state.onDocClick = (e) => {
      if (e.target.closest(".frame-main-editor .tools")) return;
      hideDrops(root);
    };
    document.addEventListener("click", state.onDocClick);

    root.querySelector(".tabs").addEventListener("click", async (e) => {
      const close = e.target.closest(".tab-close");
      if (close) {
        e.stopPropagation();
        await closeTab(state, close.closest(".tab").dataset.path);
        return;
      }
      const tabEl = e.target.closest(".tab");
      if (tabEl) await activateTab(state, tabEl.dataset.path);
    });

    const onTreeClick = async (e) => {
      const a = e.target.closest("[data-tree-path]");
      if (!a) return;
      const path = a.dataset.treePath;
      const type = a.dataset.treeType;
      if (type === "folder") {
        state.treePath = path;
        await renderTree(state);
        return;
      }
      await openFileInEditor(state, {
        path,
        name: a.dataset.treeName,
        type: "file",
        ext: (a.dataset.treeName.split(".").pop() || "").toLowerCase(),
      });
    };
    root.querySelector(".editor-tree").addEventListener("click", onTreeClick);
    root.querySelector(".header-address-content").addEventListener("click", onTreeClick);

    root.querySelector(".md-tools-bar").onclick = (e) => {
      const a = e.target.closest("[data-md-snip]");
      if (!a) return;
      insertSnippet(state, a.dataset.mdSnip);
    };
    root.querySelector(".preview-markdown-tool").addEventListener("click", (e) => {
      const a = e.target.closest("[data-md]");
      if (!a) return;
      if (a.dataset.md === "toggle-markdown") {
        state.previewOn = !state.previewOn;
        applyPreviewLayout(state);
      }
      if (a.dataset.md === "toggle-edit") {
        state.editOnly = !state.editOnly;
        applyPreviewLayout(state);
      }
    });

    editor.selection.on("changeCursor", () => {
      const pos = editor.getCursorPosition();
      root.querySelector(".editor_position").textContent = `${pos.row + 1}:${pos.column + 1}`;
    });
    editor.session.on("change", () => {
      if (state.ignoreChange) return;
      const tab = getActive(state);
      if (!tab) return;
      tab.content = editor.getValue();
      tab.dirty = tab.content !== tab.saved;
      renderTabs(state);
      schedulePreview(state);
    });

    editor.commands.addCommand({
      name: "saveFile",
      bindKey: { win: "Ctrl-S", mac: "Command-S" },
      exec: () => { saveActive(state); },
    });

    applyPreviewLayout(state);
    left.style.width = `${state.treeWidth}px`;
    root.querySelector(".frame-right").style.left = `${state.treeWidth}px`;
    treeDrag.style.left = `${state.treeWidth}px`;
    syncTogglePos();
  }

  function hideDrops(root) {
    root.querySelectorAll(".toolbar-dropdown").forEach((el) => el.classList.add("hidden"));
  }

  function getActive(state) {
    return state.tabs.find((t) => t.item.path === state.activePath) || null;
  }

  function applyPreviewLayout(state) {
    const { root, editor } = state;
    const tab = getActive(state);
    const isMd = tab && String(tab.item.ext || "").toLowerCase() === "md";
    const show = isMd && state.previewOn && !state.editOnly;
    root.classList.toggle("has-markdown", !!isMd);
    root.classList.toggle("preview-off", !show);
    root.classList.toggle("edit-only", !!state.editOnly && !!isMd);
    const ratio = show ? state.previewRatio : 0;
    const acePre = root.querySelector(".ace-editor-content");
    const right = root.querySelector(".edit-right-frame");
    const drag = root.querySelector(".editor-preview-resize");
    if (show) {
      acePre.style.right = `${ratio * 100}%`;
      right.style.width = `${ratio * 100}%`;
      right.style.display = "";
      drag.style.right = `${ratio * 100}%`;
      drag.style.display = "";
    } else if (isMd && state.editOnly) {
      acePre.style.right = "0";
      right.style.display = "none";
      drag.style.display = "none";
    } else if (isMd && !state.previewOn) {
      acePre.style.right = "0";
      right.style.display = "none";
      drag.style.display = "none";
    } else {
      acePre.style.right = "0";
      right.style.display = "none";
      drag.style.display = "none";
    }
    editor.resize();
  }

  function schedulePreview(state) {
    const tab = getActive(state);
    if (!tab || String(tab.item.ext || "").toLowerCase() !== "md") return;
    clearTimeout(state.previewTimer);
    state.previewTimer = setTimeout(() => renderPreview(state), 180);
  }

  async function renderPreview(state) {
    const tab = getActive(state);
    if (!tab || String(tab.item.ext || "").toLowerCase() !== "md") return;
    if (!state.md) state.md = await ensureMarkdown();
    const html = state.md.render(state.editor.getValue());
    state.root.querySelector(".markdown-preview").innerHTML = html;
  }

  function insertSnippet(state, key) {
    const pair = MD_SNIPPETS[key];
    if (!pair) throw new Error(`未知 Markdown 工具: ${key}`);
    const { editor } = state;
    const selected = editor.getSelectedText();
    editor.insert(pair[0] + selected + pair[1]);
    editor.focus();
  }

  function renderTabs(state) {
    const box = state.root.querySelector(".tabs");
    box.innerHTML = state.tabs.map((t) => {
      const active = t.item.path === state.activePath ? " this" : "";
      const dirty = t.dirty ? "*" : "";
      return `
        <div class="tab${active}" data-path="${esc(t.item.path)}" title="${esc(t.item.path)}">
          <div class="name"><span class="fico" style="background-image:url('${fileIcon(t.item)}')"></span>${esc(t.item.name)}${dirty}</div>
          <a href="javascript:;" class="tab-close ri-close-line"></a>
        </div>`;
    }).join("") + '<a href="javascript:;" class="tab-add ri-add-line" title="打开同目录文件请从左侧选择"></a>';
  }

  function updateTitle(state) {
    const tab = getActive(state);
    if (!tab || !state.win) return;
    const icon = fileIcon(tab.item);
    const path = displayPath(tab.item.path);
    state.win.title = tab.item.name;
    state.win.el.querySelector(".win-title").innerHTML =
      `<span class="path-ico" style="background-image:url('${icon}')"></span><span>${esc(path)}</span>`;
    state.root.querySelector(".file-mode").textContent = tab.modeLabel;
    WM.renderTaskbar();
  }

  function updateHistoryButtons(state) {
    const back = state.root.querySelector('[data-act="historyBack"]');
    const next = state.root.querySelector('[data-act="historyNext"]');
    const canBack = state.histIndex > 0;
    const canNext = state.histIndex >= 0 && state.histIndex < state.history.length - 1;
    back.classList.toggle("disable", !canBack);
    back.classList.toggle("opacity-20", !canBack);
    next.classList.toggle("disable", !canNext);
    next.classList.toggle("opacity-20", !canNext);
  }

  function pushHistory(state, path) {
    if (state.histLock) return;
    if (state.histIndex >= 0 && state.history[state.histIndex] === path) return;
    state.history = state.history.slice(0, state.histIndex + 1);
    state.history.push(path);
    state.histIndex = state.history.length - 1;
    updateHistoryButtons(state);
  }

  async function historyGo(state, delta) {
    const next = state.histIndex + delta;
    if (next < 0 || next >= state.history.length) return;
    state.histLock = true;
    state.histIndex = next;
    const path = state.history[next];
    const tab = state.tabs.find((t) => t.item.path === path);
    if (tab) await activateTab(state, path);
    else {
      const name = pathBaseName(path);
      await openFileInEditor(state, {
        path,
        name,
        type: "file",
        ext: (name.split(".").pop() || "").toLowerCase(),
      });
    }
    state.histLock = false;
    updateHistoryButtons(state);
  }

  async function renderTree(state) {
    const path = state.treePath || window.FMEnv.workspace;
    const res = await API.list(path);
    const items = res.data.folderList.concat(res.data.fileList);
    const crumbs = pathChain(path).map((p) => ({ name: pathBaseName(p), path: p }));
    state.root.querySelector(".header-address-content").innerHTML = crumbs.map((c, i) => {
      const last = i === crumbs.length - 1 ? " last" : "";
      const first = i === 0 ? " first" : "";
      return `<li class="header-address-item${first}${last}"><a href="javascript:;" data-tree-path="${esc(c.path)}" data-tree-type="folder" data-tree-name="${esc(c.name)}"><span class="title-name">${esc(c.name)}</span></a></li>`;
    }).join("");

    const tree = state.root.querySelector(".editor-tree");
    if (path !== "/") {
      items.unshift({
        name: "..",
        path: parentPath(path),
        type: "folder",
        ext: "",
      });
    }
    tree.innerHTML = items.map((it) => {
      const active = it.path === state.activePath ? " active" : "";
      return `<li class="tree-item${active}">
        <a href="javascript:;" data-tree-path="${esc(it.path)}" data-tree-type="${it.type}" data-tree-name="${esc(it.name)}">
          <span class="tico" style="background-image:url('${fileIcon(it)}')"></span>
          <span class="tname">${esc(it.name)}</span>
        </a>
      </li>`;
    }).join("");
  }

  async function persistActiveSession(state) {
    const tab = getActive(state);
    if (!tab) return;
    tab.content = state.editor.getValue();
    tab.cursor = state.editor.getCursorPosition();
    tab.scroll = state.editor.session.getScrollTop();
  }

  async function activateTab(state, path) {
    if (!path) return;
    await persistActiveSession(state);
    const tab = state.tabs.find((t) => t.item.path === path);
    if (!tab) throw new Error(`标签不存在: ${path}`);
    state.activePath = path;
    state.ignoreChange = true;
    state.editor.session.setMode(tab.mode);
    state.editor.session.setValue(tab.content);
    state.editor.session.setTabSize(state.tabSize);
    state.editor.session.setUseSoftTabs(state.useSoftTabs);
    if (tab.cursor) state.editor.moveCursorToPosition(tab.cursor);
    if (tab.scroll != null) state.editor.session.setScrollTop(tab.scroll);
    state.ignoreChange = false;
    tab.dirty = tab.content !== tab.saved;
    state.treePath = parentPath(tab.item.path);
    renderTabs(state);
    updateTitle(state);
    applyPreviewLayout(state);
    schedulePreview(state);
    await renderTree(state);
    pushHistory(state, path);
    state.editor.focus();
    state.editor.resize();
  }

  async function openFileInEditor(state, item) {
    const exist = state.tabs.find((t) => t.item.path === item.path);
    if (exist) {
      await activateTab(state, item.path);
      return;
    }
    const res = await API.preview(item.path);
    const mode = detectMode(state.aceLib, item.name);
    state.tabs.push({
      item,
      content: res.data.content,
      saved: res.data.content,
      dirty: false,
      mode,
      modeLabel: mode.replace("ace/mode/", ""),
      cursor: { row: 0, column: 0 },
      scroll: 0,
    });
    await activateTab(state, item.path);
  }

  async function saveActive(state) {
    const tab = getActive(state);
    if (!tab) throw new Error("没有打开的文件");
    const content = state.editor.getValue();
    await API.save(tab.item.path, content);
    tab.content = content;
    tab.saved = content;
    tab.dirty = false;
    renderTabs(state);
    toast("已保存");
  }

  async function saveAll(state) {
    await persistActiveSession(state);
    for (const tab of state.tabs) {
      if (!tab.dirty && tab.content === tab.saved) continue;
      await API.save(tab.item.path, tab.content);
      tab.saved = tab.content;
      tab.dirty = false;
    }
    renderTabs(state);
    toast("全部已保存");
  }

  async function refreshActive(state) {
    const tab = getActive(state);
    if (!tab) throw new Error("没有打开的文件");
    if (tab.dirty) {
      const ok = window.confirm("当前文件未保存，刷新将丢失修改，继续？");
      if (!ok) return;
    }
    const res = await API.preview(tab.item.path);
    tab.content = res.data.content;
    tab.saved = res.data.content;
    tab.dirty = false;
    state.editor.session.setValue(tab.content);
    renderTabs(state);
    schedulePreview(state);
    toast("已刷新");
  }

  async function closeTab(state, path) {
    const idx = state.tabs.findIndex((t) => t.item.path === path);
    if (idx < 0) return;
    const tab = state.tabs[idx];
    if (path === state.activePath) await persistActiveSession(state);
    if (tab.dirty) {
      const ok = window.confirm(`文件未保存：${tab.item.name}，确定关闭？`);
      if (!ok) return;
    }
    state.tabs.splice(idx, 1);
    if (!state.tabs.length) {
      state.activePath = null;
      state.editor.setValue("");
      state.editor.session.setMode("ace/mode/text");
      state.root.querySelector(".file-mode").textContent = "Text";
      if (state.win) {
        const icon = "/assets/kod/images/file_icon/icon_app/ace.png";
        state.win.title = "文本编辑器";
        state.win.el.querySelector(".win-title").innerHTML =
          `<span class="path-ico" style="background-image:url('${icon}')"></span><span>文本编辑器</span>`;
        WM.renderTaskbar();
      }
      renderTabs(state);
      return;
    }
    if (state.activePath === path) {
      const next = state.tabs[Math.max(0, idx - 1)];
      await activateTab(state, next.item.path);
    } else {
      renderTabs(state);
    }
  }

  // ── 给 agent 的可编程接口 ──
  //
  // DesktopOS 不认识 aceEditor,只按 winId 转发到这里。加/改能力只动这一处,
  // 服务端零改动(见 fm/frontend/js/desktop-os.js 的 registerInstance)。
  //
  // 编辑操作**一律基于行范围**,不提供整文件覆盖。因为用户可能正在同一个文件里打字,
  // setValue(整文件) 会吞掉他刚敲的内容;session.replace(range, text) 只动指定范围,
  // 用户在别处的改动不受影响。

  function tabOf(state, path) {
    if (!path) {
      const active = getActive(state);
      if (!active) throw new Error("编辑器里没有打开任何文件");
      return active;
    }
    const want = normalizeFsPath(path);
    const tab = state.tabs.find((t) => t.item.path === want);
    if (!tab) {
      throw new Error(`编辑器里没有打开 ${want};已打开的是 ${state.tabs.map((t) => t.item.path).join(", ") || "(无)"}`);
    }
    return tab;
  }

  //: 非活动标签页的内容在 tab.content 里,活动标签页的最新内容在 editor 里
  //: (用户可能刚敲了字还没触发同步)。取内容要区分,否则会读到旧的。
  function contentOf(state, tab) {
    return tab.item.path === state.activePath ? state.editor.getValue() : tab.content;
  }

  //: 写操作只作用于活动标签页(ace 只有一个 session 绑在编辑器上)。path 参数是
  //: **防呆用的**:agent 以为在改 A 文件、实际活动页是 B,这种错要当场拦住而不是
  //: 悄悄改错文件。不自动切标签页 —— 那会改变用户眼前看到的东西。
  function assertActive(state, path) {
    const tab = getActive(state);
    if (!tab) throw new Error("编辑器里没有打开任何文件");
    if (path) {
      const want = normalizeFsPath(path);
      if (want !== state.activePath) {
        throw new Error(
          `当前活动标签页是 ${state.activePath},不是 ${want}。` +
          `写操作只作用于活动页 —— 先用 doc.switchTab 切过去。`
        );
      }
    }
    return tab;
  }

  //: 算出「整行删除 start..end」对应的字符范围 [startRow, startCol, endRow, endCol]。
  //:
  //: 抽成纯函数是为了能脱离浏览器验证 —— 末行和整文件是这段逻辑里唯一容易出错的地方:
  //:   · 一般情况:跨到下一行开头,这样换行符一起被吃掉
  //:   · 删到末行:没有「下一行」,只能反过来吃掉上一行末尾的换行符
  //:   · 整个文件都删:退化成清空,没有换行符可吃
  //: lineLen(row) 返回该行(0 起)的字符数。
  function deleteRangeOf(startLine, endLine, totalLines, lineLen) {
    if (endLine < totalLines) return [startLine - 1, 0, endLine, 0];
    if (startLine > 1) {
      return [startLine - 2, lineLen(startLine - 2), endLine - 1, lineLen(endLine - 1)];
    }
    return [0, 0, endLine - 1, lineLen(endLine - 1)];
  }

  //: 供 _t_ace_edit.mjs 在 node 里验证纯逻辑用(不参与运行时)。
  window.AceAgentInternals = { deleteRangeOf };

  function registerAgentInterface(state, winId) {
    const editor = state.editor;
    const Range = state.aceLib.require("ace/range").Range;

    window.DesktopOS.registerInstance(winId, {
      app: "aceEditor",

      describe: () => {
        const tab = getActive(state);
        const sel = editor.getSelectionRange();
        return {
          activePath: state.activePath,
          dirty: !!(tab && tab.dirty),
          language: tab ? String(tab.mode || "").replace("ace/mode/", "") : "",
          lineCount: editor.session.getLength(),
          cursor: editor.getCursorPosition(),
          selection: {
            empty: editor.selection.isEmpty(),
            startLine: sel.start.row + 1,
            endLine: sel.end.row + 1,
          },
          tabs: state.tabs.map((t) => ({
            path: t.item.path,
            dirty: !!t.dirty,
            active: t.item.path === state.activePath,
          })),
        };
      },

      capabilities: {
        "doc.read": {
          description: "读取编辑器里某个文件的内容(带行号)。读的是编辑器里的当前内容,包含用户尚未保存的改动 —— 这一点和直接读磁盘文件不同。",
          params: {
            path: { type: "string", required: false, description: "标签页路径,省略则用当前活动标签页" },
            start_line: { type: "number", required: false, description: "起始行号(1 起),省略从第一行" },
            end_line: { type: "number", required: false, description: "结束行号(含),省略到最后一行" },
          },
          run: ({ path, start_line, end_line }) => {
            const tab = tabOf(state, path);
            const lines = contentOf(state, tab).split("\n");
            const from = Math.max(1, start_line || 1);
            const to = Math.min(lines.length, end_line || lines.length);
            if (from > lines.length) throw new Error(`start_line ${from} 超过文件行数 ${lines.length}`);
            return {
              path: tab.item.path,
              dirty: !!tab.dirty,
              totalLines: lines.length,
              startLine: from,
              endLine: to,
              content: lines.slice(from - 1, to).map((ln, i) => `${from + i}\t${ln}`).join("\n"),
            };
          },
        },

        "doc.getSelection": {
          description: "读取用户当前选中的文本及其行范围。用户没选东西时 empty 为 true。",
          params: {},
          run: () => {
            const sel = editor.getSelectionRange();
            return {
              path: state.activePath,
              empty: editor.selection.isEmpty(),
              startLine: sel.start.row + 1,
              endLine: sel.end.row + 1,
              text: editor.getSelectedText(),
            };
          },
        },

        "doc.replaceLines": {
          description:
            "替换活动标签页里指定行范围的内容。只动这个范围,用户在别处的改动不受影响。" +
            "注意:替换后如果行数变了,后面的行号会整体位移 —— 要连续改多处就从后往前改," +
            "或者每改一次重新 doc.read 确认行号。替换后是未保存状态,需要 doc.save 落盘。",
          params: {
            start_line: { type: "number", required: true, description: "起始行号(1 起,含)" },
            end_line: { type: "number", required: true, description: "结束行号(含)" },
            text: { type: "string", required: true, description: "替换成的内容,不要带行号" },
            path: { type: "string", required: false, description: "可选,写上以确认改的是这个文件(与活动页不符会报错)" },
          },
          run: ({ start_line, end_line, text, path }) => {
            const tab = assertActive(state, path);
            const total = editor.session.getLength();
            if (start_line < 1 || start_line > total) throw new Error(`start_line ${start_line} 超出范围 1..${total}`);
            if (end_line < start_line || end_line > total) throw new Error(`end_line ${end_line} 超出范围 ${start_line}..${total}`);
            const lastCol = editor.session.getLine(end_line - 1).length;
            editor.session.replace(new Range(start_line - 1, 0, end_line - 1, lastCol), text);
            return {
              path: tab.item.path,
              replacedLines: [start_line, end_line],
              totalLines: editor.session.getLength(),
              dirty: !!tab.dirty,
            };
          },
        },

        "doc.insertLines": {
          description:
            "在指定行之后插入内容,不覆盖任何现有行。after_line 为 0 表示插到文件开头。" +
            "插入会让后面的行号整体后移。",
          params: {
            after_line: { type: "number", required: true, description: "插到这一行之后;0 表示文件开头" },
            text: { type: "string", required: true, description: "要插入的内容" },
            path: { type: "string", required: false, description: "可选,写上以确认改的是这个文件" },
          },
          run: ({ after_line, text, path }) => {
            const tab = assertActive(state, path);
            const total = editor.session.getLength();
            if (after_line < 0 || after_line > total) throw new Error(`after_line ${after_line} 超出范围 0..${total}`);
            const payload = text.endsWith("\n") ? text : text + "\n";
            editor.session.insert({ row: after_line, column: 0 }, payload);
            return { path: tab.item.path, totalLines: editor.session.getLength(), dirty: !!tab.dirty };
          },
        },

        "doc.deleteLines": {
          description:
            "整行删除指定范围。这和「用 doc.replaceLines 替换成空字符串」不一样 —— 后者会留下一个空行," +
            "这个是把行本身连同换行符一起去掉。删除会让后面的行号整体前移。",
          params: {
            start_line: { type: "number", required: true, description: "起始行号(1 起,含)" },
            end_line: { type: "number", required: true, description: "结束行号(含)" },
            path: { type: "string", required: false, description: "可选,写上以确认改的是这个文件" },
          },
          run: ({ start_line, end_line, path }) => {
            const tab = assertActive(state, path);
            const total = editor.session.getLength();
            if (start_line < 1 || start_line > total) throw new Error(`start_line ${start_line} 超出范围 1..${total}`);
            if (end_line < start_line || end_line > total) throw new Error(`end_line ${end_line} 超出范围 ${start_line}..${total}`);
            // 要连换行符一起删,范围得跨到下一行的开头 —— 边界情况见 deleteRangeOf。
            const [sr, sc, er, ec] = deleteRangeOf(
              start_line, end_line, total, (row) => editor.session.getLine(row).length
            );
            editor.session.remove(new Range(sr, sc, er, ec));
            return {
              path: tab.item.path,
              deletedLines: [start_line, end_line],
              totalLines: editor.session.getLength(),
              dirty: !!tab.dirty,
            };
          },
        },

        "doc.undo": {
          description: "撤销上一步改动。和用户按 Ctrl+Z 是同一个 undo 栈 —— 改错了可以自己退回来。",
          params: {},
          run: () => {
            editor.undo();
            const tab = getActive(state);
            return { path: tab ? tab.item.path : "", totalLines: editor.session.getLength(),
                     dirty: !!(tab && tab.dirty) };
          },
        },

        "doc.save": {
          description: "把当前活动标签页存盘。",
          params: {},
          run: async () => {
            await saveActive(state);
            const tab = getActive(state);
            return { path: tab ? tab.item.path : "", dirty: !!(tab && tab.dirty) };
          },
        },

        "doc.switchTab": {
          description: "切换到另一个已打开的标签页(要打开新文件用 desktop_open_file)。",
          params: { path: { type: "string", required: true, description: "已打开的标签页路径" } },
          run: async ({ path }) => {
            const tab = tabOf(state, path);
            await activateTab(state, tab.item.path);
            return { activePath: state.activePath };
          },
        },
      },
    });
  }

  kodApp.add({
    name: "aceEditor",
    title: "文本编辑器",
    sort: 10,
    singleton: true,
    ext: EXT,
    icon: "/assets/kod/images/file_icon/icon_app/ace.png",
    async open(item, id) {
      const aceLib = await ensureAce();
      const icon = "/assets/kod/images/file_icon/icon_app/ace.png";
      if (editorUI && editorUI.win && WM.windows.get(id)) {
        WM.focus(id);
        if (editorUI.win.minimized) WM.restore(id);
        if (item) await openFileInEditor(editorUI, item);
        return editorUI.win;
      }
      const state = createEditorUI(aceLib);
      const win = WM.create({
        id,
        title: item ? item.name : "文本编辑器",
        icon: item ? fileIcon(item) : icon,
        width: Math.floor(window.innerWidth * 0.85),
        height: Math.floor(window.innerHeight * 0.78),
        className: "explorer-editor-dialog",
        titleHtml: item
          ? `<span class="path-ico" style="background-image:url('${fileIcon(item)}')"></span><span>${esc(item.path)}</span>`
          : `<span class="path-ico" style="background-image:url('${icon}')"></span><span>文本编辑器</span>`,
        content: state.root,
      });
      state.win = win;
      editorUI = state;
      if (item) await openFileInEditor(state, item);
      else {
        renderTabs(state);
        await renderTree(state);
      }
      const resize = () => state.editor.resize();
      setTimeout(resize, 0);
      const obs = new ResizeObserver(resize);
      obs.observe(state.root);
      registerAgentInterface(state, id);
      win.onClose = () => {
        const dirty = state.tabs.some((t) => t.dirty);
        if (dirty) {
          const ok = window.confirm("有未保存的文件，确定关闭编辑器？");
          if (!ok) return false;
        }
        document.removeEventListener("click", state.onDocClick);
        obs.disconnect();
        window.DesktopOS.unregisterInstance(id);
        state.editor.destroy();
        editorUI = null;
        return true;
      };
      return win;
    },
  });
})();
