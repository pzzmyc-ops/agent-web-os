// 验证 DesktopOS 的实例注册机制 —— 框架改动的核心。
// 用 node 跑,把 WM / kodApp / window 都 stub 掉,不需要浏览器也不需要 ace。
//
//   node fm/frontend/js/_t_desktop_os.mjs

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

const here = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));
const src = fs.readFileSync(path.join(here, "desktop-os.js"), "utf8");

// ---- 最小 stub ----
const windows = new Map();
function fakeWin(id, z) {
  return { id, title: `win ${id}`, minimized: false, maximized: false, el: { style: { zIndex: String(z) } } };
}
const sandbox = {
  console,
  WM: {
    windows,
    z: 100,
    create: (o) => { const w = fakeWin(o.id, ++sandbox.WM.z); windows.set(o.id, w); return w; },
    focus: (id) => {}, close: (id) => { windows.delete(String(id)); },
    minimize: () => {}, restore: () => {}, toggleMaximize: () => {},
  },
  kodApp: { apps: { explorer: { name: "explorer", title: "我的文件", ext: [] } },
            launch: async () => null, open: async () => null },
  TransferHub: { tasks: [], on: () => {} },
  window: {},
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const OS = sandbox.DesktopOS;

// ---- 用例 ----
let fails = 0;
const check = (name, cond, extra = "") => {
  console.log(`${cond ? "  ok  " : "  FAIL"} ${name}${extra ? "  " + extra : ""}`);
  if (!cond) fails++;
};

const w1 = sandbox.WM.create({ id: "explorer-1700000000001" });
const w2 = sandbox.WM.create({ id: "aceEditor" });
OS.activeId = "aceEditor";

// 未注册实例的窗口:只有窗口级信息,没有 state/capabilities
const before = OS.listWindows().find((w) => w.winId === "aceEditor");
check("未接入的 app 没有 state", before.state === undefined && before.capabilities === undefined);
check("未接入的 app 调 app.describe 报明确错误", await OS.exec({ op: "app.describe", params: { winId: "aceEditor" } })
  .then(() => false).catch((e) => /没有可编程接口/.test(e.message)));

// 注册两个实例
let docContent = "line1\nline2\nline3";
OS.registerInstance("explorer-1700000000001", {
  app: "explorer",
  describe: () => ({ path: "/项目", selection: ["/项目/a.py"], viewMode: "icon", itemCount: 3 }),
  capabilities: {
    "explorer.navigate": {
      description: "切目录",
      params: { path: { type: "string", required: true } },
      run: ({ path }) => ({ path }),
    },
  },
});
OS.registerInstance("aceEditor", {
  app: "aceEditor",
  describe: () => ({ activePath: "/项目/a.py", dirty: true, lineCount: 3 }),
  capabilities: {
    "doc.read": {
      description: "读内容",
      params: { start_line: { type: "number", required: false }, mode: { type: "string", required: false, enum: ["raw", "numbered"] } },
      run: ({ start_line }) => ({ content: docContent, startLine: start_line || 1 }),
    },
    "doc.replaceLines": {
      description: "替换行",
      params: { start_line: { type: "number", required: true }, text: { type: "string", required: true } },
      run: ({ start_line, text }) => {
        const lines = docContent.split("\n");
        lines[start_line - 1] = text;
        docContent = lines.join("\n");
        return { totalLines: lines.length };
      },
    },
  },
});

// 快照现在应该是通用地读实例状态
const snap = OS.snapshot();
check("快照里 cwd 来自实例 describe", snap.cwd === "/项目", `cwd=${snap.cwd}`);
check("快照里 selection 来自实例 describe", JSON.stringify(snap.selection) === '["/项目/a.py"]');
const ace = snap.windows.find((w) => w.winId === "aceEditor");
check("接入的 app 有 state", ace.state && ace.state.activePath === "/项目/a.py");
check("接入的 app 报出 capabilities", JSON.stringify(ace.capabilities) === '["doc.read","doc.replaceLines"]');

// 关键的架构断言:DesktopOS 的**代码**里不能出现具体 app 名(注释里的示例不算)。
// 唯一允许的例外是 cwd/selection 那处 "explorer" —— 那是产品语义(用户当前在哪),
// 不是框架耦合。除此之外任何 app 名出现在代码里,就说明又在打特例补丁了。
const code = src
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .split("\n").filter((l) => !/^\s*(\/\/|\/\/:)/.test(l)).join("\n");
const appNamesInCode = ["aceEditor", "videoPlayer", "onlyoffice", "imageViewer", "audioPlayer"]
  .filter((n) => code.includes(n));
check("代码里没有任何 app 特例分支", appNamesInCode.length === 0, `发现: ${appNamesInCode.join(",") || "无"}`);
const explorerHits = (code.match(/"explorer"/g) || []).length;
check("explorer 的引用只剩 cwd/selection 那一处", explorerHits === 1, `出现 ${explorerHits} 次`);

// app.capabilities 要给出 JSON Schema
const caps = await OS.exec({ op: "app.capabilities", params: { winId: "aceEditor" } });
const read = caps.capabilities.find((c) => c.name === "doc.read");
check("capabilities 返回 JSON Schema", read.schema.type === "object" && "start_line" in read.schema.properties);
check("schema 带上 enum", JSON.stringify(read.schema.properties.mode.enum) === '["raw","numbered"]');
const repl = caps.capabilities.find((c) => c.name === "doc.replaceLines");
check("schema 标出 required", JSON.stringify(repl.schema.required.sort()) === '["start_line","text"]');

// app.invoke 正常路径
const r1 = await OS.exec({ op: "app.invoke", params: { winId: "aceEditor", capability: "doc.read", args: {} } });
check("invoke 正常返回", r1.content === "line1\nline2\nline3");
await OS.exec({ op: "app.invoke", params: { winId: "aceEditor", capability: "doc.replaceLines", args: { start_line: 2, text: "CHANGED" } } });
check("invoke 真的改了状态", docContent === "line1\nCHANGED\nline3", `content=${JSON.stringify(docContent)}`);

// 校验:通用 invoke 丢掉的静态校验要在这里补回来
const bad = async (params, re, name) => {
  const msg = await OS.exec({ op: "app.invoke", params }).then(() => "(没有报错)").catch((e) => e.message);
  check(name, re.test(msg), `→ ${msg}`);
};
await bad({ winId: "aceEditor", capability: "doc.replaceLines", args: { text: "x" } }, /缺少必填参数 start_line/, "缺必填参数被拦住");
await bad({ winId: "aceEditor", capability: "doc.read", args: { start_line: "2" } }, /应为 number,收到 string/, "类型不对被拦住");
await bad({ winId: "aceEditor", capability: "doc.read", args: { mode: "fancy" } }, /只能是 raw\/numbered/, "enum 越界被拦住");
await bad({ winId: "aceEditor", capability: "doc.read", args: { nope: 1 } }, /不接受参数 nope/, "多余参数被拦住");
await bad({ winId: "aceEditor", capability: "doc.nonexist", args: {} }, /不支持 doc.nonexist/, "不存在的能力报出可用列表");

// describe 抛错不能让整个快照崩
OS.registerInstance("broken", { app: "broken", describe: () => { throw new Error("boom"); }, capabilities: {} });
sandbox.WM.create({ id: "broken" });
let survived = true;
try { OS.snapshot(); } catch (e) { survived = false; }
check("一个 app 的 describe 崩掉不影响整个快照", survived);

// 事件要带 phase
const evs = [];
OS.on((e) => evs.push(e));
await OS.exec({ op: "app.invoke", params: { winId: "aceEditor", capability: "doc.read", args: {} } });
const phases = evs.filter((e) => e.op === "app.invoke").map((e) => e.phase);
check("invoke 发 start + complete 事件", JSON.stringify(phases) === '["start","complete"]', `phases=${phases}`);

// 关窗口要清掉实例
OS.install();
sandbox.WM.close("aceEditor");
check("关窗口后实例被注销", !OS._instances.has("aceEditor"));

console.log(fails ? `\nFAIL — ${fails} 项不通过` : "\nPASS — 实例注册机制、schema 校验、事件、清理都正确");
process.exit(fails ? 1 : 0);
