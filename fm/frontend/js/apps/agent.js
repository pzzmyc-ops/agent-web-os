// Agent —— 文件管理器里的一个 app,同时也是桌面暴露给 agent 的入口。
//
// 窗口内是一个 iframe 指向 /agent(agent 自己的对话界面,同源)。走 iframe 而不是
// 原生移植,是因为两边前端范式不同:agent 前端是 ESM 模块(static/js/,~30 个模块),
// 文件管理器是经典全局脚本。iframe 让两者都不用改。
//
// iframe 边界上没有任何桥。文件变更刷新走服务端的 /api/desktop/refresh,由
// desktop-link.js 收 fs_changed 后刷新;agent 驱动桌面连 /api/desktop/control,
// 由 desktop-link.js 交给 DesktopOS 执行。

const AgentApp = {
  name: "agent",
  title: "Agent",
  sort: 1,
  singleton: true,
  ext: [],
  icon: "/images/agent.svg",

  open(item, id) {
    const frame = document.createElement("iframe");
    frame.className = "agent-frame";
    frame.src = "/agent";
    frame.style.cssText = "width:100%;height:100%;border:0;display:block;";

    const win = WM.create({
      id,
      title: this.title,
      icon: this.icon,
      width: 1040,
      height: 700,
      content: frame,
      className: "win-agent",
    });
    return win;
  },
};

kodApp.add(AgentApp);
