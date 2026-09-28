const RemoteApp = {
  name: "remote",
  title: "远程桌面",
  sort: 6,
  singleton: true,
  ext: [],
  icon: "/images/remote.svg",

  open(item, id) {
    const frame = document.createElement("iframe");
    frame.className = "agent-frame";
    frame.src = "/remote/";
    frame.style.cssText = "width:100%;height:100%;border:0;display:block;";
    const win = WM.create({
      id,
      title: this.title,
      icon: this.icon,
      width: 1280,
      height: 800,
      content: frame,
      className: "win-agent",
    });
    win.onClose = () => {
      if (win._remoteClosing) {
        return false;
      }
      win._remoteClosing = true;
      if (frame.contentWindow) {
        frame.contentWindow.postMessage({ type: "remote-disconnect" }, window.location.origin);
      }
      fetch("/remote/disconnect", { method: "POST", keepalive: true }).then((res) => {
        win.onClose = null;
        WM.close(id);
        if (!res.ok) {
          throw new Error("关闭远程连接失败 HTTP " + res.status);
        }
      }).catch((err) => {
        win.onClose = null;
        if (WM.windows.has(id)) {
          WM.close(id);
        }
        throw err;
      });
      return false;
    };
    return win;
  },
};
