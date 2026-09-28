(function () {
  const EXT = ["html", "htm"];
  const ICON = "/assets/kod/images/file_icon/icon_file/html.png";
  const ACE_BASE = "/assets/kod/app/vender/ace/src-min-noconflict/";

  function esc(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function ensureAce() {
    if (!window.ace) {
      await Loader.loadJs(`${ACE_BASE}ace.js`);
    }
    if (!window.ace) throw new Error("Ace 加载失败");
    window.ace.config.set("basePath", ACE_BASE);
    return window.ace;
  }

  function openViewer(item, id) {
    const fileIco = fileIcon(item);
    const root = document.createElement("div");
    root.className = "html-viewer-app";
    root.innerHTML = `
      <div class="html-viewer-bar">
        <button type="button" data-act="save" title="保存(Ctrl-S)" hidden><i class="font-icon ri-save-line-3"></i></button>
        <button type="button" data-act="reload" title="刷新"><i class="font-icon ri-refresh-line"></i></button>
        <button type="button" data-act="mode">代码</button>
      </div>
      <div class="html-viewer-stage">
        <iframe class="html-viewer-frame"></iframe>
        <pre class="html-viewer-editor" hidden></pre>
      </div>
    `;

    const frame = root.querySelector(".html-viewer-frame");
    const editorEl = root.querySelector(".html-viewer-editor");
    const saveBtn = root.querySelector('[data-act="save"]');
    const modeBtn = root.querySelector('[data-act="mode"]');

    const win = WM.create({
      id,
      title: item.name,
      icon: fileIco,
      className: "html-viewer-dialog",
      titleHtml: `<span class="path-ico" style="background-image:url('${fileIco}')"></span><span>${esc(item.name)}</span>`,
      width: Math.floor(window.innerWidth * 0.85),
      height: Math.floor(window.innerHeight * 0.8),
      content: root,
    });

    const state = {
      item,
      mode: "preview",
      editor: null,
      content: null,
      saved: null,
      dirty: false,
    };

    function syncTitle() {
      const ico = fileIcon(state.item);
      const star = state.dirty ? "*" : "";
      win.title = state.item.name + star;
      win.icon = ico;
      win.el.querySelector(".win-title").innerHTML =
        `<span class="path-ico" style="background-image:url('${ico}')"></span><span>${esc(state.item.name)}${star}</span>`;
      WM.renderTaskbar();
    }

    function syncChrome() {
      const code = state.mode === "code";
      saveBtn.hidden = !code;
      modeBtn.textContent = code ? "预览" : "代码";
      frame.hidden = code;
      editorEl.hidden = !code;
      root.classList.toggle("is-code", code);
      syncTitle();
    }

    function loadPreview() {
      frame.src = API.browseUrl(state.item.path) + "?t=" + Date.now();
    }

    function markDirty() {
      if (!state.editor) return;
      state.content = state.editor.getValue();
      state.dirty = state.content !== state.saved;
      syncTitle();
    }

    async function loadSource() {
      const res = await API.preview(state.item.path);
      state.content = res.data.content;
      state.saved = res.data.content;
      state.dirty = false;
      if (state.editor) state.editor.setValue(state.content, -1);
    }

    async function ensureEditor() {
      if (state.editor) return state.editor;
      const aceLib = await ensureAce();
      state.editor = aceLib.edit(editorEl);
      state.editor.setTheme("ace/theme/chrome");
      state.editor.session.setMode("ace/mode/html");
      state.editor.setShowPrintMargin(false);
      state.editor.setFontSize("14px");
      state.editor.session.setUseWrapMode(true);
      state.editor.commands.addCommand({
        name: "save",
        bindKey: { win: "Ctrl-S", mac: "Command-S" },
        exec: () => {
          saveActive().catch((err) => toast(err.message || String(err)));
        },
      });
      state.editor.session.on("change", markDirty);
      return state.editor;
    }

    async function saveActive() {
      if (state.mode !== "code" || !state.editor) throw new Error("当前不是代码模式");
      const content = state.editor.getValue();
      const res = await API.save(state.item.path, content);
      state.item = res.data;
      state.content = content;
      state.saved = content;
      state.dirty = false;
      syncTitle();
      toast("已保存");
    }

    async function showPreview() {
      if (state.mode === "code" && state.editor) {
        state.content = state.editor.getValue();
        if (state.content !== state.saved) await saveActive();
      }
      state.mode = "preview";
      syncChrome();
      loadPreview();
    }

    async function showCode() {
      await ensureEditor();
      if (!state.dirty) await loadSource();
      state.mode = "code";
      syncChrome();
      state.editor.resize();
      state.editor.focus();
    }

    async function reload() {
      if (state.mode === "preview") {
        loadPreview();
        return;
      }
      if (state.dirty) {
        const ok = window.confirm("当前文件未保存，刷新将丢失修改，继续？");
        if (!ok) return;
      }
      await loadSource();
      syncTitle();
    }

    const mediaView = {
      hasPath: (path) => path === normalizeFsPath(state.item.path),
      update(fresh) {
        state.item = fresh;
        if (state.mode === "preview") loadPreview();
        else syncTitle();
      },
    };
    window.FMMediaViews.add(mediaView);

    root.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-act]");
      if (!btn) return;
      const act = btn.dataset.act;
      Promise.resolve().then(() => {
        if (act === "save") return saveActive();
        if (act === "reload") return reload();
        if (act === "mode") return state.mode === "preview" ? showCode() : showPreview();
        throw new Error("未知操作: " + act);
      }).catch((err) => toast(err.message || String(err)));
    });

    const obs = new ResizeObserver(() => {
      if (state.editor && state.mode === "code") state.editor.resize();
    });
    obs.observe(root);

    win.onClose = () => {
      if (state.dirty) {
        const ok = window.confirm("文件未保存，确定关闭？");
        if (!ok) return false;
      }
      window.FMMediaViews.delete(mediaView);
      obs.disconnect();
      if (state.editor) state.editor.destroy();
      return true;
    };

    syncChrome();
    loadPreview();
    return win;
  }

  kodApp.add({
    name: "htmlViewer",
    title: "HTML 查看器",
    sort: 40,
    ext: EXT,
    icon: ICON,
    async open(item, id) {
      if (!item) {
        const root = document.createElement("div");
        root.className = "app-empty";
        root.textContent = "在文件管理器中双击 HTML 以预览";
        return WM.create({
          id: id || "htmlViewer:empty",
          title: "HTML 查看器",
          icon: ICON,
          className: "html-viewer-dialog",
          titleHtml: `<span class="path-ico" style="background-image:url('${ICON}')"></span><span>HTML 查看器</span>`,
          width: 640,
          height: 420,
          content: root,
        });
      }
      return openViewer(item, id);
    },
  });
})();
