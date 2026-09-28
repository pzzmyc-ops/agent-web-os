const kodApp = {
  apps: {},
  byExt: {},

  add(app) {
    if (!app || !app.name) {
      throw new Error("app.name is required");
    }
    if (app.menu === undefined) app.menu = true;
    this.apps[app.name] = app;
    (app.ext || []).forEach((ext) => {
      const key = String(ext).toLowerCase();
      if (!this.byExt[key]) this.byExt[key] = [];
      this.byExt[key] = this.byExt[key].filter((a) => a.name !== app.name);
      this.byExt[key].push(app);
      this.byExt[key].sort((a, b) => (b.sort || 0) - (a.sort || 0));
    });
  },

  get(name) {
    return this.apps[name] || null;
  },

  remove(name) {
    const app = this.apps[name];
    if (!app) {
      throw new Error("App 不存在: " + name);
    }
    delete this.apps[name];
    (app.ext || []).forEach((ext) => {
      const key = String(ext).toLowerCase();
      if (!this.byExt[key]) {
        throw new Error("扩展索引缺失: " + key);
      }
      this.byExt[key] = this.byExt[key].filter((a) => a.name !== name);
    });
  },

  listMenu() {
    return Object.values(this.apps)
      .filter((a) => a.menu)
      .sort((a, b) => (a.sort || 0) - (b.sort || 0));
  },

  getApp(ext) {
    const list = this.byExt[String(ext || "").toLowerCase()];
    if (!list || !list.length) return null;
    return list[0];
  },

  listByExt(ext) {
    return this.byExt[String(ext || "").toLowerCase()] || [];
  },

  async launch(name) {
    const app = this.get(name);
    if (!app) throw new Error(`App 不存在: ${name}`);
    const id = app.singleton ? app.name : `${app.name}:empty`;
    const exist = WM.windows.get(id);
    if (exist) {
      WM.focus(id);
      if (exist.minimized) WM.restore(id);
      return exist;
    }
    return app.open(null, id);
  },

  async open(item, appName) {
    if (!item) {
      if (!appName) throw new Error("kodApp.open 需要文件或 appName");
      return this.launch(appName);
    }
    if (item.type === "folder") {
      throw new Error("kodApp.open 仅用于文件");
    }
    let app = null;
    if (appName) {
      app = this.get(appName);
      if (!app) throw new Error(`App 不存在: ${appName}`);
    } else {
      app = this.getApp(item.ext);
    }
    if (!app) {
      const a = document.createElement("a");
      a.href = API.downloadUrl(item.path);
      a.download = item.name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      return null;
    }
    const id = app.singleton ? app.name : `${app.name}:${item.path}`;
    const exist = WM.windows.get(id);
    if (exist) {
      WM.focus(id);
      if (exist.minimized) WM.restore(id);
      if (app.singleton) return app.open(item, id);
      return exist;
    }
    return app.open(item, id);
  },
};

function openFile(item, appName) {
  return kodApp.open(item, appName);
}
