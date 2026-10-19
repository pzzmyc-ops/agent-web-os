const BrowserApp = {
  name: "browser",
  title: "简易浏览器壳",
  sort: 13,
  singleton: true,
  menu: true,
  icon: "/images/browser.svg",

  open(item, id) {
    const root = document.createElement("div");
    root.className = "browser-app";
    root.innerHTML = [
      '<div class="browser-tabs"><div class="browser-tablist"></div><button type="button" class="browser-add" title="新标签">+</button></div>',
      '<div class="browser-bar">',
      '<button type="button" data-act="back" disabled>后退</button>',
      '<button type="button" data-act="reload" disabled>刷新</button>',
      '<input type="text" spellcheck="false" placeholder="输入网址" />',
      '<button type="button" data-act="go">打开</button>',
      "</div>",
      '<div class="browser-stage"></div>',
    ].join("");
    const input = root.querySelector("input");
    const backBtn = root.querySelector('[data-act="back"]');
    const reloadBtn = root.querySelector('[data-act="reload"]');
    const tablist = root.querySelector(".browser-tablist");
    const stage = root.querySelector(".browser-stage");
    const STORE = "fm.browser.tabs";
    const tabs = [];
    let active = 0;

    function pageUrl(text) {
      const raw = String(text || "").trim();
      if (!raw) return "";
      if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(raw)) {
        if (!/^https?:\/\//i.test(raw)) throw new Error("只打开 http 或 https");
        return raw;
      }
      const host = raw.split("/")[0].split("?")[0].split("#")[0];
      const local = /^(localhost|127\.0\.0\.1)(:\d+)?$/i.test(host);
      return (local ? "http://" : "https://") + raw;
    }

    function tabLabel(tab) {
      const url = tab.index >= 0 ? tab.history[tab.index] : "";
      if (!url) return "新标签页";
      return new URL(url).host || url;
    }

    function current() {
      return tabs[active];
    }

    function remember() {
      sessionStorage.setItem(STORE, JSON.stringify({
        active,
        tabs: tabs.map((tab) => ({ history: tab.history, index: tab.index })),
      }));
    }

    function frameUrl(tab) {
      const url = tab.index >= 0 ? tab.history[tab.index] : "";
      if (!url) return "about:blank";
      return "/api/browser/page?url=" + encodeURIComponent(url);
    }

    function renderTabs() {
      tablist.innerHTML = "";
      tabs.forEach((tab, i) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "browser-tab" + (i === active ? " on" : "");
        const name = document.createElement("span");
        name.textContent = tabLabel(tab);
        const close = document.createElement("i");
        close.textContent = "×";
        close.onclick = (e) => {
          e.stopPropagation();
          closeTab(i);
        };
        btn.appendChild(name);
        btn.appendChild(close);
        btn.onclick = () => {
          active = i;
          paint();
        };
        tablist.appendChild(btn);
      });
    }

    function paint() {
      const tab = current();
      input.value = tab.index >= 0 ? tab.history[tab.index] : "";
      tabs.forEach((item, i) => {
        item.frame.classList.toggle("show", i === active);
        const next = frameUrl(item);
        if (item.frame.getAttribute("src") !== next) item.frame.src = next;
      });
      backBtn.disabled = tab.index <= 0;
      reloadBtn.disabled = tab.index < 0;
      renderTabs();
      remember();
    }

    function addTab(history, index) {
      const frame = document.createElement("iframe");
      frame.title = "页面";
      frame.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms allow-modals allow-downloads");
      stage.appendChild(frame);
      const tab = { history: history.slice(), index, frame };
      tabs.push(tab);
      return tab;
    }

    function closeTab(i) {
      tabs[i].frame.remove();
      tabs.splice(i, 1);
      if (!tabs.length) addTab([], -1);
      if (i < active) active -= 1;
      else if (active >= tabs.length) active = tabs.length - 1;
      paint();
    }

    function openUrl(text) {
      const tab = current();
      const url = pageUrl(text);
      if (!url) {
        tab.history.splice(0, tab.history.length);
        tab.index = -1;
        paint();
        return;
      }
      if (tab.history[tab.index] === url) {
        tab.frame.removeAttribute("src");
        tab.frame.src = frameUrl(tab);
        return;
      }
      tab.history.splice(tab.index + 1);
      tab.history.push(url);
      tab.index = tab.history.length - 1;
      paint();
    }

    function recall() {
      const raw = sessionStorage.getItem(STORE);
      if (!raw) {
        addTab([], -1);
        return;
      }
      const data = JSON.parse(raw);
      if (!Array.isArray(data.tabs) || typeof data.active !== "number") throw new Error("浏览器记录坏了");
      data.tabs.forEach((tab) => {
        if (!Array.isArray(tab.history) || typeof tab.index !== "number") throw new Error("浏览器记录坏了");
        tab.history.forEach((url) => {
          if (typeof url !== "string" || !/^https?:\/\//i.test(url)) throw new Error("浏览器记录坏了");
        });
        if (tab.index < -1 || tab.index >= tab.history.length) throw new Error("浏览器记录坏了");
        addTab(tab.history, tab.index);
      });
      if (!tabs.length) addTab([], -1);
      if (data.active < 0 || data.active >= tabs.length) throw new Error("浏览器记录坏了");
      active = data.active;
    }

    function onMessage(event) {
      const from = tabs.findIndex((tab) => tab.frame.contentWindow === event.source);
      if (from < 0) return;
      if (!event.data || (event.data.type !== "browser-go" && event.data.type !== "browser-tab")) return;
      const url = pageUrl(event.data.url);
      if (!url) throw new Error("没有网址");
      if (event.data.type === "browser-tab") {
        addTab([url], 0);
        active = tabs.length - 1;
        paint();
        return;
      }
      const tab = tabs[from];
      if (tab.history[tab.index] === url) return;
      tab.history.splice(tab.index + 1);
      tab.history.push(url);
      tab.index = tab.history.length - 1;
      active = from;
      paint();
    }

    root.querySelector('[data-act="go"]').onclick = () => openUrl(input.value);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") openUrl(input.value);
    });
    backBtn.onclick = () => {
      const tab = current();
      if (tab.index <= 0) return;
      tab.index -= 1;
      paint();
    };
    reloadBtn.onclick = () => {
      const tab = current();
      if (tab.index < 0) return;
      tab.frame.removeAttribute("src");
      tab.frame.src = frameUrl(tab);
    };
    root.querySelector(".browser-add").onclick = () => {
      addTab([], -1);
      active = tabs.length - 1;
      paint();
    };
    window.addEventListener("message", onMessage);
    const win = WM.create({
      id,
      title: this.title,
      icon: this.icon,
      className: "browser-dialog",
      width: 1100,
      height: 720,
      content: root,
    });
    win.onClose = () => {
      window.removeEventListener("message", onMessage);
      return true;
    };
    recall();
    paint();
    return win;
  },
};

kodApp.add(BrowserApp);
