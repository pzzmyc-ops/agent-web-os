(function () {
  const EXT = ["doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "csv", "rtf"];
  const ICON = "/assets/plugins/officeViewer/static/images/icon.png";
  const WATCH_MS = 1000;
  let suppressLeave = 0;
  window.addEventListener("beforeunload", function (e) {
    if (!suppressLeave) return;
    e.stopImmediatePropagation();
    delete e.returnValue;
  }, true);

  function esc(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function loadApiJs(src) {
    if (window.DocsAPI && window.DocsAPI.DocEditor) return;
    await Loader.loadJs(src);
    if (!window.DocsAPI || !window.DocsAPI.DocEditor) {
      throw new Error("OnlyOffice DocsAPI 加载失败，请确认 Document Server 已启动");
    }
  }

  function docTypeOf(ext) {
    const e = String(ext || "").toLowerCase();
    if (["xls", "xlsx", "ods", "csv"].includes(e)) return "cell";
    if (["ppt", "pptx", "odp"].includes(e)) return "slide";
    return "word";
  }

  function stampKey(st) {
    return String(st.size) + ":" + String(st.mtimeNs);
  }

  window.OnlyOfficeApp = {
    name: "onlyoffice",
    title: "Office",
    sort: 20,
    ext: EXT,
    icon: ICON,
    async open(item, id) {
      Loader.loadCss("/css/editor.css?v=13");
      if (!item) {
        const root = document.createElement("div");
        root.className = "app-empty";
        root.textContent = "在文件管理器中双击 Office 文档以打开";
        return WM.create({
          id,
          title: "Office",
          icon: ICON,
          width: Math.floor(window.innerWidth * 0.85),
          height: Math.floor(window.innerHeight * 0.8),
          className: "onlyoffice-dialog",
          titleHtml: `<span class="path-ico" style="background-image:url('${ICON}')"></span><span>Office</span>`,
          content: root,
        });
      }

      const root = document.createElement("div");
      root.className = "onlyoffice-app";
      const win = WM.create({
        id,
        title: `Office - ${item.name}`,
        icon: ICON,
        width: Math.floor(window.innerWidth * 0.85),
        height: Math.floor(window.innerHeight * 0.8),
        className: "onlyoffice-dialog",
        titleHtml: `<span class="path-ico" style="background-image:url('${ICON}')"></span><span>Office - ${esc(item.name)}</span>`,
        content: root,
      });

      const docType = docTypeOf(item.ext || (item.name || "").split(".").pop());
      let editor = null;
      let closed = false;
      let busy = false;
      let flushing = false;
      let dirty = false;
      let lastError = "";
      let currentKey = "";
      let pendingKey = "";
      let timer = null;

      function sleep(ms) {
        return new Promise((resolve) => setTimeout(resolve, ms));
      }

      function stopWatch() {
        if (!timer) return;
        clearInterval(timer);
        timer = null;
      }

      function startWatch() {
        if (timer || closed) return;
        timer = setInterval(() => {
          watchTick().catch((e) => {
            stopWatch();
            showError("文件监视已停止，磁盘变更不再自动重载: " + String((e && e.message) || e));
          });
        }, WATCH_MS);
      }

      function showError(message) {
        lastError = String(message || "");
        let bar = root.querySelector(".onlyoffice-error");
        if (!bar) {
          bar = document.createElement("div");
          bar.className = "onlyoffice-error";
          bar.style.cssText = "position:absolute;left:0;right:0;top:0;z-index:10;padding:10px 14px;background:#fdecea;color:#b3261e;font-size:13px;line-height:1.5;border-bottom:1px solid #f5c2c0;white-space:pre-wrap;word-break:break-all;";
          root.appendChild(bar);
        }
        bar.textContent = lastError;
      }

      async function readStamp() {
        const res = await API.onlyofficeStamp(item.path);
        return res.data;
      }

      function teardownEditor() {
        suppressLeave += 1;
        window.onbeforeunload = null;
        const iframe = root.querySelector("iframe");
        if (iframe) {
          try {
            if (iframe.contentWindow) iframe.contentWindow.onbeforeunload = null;
          } catch (e) {
            lastError = String((e && e.message) || e);
          }
          iframe.src = "about:blank";
        }
        if (editor && typeof editor.destroyEditor === "function") {
          editor.destroyEditor();
        }
        editor = null;
        root.innerHTML = "";
        suppressLeave -= 1;
      }

      async function mountEditor() {
        const res = await API.onlyofficeConfig(item.path, "edit");
        const { config, apiJs, documentServer } = res.data;
        await loadApiJs(apiJs);
        teardownEditor();
        const host = document.createElement("div");
        host.id = "oo-" + Date.now();
        host.className = "onlyoffice-host";
        root.appendChild(host);
        config.events = Object.assign({}, config.events, {
          onDocumentStateChange: (ev) => {
            if (ev && ev.data === true) {
              dirty = true;
              return;
            }
            if (!ev || ev.data !== false || !dirty) return;
            dirty = false;
            flushToDisk().catch((e) => {
              lastError = String((e && e.message) || e);
            });
          },
        });
        try {
          editor = new window.DocsAPI.DocEditor(host.id, config);
        } catch (err) {
          throw new Error(`OnlyOffice 初始化失败: ${err.message || err} (${documentServer})`);
        }
        const st = await readStamp();
        currentKey = stampKey(st);
        pendingKey = "";
      }

      async function flushToDisk() {
        if (closed) throw new Error("窗口已关闭");
        if (flushing) throw new Error("正在保存到磁盘");
        if (!editor || typeof editor.serviceCommand !== "function") {
          throw new Error("编辑器不能强制保存");
        }
        flushing = true;
        lastError = "";
        try {
          const before = await readStamp();
          editor.serviceCommand("forcesave");
          for (let i = 0; i < 40; i += 1) {
            await sleep(250);
            const st = await readStamp();
            if (st.fromOffice && (st.mtimeNs !== before.mtimeNs || st.size !== before.size)) {
              currentKey = stampKey(st);
              pendingKey = "";
              return { path: item.path, saved: true };
            }
          }
          throw new Error("编辑器已保存，但磁盘文件还没有更新，再保存一次或稍后重试 import");
        } finally {
          flushing = false;
        }
      }

      async function reloadFromDisk() {
        if (closed) throw new Error("窗口已关闭");
        if (busy) throw new Error("正在重载");
        busy = true;
        lastError = "";
        try {
          await mountEditor();
          startWatch();
          return { path: item.path, reloaded: true };
        } catch (e) {
          showError("重新加载失败: " + String((e && e.message) || e));
          throw e;
        } finally {
          busy = false;
        }
      }

      async function watchTick() {
        if (closed || busy) return;
        const st = await readStamp();
        const key = stampKey(st);
        if (key === currentKey) {
          pendingKey = "";
          return;
        }
        if (st.fromOffice) {
          currentKey = key;
          pendingKey = "";
          return;
        }
        if (pendingKey === key) {
          await reloadFromDisk();
          return;
        }
        pendingKey = key;
      }

      window.DesktopOS.registerInstance(id, {
        app: "onlyoffice",
        describe: () => ({
          path: item.path,
          name: item.name,
          docType,
          mode: "edit",
          error: lastError || undefined,
        }),
        capabilities: {
          "doc.save": {
            description: "把编辑器里的内容强制写到磁盘。界面上的已保存不等于磁盘已更新。import 之前必须先调这个并等到返回。",
            params: {},
            run: () => flushToDisk(),
          },
          "doc.reload": {
            description: "从磁盘重新加载当前文件。office-suite compile 成功后必须调用。桌面也会自己重载，调两次没关系。",
            params: {},
            run: () => reloadFromDisk(),
          },
        },
      });

      try {
        await mountEditor();
      } catch (err) {
        win.onClose = null;
        window.DesktopOS.unregisterInstance(id);
        WM.close(id);
        throw err;
      }
      startWatch();

      win.onClose = () => {
        closed = true;
        stopWatch();
        window.DesktopOS.unregisterInstance(id);
        teardownEditor();
      };
      return win;
    },
  };
})();
