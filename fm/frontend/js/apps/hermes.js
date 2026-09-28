const HermesApp = {
  name: "hermes",
  title: "Hermes",
  sort: 2,
  singleton: true,
  ext: [],
  icon: "/images/hermes.svg",

  open(item, id) {
    const frame = document.createElement("iframe");
    frame.className = "agent-frame";
    frame.src = "/hermes/";
    frame.style.cssText = "width:100%;height:100%;border:0;display:block;";
    return WM.create({
      id,
      title: this.title,
      icon: this.icon,
      width: 1040,
      height: 700,
      content: frame,
      className: "win-agent",
    });
  },
};
