const DingTalkBotApp = {
  name: "dingtalk-bot",
  title: "钉钉机器人agent",
  sort: 4,
  singleton: true,
  ext: [],
  icon: "/images/agent.svg",

  open(item, id) {
    const frame = document.createElement("iframe");
    frame.className = "agent-frame";
    frame.src = "/agent?dingtalk=1";
    frame.style.cssText = "width:100%;height:100%;border:0;display:block;";
    return WM.create({
      id,
      title: this.title,
      icon: this.icon,
      width: 880,
      height: 640,
      content: frame,
      className: "win-agent",
    });
  },
};

kodApp.add(DingTalkBotApp);
