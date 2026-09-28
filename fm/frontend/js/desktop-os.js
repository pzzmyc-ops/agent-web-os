// DesktopOS —— 桌面的 OS 接口层。
//
// 作用:把 FM 里本来就有的窗口管理器(WM)和 app 注册表(kodApp)对外「说出来」,
// 让 agent 能知道桌面状态、并驱动桌面。分四块:
//
//   operations   窗口级操作的唯一声明(名字/说明/参数/是否只读),调用方运行时读它
//   snapshot()   现在是什么样(哪些窗口、谁在最前、在哪个目录、选中了什么)
//   on(fn)       事件订阅,每个事件都带 phase: start / complete / error
//   exec(cmd)    执行 operations 里的一个操作
//
// 这是一个被动接口:谁来调都一样,它不认识任何 agent,也不声明自己面对哪块屏幕。
//
// 实现方式是**包装**而不是改 WM/kodApp 的实现,所以 FM 原有代码一行不动。app 内部
// 状态则由 app 自己通过 registerInstance 声明 —— DesktopOS 不认识任何具体 app。
//
// 唯一一处按 app 名分支的地方是快照里的 cwd / selection:它们定义为「最前面那个资源
// 管理器所在的目录和选中项」。这是产品语义(用户当前在哪),不是框架耦合。
//
// 加载顺序要求:必须在 wm.js / kodApp.js 之后(要包装它们),在 explorer.js /
// desktop.js / startMenu.js 之前(它们会创建窗口和启动 app)。
//
// 一个刻意的设计选择:**选中态不发事件,只在 snapshot 里体现**。explorer 里
// state.selected 有 10 多个修改点,而且鼠标框选会高频触发;推事件的收益远小于成本,
// agent 需要时拉一次快照就够了。导航(切目录)是低频且有唯一漏斗,所以发事件。

const DesktopOS = {
  //: 最前面的窗口 id。WM 自己只有 .active class,没有这个指针。
  activeId: "",
  //: winId → app 名。kodApp 启动时知道权威的 app 名,记在这里;WM.create 直接被
  //: 调用(比如 explorer)时回落到从 id 推断。
  _appOf: new Map(),
  //: winId → 实例接口。app 自己注册,DesktopOS 只做路由 —— 见 registerInstance。
  _instances: new Map(),
  _listeners: [],
  _seq: 0,

  // ---- app 实例接口 ----
  //
  // 这是让 agent 能看进 app 内部的机制。核心约定:**DesktopOS 不认识任何具体 app**,
  // app 自己声明「我现在什么状态」和「我支持哪些操作」,DesktopOS 只负责转发和校验。
  //
  // 这样加一个 app 能力只需要改那个 app 的文件,调用方零改动 —— 对外永远只有
  // app.describe / app.capabilities / app.invoke 三个操作,不会随 app 数量增长。
  // (运行时能力发现,和 LSP / MCP 是同一个思路。)
  //
  //   DesktopOS.registerInstance(win.id, {
  //     app: "aceEditor",
  //     describe: () => ({ path, dirty, cursor, ... }),      // 当前状态
  //     capabilities: {
  //       "doc.read": {
  //         description: "读取文件内容",
  //         params: { start_line: { type: "number", required: false, description: "起始行" } },
  //         run: (args) => "...",
  //       },
  //     },
  //   });
  //   win.onClose = () => DesktopOS.unregisterInstance(win.id);

  registerInstance(winId, spec) {
    const id = String(winId || "");
    if (!id || !spec) return;
    this._instances.set(id, spec);
    if (spec.app) this._appOf.set(id, spec.app);
    this.emit({ op: "app.ready", winId: id, app: spec.app || this._inferApp(id),
      capabilities: Object.keys(spec.capabilities || {}) });
  },

  unregisterInstance(winId) {
    this._instances.delete(String(winId || ""));
  },

  instance(winId) {
    const inst = this._instances.get(String(winId || ""));
    if (!inst) throw new Error(`窗口 ${winId} 没有可编程接口(这个 app 还没接入,或窗口不存在)`);
    return inst;
  },

  //: 实例状态。describe() 抛错不能让整个快照崩掉 —— 一个 app 出问题不该影响别的。
  _describe(winId) {
    const inst = this._instances.get(String(winId || ""));
    if (!inst || typeof inst.describe !== "function") return null;
    try {
      return inst.describe();
    } catch (e) {
      return { error: String((e && e.message) || e) };
    }
  },

  //: 把 app 声明的 params 转成 JSON Schema 给模型看。
  _schemaOf(cap) {
    const params = (cap && cap.params) || {};
    const properties = {};
    const required = [];
    Object.keys(params).forEach((k) => {
      const p = params[k] || {};
      properties[k] = { type: p.type || "string" };
      if (p.description) properties[k].description = p.description;
      if (p.enum) properties[k].enum = p.enum;
      if (p.required) required.push(k);
    });
    return { type: "object", properties, required };
  },

  //: invoke 前校验。通用 invoke 的代价是丢了静态 schema 校验,所以在这里补回来 ——
  //: 否则模型传错参数只会在 app 内部炸出一个看不懂的错。
  _validate(capName, cap, args) {
    const params = (cap && cap.params) || {};
    const got = args || {};
    for (const k of Object.keys(params)) {
      const p = params[k] || {};
      if (p.required && got[k] === undefined) {
        throw new Error(`${capName} 缺少必填参数 ${k}`);
      }
      if (got[k] === undefined) continue;
      const want = p.type || "string";
      const actual = Array.isArray(got[k]) ? "array" : typeof got[k];
      if (want !== "any" && actual !== want) {
        throw new Error(`${capName} 的参数 ${k} 应为 ${want},收到 ${actual}`);
      }
      if (p.enum && !p.enum.includes(got[k])) {
        throw new Error(`${capName} 的参数 ${k} 只能是 ${p.enum.join("/")},收到 ${got[k]}`);
      }
    }
    for (const k of Object.keys(got)) {
      if (!(k in params)) throw new Error(`${capName} 不接受参数 ${k};可用的是 ${Object.keys(params).join("/") || "(无)"}`);
    }
  },

  // ---- 事件 ----

  on(fn) {
    this._listeners.push(fn);
    return () => {
      this._listeners = this._listeners.filter((x) => x !== fn);
    };
  },

  emit(ev) {
    this._seq += 1;
    const full = { seq: this._seq, ts: Date.now(), phase: "complete", ...ev };
    this._listeners.forEach((fn) => {
      try {
        fn(full);
      } catch (e) {
        console.error("[DesktopOS] listener error", e);
      }
    });
    return full;
  },

  // ---- 状态 ----

  _inferApp(winId) {
    const s = String(winId || "");
    const known = this._appOf.get(s);
    if (known) return known;
    if (s.includes(":")) return s.split(":")[0];
    const m = s.match(/^([A-Za-z_][\w-]*?)-\d{6,}$/); // explorer-1786022740123
    return m ? m[1] : s;
  },

  _windowInfo(win) {
    const app = this._inferApp(win.id);
    const info = {
      winId: win.id,
      app,
      title: win.title,
      minimized: !!win.minimized,
      maximized: !!win.maximized,
      z: Number(win.el.style.zIndex || 0),
      active: win.id === this.activeId && !win.minimized,
    };
    // app 自己报的状态。没接入的 app 就没有 state,agent 只能看到窗口级信息。
    const inst = this._instances.get(win.id);
    if (inst) {
      info.state = this._describe(win.id);
      info.capabilities = Object.keys(inst.capabilities || {});
    }
    return info;
  },

  listWindows() {
    const out = [];
    WM.windows.forEach((win) => out.push(this._windowInfo(win)));
    return out.sort((a, b) => b.z - a.z);
  },

  frontmost() {
    const wins = this.listWindows().filter((w) => !w.minimized);
    return wins.length ? wins[0] : null;
  },

  snapshot() {
    const windows = this.listWindows();
    const front = this.frontmost();
    // cwd / selection 取「最前面的资源管理器」—— 它才代表用户当前在看哪个目录。
    // 数据从通用实例接口读(w.state),不再伸手进 explorer 的闭包。
    const withPath = (w) => w.app === "explorer" && w.state && w.state.path;
    const explorer = windows.find((w) => withPath(w) && !w.minimized) || windows.find(withPath);
    const busy = (typeof TransferHub !== "undefined" ? TransferHub.tasks : [])
      .filter((t) => ["running", "waiting", "packing"].includes(t.status))
      .map((t) => ({ kind: t.kind, name: t.name, status: t.status, progress: t.progress }));
    return {
      frontmost: front
        ? { winId: front.winId, app: front.app, title: front.title, state: front.state || null }
        : null,
      windows,
      cwd: explorer ? explorer.state.path : "",
      selection: explorer && explorer.state.selection ? explorer.state.selection : [],
      apps: Object.values(kodApp.apps).map((a) => ({ name: a.name, title: a.title, ext: a.ext || [] })),
      busy,
    };
  },

  // ---- 命令 ----

  //: 窗口级操作的唯一声明:名字、说明、参数、执行。调用方(任何 agent)不抄这张表,
  //: 用 os.operations 在运行时读 —— 加一个操作只改这里,调用方零改动。
  //: params 的写法与 app 能力一致,所以 _schemaOf / _validate 两边共用。
  operations: {
    "os.operations": {
      description: "列出桌面支持的全部操作及每个操作的参数 schema。不确定能做什么时先调这个。",
      params: {},
      readonly: true,
      run() {
        return {
          operations: Object.keys(this.operations).map((name) => ({
            name,
            description: this.operations[name].description || "",
            readonly: !!this.operations[name].readonly,
            schema: this._schemaOf(this.operations[name]),
          })),
        };
      },
    },

    "os.snapshot": {
      description:
        "查看桌面当前状态:哪些窗口开着、哪个在最前面、用户在哪个目录、选中了哪些文件、" +
        "装了哪些 app、有没有正在进行的传输任务。想知道「用户在做什么」就调这个。",
      params: {},
      readonly: true,
      run() {
        return this.snapshot();
      },
    },

    "os.listWindows": {
      description:
        "列出所有窗口,按层叠顺序(最前面的在前)。每个窗口有 winId(操作窗口时要用)、app、" +
        "title、是否最小化/最大化;接入了可编程接口的窗口还带 state 与 capabilities。",
      params: {},
      readonly: true,
      run() {
        return { windows: this.listWindows(), frontmost: this.frontmost() };
      },
    },

    "window.focus": {
      description: "把某个窗口切到最前面并聚焦。",
      params: { winId: { type: "string", required: true, description: "窗口 id,从 os.snapshot 或 os.listWindows 拿" } },
      run(p) {
        return this._needWin(p.winId, (w) => {
          WM.focus(w.id);
          return { winId: w.id };
        });
      },
    },

    "window.close": {
      description: "关闭某个窗口。窗口可能拒绝关闭(比如编辑器有未保存的改动),那时返回的 closed 是 false。",
      params: { winId: { type: "string", required: true, description: "窗口 id" } },
      run(p) {
        return this._needWin(p.winId, (w) => {
          WM.close(w.id);
          return { winId: w.id, closed: !WM.windows.has(w.id) };
        });
      },
    },

    "window.minimize": {
      description: "最小化某个窗口。",
      params: { winId: { type: "string", required: true, description: "窗口 id" } },
      run(p) {
        return this._needWin(p.winId, (w) => {
          WM.minimize(w.id);
          return { winId: w.id };
        });
      },
    },

    "window.restore": {
      description: "把最小化的窗口还原。",
      params: { winId: { type: "string", required: true, description: "窗口 id" } },
      run(p) {
        return this._needWin(p.winId, (w) => {
          WM.restore(w.id);
          return { winId: w.id };
        });
      },
    },

    "window.maximize": {
      description: "最大化某个窗口。",
      params: { winId: { type: "string", required: true, description: "窗口 id" } },
      run(p) {
        return this._needWin(p.winId, (w) => {
          if (!w.maximized) WM.toggleMaximize(w.id);
          return { winId: w.id, maximized: true };
        });
      },
    },

    "app.launch": {
      description: "启动一个 app(不带文件)。可用的 app 名从 os.snapshot 的 apps 里看。",
      params: { app: { type: "string", required: true, description: "app 名,如 transferManager" } },
      async run(p) {
        const name = String(p.app || "");
        if (!kodApp.get(name)) throw new Error(`app 不存在: ${name}`);
        const win = await kodApp.launch(name);
        return { winId: win ? win.id : "", app: name };
      },
    },

    "explorer.open": {
      description: "开一个资源管理器窗口并定位到指定目录。",
      params: {
        path: { type: "string", required: false, description: "目录的真实路径,正斜杠,如 D:/项目;省略为此电脑" },
      },
      run(p) {
        const path = window.normalizeFsPath(p.path || "/");
        const win = openExplorer(path);
        return { winId: win ? win.id : "", path };
      },
    },

    "file.open": {
      description: "打开一个文件。不指定 app 时按扩展名选默认 app(和用户双击的行为一致)。",
      params: {
        path: { type: "string", required: true, description: "文件路径,前导斜杠,如 /项目/a.py" },
        app: { type: "string", required: false, description: "强制用某个 app 打开,如 aceEditor" },
      },
      async run(p) {
        const path = window.normalizeFsPath(p.path || "");
        if (!path || path === "/") throw new Error("file.open 需要一个文件路径");
        const res = await API.info(path);
        const item = res.data;
        if (item.type === "folder") throw new Error(`${path} 是目录,用 explorer.open`);
        const win = await kodApp.open(item, p.app || undefined);
        return { winId: win ? win.id : "", path, app: p.app || "" };
      },
    },

    // ---- 通用 app 操作:不认识具体 app,只转发到实例接口 ----

    "app.describe": {
      description:
        "查看某个窗口内部的状态 —— 用户此刻在这个 app 里做什么。不同 app 报的字段不同," +
        "例如文本编辑器报当前文件/是否有未保存改动/光标位置,资源管理器报当前目录/选中的文件。" +
        "这个 app 没接入可编程接口时报错,那时只能从 os.listWindows 看窗口级信息。",
      params: { winId: { type: "string", required: true, description: "窗口 id" } },
      readonly: true,
      run(p) {
        const inst = this.instance(p.winId);
        return { winId: String(p.winId), app: inst.app || this._inferApp(p.winId),
          state: this._describe(p.winId) };
      },
    },

    "app.capabilities": {
      description:
        "列出某个窗口里 app 支持的操作,以及每个操作的参数 schema。调用前先看这个 —— " +
        "支持的操作因 app 而异,同一个 app 在不同状态下能力也可能不同。",
      params: { winId: { type: "string", required: true, description: "窗口 id" } },
      readonly: true,
      run(p) {
        const inst = this.instance(p.winId);
        const caps = inst.capabilities || {};
        return {
          winId: String(p.winId),
          app: inst.app || this._inferApp(p.winId),
          capabilities: Object.keys(caps).map((name) => ({
            name,
            description: caps[name].description || "",
            schema: this._schemaOf(caps[name]),
          })),
        };
      },
    },

    "app.invoke": {
      description: "调用某个窗口里 app 的一个操作。capability 与 args 先用 app.capabilities 查清楚,参数会按它给的 schema 校验。",
      params: {
        winId: { type: "string", required: true, description: "窗口 id" },
        capability: { type: "string", required: true, description: "能力名,如 doc.reload" },
        args: { type: "object", required: false, description: "该能力的参数对象,没有参数时省略" },
      },
      async run(p) {
        const inst = this.instance(p.winId);
        const capName = String(p.capability || "");
        const cap = (inst.capabilities || {})[capName];
        if (!cap) {
          throw new Error(
            `${inst.app || "该 app"} 不支持 ${capName};支持的是 ${Object.keys(inst.capabilities || {}).join("/") || "(无)"}`
          );
        }
        const args = p.args || {};
        this._validate(capName, cap, args);
        this.emit({ op: "app.invoke", phase: "start", winId: String(p.winId),
          app: inst.app, capability: capName });
        try {
          const result = await cap.run(args);
          this.emit({ op: "app.invoke", phase: "complete", winId: String(p.winId),
            app: inst.app, capability: capName });
          // 能力没返回东西时要说清楚,不要塞一个空对象 —— 空对象看起来像「成功但没数据」,
          // 会掩盖掉「这个能力其实坏了」。(踩过:onlyoffice 的 callCommand 传字符串
          // 时编辑器只定义函数不执行,回调拿到 undefined,表现就是一路返回 {}。)
          if (result === undefined || result === null) {
            return { ok: true, warning: `${capName} 执行完成但没有返回数据` };
          }
          return typeof result === "object" ? result : { result };
        } catch (e) {
          this.emit({ op: "app.invoke", phase: "error", winId: String(p.winId),
            app: inst.app, capability: capName, error: String((e && e.message) || e) });
          throw e;
        }
      },
    },
  },

  async exec(cmd) {
    const op = String((cmd && cmd.op) || "");
    const entry = this.operations[op];
    if (!entry) {
      throw new Error(`未知操作: ${op};可用的是 ${Object.keys(this.operations).join("/")}`);
    }
    const p = (cmd && cmd.params) || {};
    this._validate(op, entry, p);
    return entry.run.call(this, p);
  },

  _needWin(winId, fn) {
    const win = WM.windows.get(String(winId || ""));
    if (!win) throw new Error(`窗口不存在: ${winId}`);
    return fn(win);
  },

  // ---- 给 WM / kodApp 打点 ----

  install() {
    if (this._installed) return;
    this._installed = true;

    const wmCreate = WM.create.bind(WM);
    WM.create = (opts) => {
      const id = String((opts && opts.id) || "");
      const existed = WM.windows.has(id);
      if (!existed) this.emit({ op: "window.create", phase: "start", winId: id, app: this._inferApp(id) });
      const win = wmCreate(opts);
      if (!existed) {
        this.emit({ op: "window.create", phase: "complete", ...this._windowInfo(win) });
      }
      return win;
    };

    const wmFocus = WM.focus.bind(WM);
    WM.focus = (id) => {
      const prev = this.activeId;
      wmFocus(id);
      if (WM.windows.has(String(id))) {
        this.activeId = String(id);
        if (prev !== this.activeId) {
          this.emit({ op: "window.focus", winId: this.activeId, prevWinId: prev, app: this._inferApp(this.activeId) });
        }
      }
    };

    const wmClose = WM.close.bind(WM);
    WM.close = (id) => {
      const key = String(id);
      if (!WM.windows.has(key)) return;
      const app = this._inferApp(key);
      this.emit({ op: "window.close", phase: "start", winId: key, app });
      wmClose(id);
      const gone = !WM.windows.has(key);
      this.emit({ op: "window.close", phase: gone ? "complete" : "error", winId: key, app,
        error: gone ? undefined : "窗口拒绝关闭(onClose 返回了 false)" });
      if (gone) {
        this._appOf.delete(key);
        this._instances.delete(key);
        if (this.activeId === key) this.activeId = "";
      }
    };

    const wmMin = WM.minimize.bind(WM);
    WM.minimize = (id) => {
      wmMin(id);
      if (this.activeId === String(id)) this.activeId = "";
      this.emit({ op: "window.minimize", winId: String(id), app: this._inferApp(id) });
    };

    const wmRestore = WM.restore.bind(WM);
    WM.restore = (id) => {
      wmRestore(id);
      this.emit({ op: "window.restore", winId: String(id), app: this._inferApp(id) });
    };

    const wmMax = WM.toggleMaximize.bind(WM);
    WM.toggleMaximize = (id) => {
      wmMax(id);
      const win = WM.windows.get(String(id));
      this.emit({ op: "window.maximize", winId: String(id), app: this._inferApp(id),
        maximized: !!(win && win.maximized) });
    };

    // kodApp 知道权威的 app 名,记下来供 _inferApp 用(explorer 那种直接调 WM.create
    // 的走不到这里,回落到从 id 推断)。
    const appLaunch = kodApp.launch.bind(kodApp);
    kodApp.launch = async (name) => {
      this.emit({ op: "app.launch", phase: "start", app: name });
      try {
        const win = await appLaunch(name);
        if (win) this._appOf.set(win.id, name);
        this.emit({ op: "app.launch", phase: "complete", app: name, winId: win ? win.id : "" });
        return win;
      } catch (e) {
        this.emit({ op: "app.launch", phase: "error", app: name, error: String((e && e.message) || e) });
        throw e;
      }
    };

    const appOpen = kodApp.open.bind(kodApp);
    kodApp.open = async (item, appName) => {
      const path = item ? item.path : "";
      this.emit({ op: "app.open", phase: "start", app: appName || "", path });
      try {
        const win = await appOpen(item, appName);
        if (win) this._appOf.set(win.id, this._inferApp(win.id));
        this.emit({ op: "app.open", phase: "complete", app: win ? this._inferApp(win.id) : (appName || ""),
          winId: win ? win.id : "", path });
        return win;
      } catch (e) {
        this.emit({ op: "app.open", phase: "error", app: appName || "", path,
          error: String((e && e.message) || e) });
        throw e;
      }
    };

    // 传输任务的进度已经有自己的推送,接过来统一成带 phase 的事件。
    if (typeof TransferHub !== "undefined") {
      TransferHub.on((tasks) => {
        (tasks || []).forEach((t) => {
          const phase = t.status === "done" ? "complete"
            : (t.status === "error" || t.status === "failed") ? "error"
            : t.status === "running" || t.status === "packing" ? "progress"
            : "start";
          this.emit({ op: "transfer", phase, taskId: t.id, kind: t.kind, name: t.name,
            status: t.status, progress: t.progress });
        });
      });
    }
  },
};

window.DesktopOS = DesktopOS;
