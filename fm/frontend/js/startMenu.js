const StartMenu = {
  open: false,

  init() {
    this.btn = document.getElementById("start-btn");
    this.panel = document.getElementById("start-menu");
    this.btn.addEventListener("click", (e) => {
      e.stopPropagation();
      this.toggle();
    });
    document.addEventListener("click", (e) => {
      if (!this.open) return;
      if (e.target.closest("#start-menu, #start-btn")) return;
      this.hide();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && this.open) this.hide();
    });
    this.render();
  },

  async toggle() {
    if (this.open) this.hide();
    else await this.show();
  },

  async show() {
    await registerEmbeddedApps();
    this.render();
    this.panel.classList.remove("hidden");
    this.btn.classList.add("active");
    this.open = true;
  },

  hide() {
    this.panel.classList.add("hidden");
    this.btn.classList.remove("active");
    this.open = false;
  },

  render() {
    const homeIcon = "/assets/kod/images/file_icon/icon_others/folder_win11.png";
    const apps = kodApp.listMenu();
    const rows = [
      { kind: "explorer", title: "我的文件", icon: homeIcon },
      ...apps.map((a) => ({ kind: "app", name: a.name, title: a.title, icon: a.icon })),
    ];
    this.panel.innerHTML = `
      <div class="start-menu-title">应用</div>
      <div class="start-menu-list">
        ${rows.map((r) => `
          <button type="button" class="start-menu-item" data-kind="${r.kind}" data-name="${r.name || ""}">
            <span class="start-menu-ico" style="background-image:url('${r.icon}')"></span>
            <span class="start-menu-name">${escapeHtml(r.title)}</span>
          </button>
        `).join("")}
      </div>
    `;
    this.panel.querySelectorAll(".start-menu-item").forEach((el) => {
      el.addEventListener("click", async () => {
        const kind = el.dataset.kind;
        const name = el.dataset.name;
        this.hide();
        try {
          if (kind === "explorer") openExplorer(window.FMEnv.workspace);
          else await kodApp.launch(name);
        } catch (err) {
          toast(err.message || String(err));
        }
      });
    });
  },
};
