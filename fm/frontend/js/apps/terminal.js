const FMTerminal = {
  seq: 0,
  icon: "/images/terminal.svg",

  async openHome(winId) {
    const resp = await fetch("/api/terminal/home");
    const body = await resp.text();
    if (!resp.ok) throw new Error(body);
    const data = JSON.parse(body);
    if (!data.path) throw new Error("没有用户目录");
    return this.open(data.path, winId);
  },

  async open(folder, winId) {
    if (!folder || folder === "/") throw new Error("这里不是可以打开终端的目录");
    await Loader.loadCss("/vendor/xterm/xterm.min.css");
    await Loader.loadJs("/vendor/xterm/xterm.min.js");
    await Loader.loadJs("/vendor/xterm/xterm-addon-fit.min.js");
    if (!window.Terminal || !window.FitAddon) throw new Error("终端组件加载失败");
    const name = String(folder).split(/[\\/]/).filter(Boolean).pop() || folder;
    const host = document.createElement("div");
    host.className = "term-host";
    const id = winId || ("terminal:" + (++this.seq));
    const win = WM.create({
      id,
      title: "终端 - " + name,
      icon: this.icon,
      className: "terminal-dialog",
      width: 760,
      height: 480,
      content: host,
    });
    const term = new window.Terminal({
      cursorBlink: true,
      fontFamily: "Cascadia Mono, Consolas, monospace",
      fontSize: 14,
      ignoreBracketedPasteMode: true,
      theme: { background: "#012456", foreground: "#f2f2f2", cursor: "#f2f2f2" },
    });
    const fit = new window.FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open(host);
    fit.fit();
    term.attachCustomKeyEventHandler((event) => {
      if (event.type !== "keydown" || !event.ctrlKey || event.altKey || event.metaKey || event.shiftKey) return true;
      if (event.key !== "c" && event.key !== "C") return true;
      if (!term.hasSelection()) return true;
      const text = term.getSelection();
      term.clearSelection();
      navigator.clipboard.writeText(text).catch((err) => { throw err; });
      return false;
    });
    const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/api/terminal/ws?path=" + encodeURIComponent(folder));
    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(new TextEncoder().encode(data));
    });
    ws.onmessage = (ev) => term.write(ev.data);
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
    };
    ws.onclose = (ev) => {
      term.write("\r\n[连接已关闭 " + ev.code + (ev.reason ? " " + ev.reason : "") + "]\r\n");
    };
    const observer = new ResizeObserver(() => {
      fit.fit();
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
      }
    });
    observer.observe(host);
    win.onClose = () => {
      observer.disconnect();
      ws.close();
      term.dispose();
      return true;
    };
    return win;
  },
};

kodApp.add({
  name: "terminal",
  title: "终端",
  sort: 12,
  menu: true,
  icon: FMTerminal.icon,
  open(item, id) {
    return FMTerminal.openHome(id);
  },
});
