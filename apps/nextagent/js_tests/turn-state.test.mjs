/**
 * 「这个会话还在不在跑」只有一个权威:后端推的 tasks_snapshot。
 *
 * 这组测试盯的就是当初那个 bug —— 回合出错/被停止、turn_complete 那一帧没到,
 * 前端就永远卡在「停止键 + 等待回复中」。现在所有状态都从名单收敛,所以:
 *   - 丢 turn_complete 也能自己好;
 *   - 后端说在跑就绝不会被误判成结束;
 *   - 旧回合迟到的收尾帧不会打断新回合。
 */
import assert from "node:assert/strict";

import { installDom } from "./dom-stub.mjs";

installDom();

// 这批模块会顺带用到工作区路径;给个假的 fetch,让它们按真实流程初始化。
globalThis.fetch = async () => ({
  ok: true,
  status: 200,
  json: async () => ({ workspace: "E:/mafagent/data/workspace", roots: [] }),
  text: async () => "",
  headers: { get: () => null },
});

const { state, getConvState, convCache, taskMirror, taskToConv } = await import(
  "../static/js/state.js"
);
const { loadWorkspaceEnv } = await import("../static/js/asset-url.js");
await loadWorkspaceEnv().catch(() => undefined);
const { deriveComposerAction } = await import("../static/js/composer.js");
const { handleMessage } = await import("../static/js/message-handler.js");

const sent = [];

function reset() {
  for (const key of Object.keys(taskMirror)) delete taskMirror[key];
  for (const key of Object.keys(taskToConv)) delete taskToConv[key];
  for (const key of Object.keys(convCache)) delete convCache[key];
  state.currentConversationId = "thread-a";
  state.bootstrapPhase = "ready";
  state.bootstrapping = false;
  state.connectionId = "conn-test";
  state.ws = {
    readyState: 1,
    send(raw) {
      sent.push(JSON.parse(raw));
    },
  };
  sent.length = 0;
}

function conv() {
  const cs = getConvState("thread-a");
  cs.historyLoaded = true;
  return cs;
}

function waitingRows() {
  return conv().el.querySelectorAll(".turn-waiting-row").length;
}

function startTurn(taskId, seq = 1) {
  handleMessage({
    type: "stream_start",
    seq,
    taskId,
    threadId: "thread-a",
    data: { taskId, threadId: "thread-a", content: "", taskKind: "chat" },
  });
}

function snapshot(tasks) {
  handleMessage({ type: "tasks_snapshot", data: { tasks } });
}

const results = [];
function test(name, fn) {
  results.push({ name, fn });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ---------------------------------------------------------------- 核心回归

test("turn_complete 丢失时,后端空名单也能把界面收回发送态", () => {
  reset();
  conv();
  startTurn("task-a");
  assert.equal(deriveComposerAction("thread-a").mode, "stop", "开跑后应是停止键");
  assert.equal(waitingRows(), 1, "开跑后应有等待提示");

  // turn_complete 这一帧丢了。后端回合结束时照样推空名单。
  snapshot([]);

  assert.equal(taskMirror["task-a"], undefined, "镜像行应被清掉");
  assert.equal(taskToConv["task-a"], undefined);
  assert.equal(deriveComposerAction("thread-a").mode, "send", "应回到发送键");
  assert.equal(conv().isStreaming, false);
  assert.equal(waitingRows(), 0, "「等待回复中」必须被拔掉");
});

test("后端说还在跑,界面就绝不能被误判成结束", () => {
  reset();
  conv();
  snapshot([
    { taskId: "task-a", threadId: "thread-a", status: "running", taskKind: "chat", snapshotSeq: 3 },
  ]);

  assert.equal(taskMirror["task-a"].status, "running");
  assert.equal(deriveComposerAction("thread-a").mode, "stop");

  // 内容帧乱序/重复到达也不该把状态弄丢
  snapshot([
    { taskId: "task-a", threadId: "thread-a", status: "queued", taskKind: "chat", snapshotSeq: 3 },
  ]);
  assert.equal(deriveComposerAction("thread-a").mode, "stop");
});

test("stream_error 只画错误块,不自己收尾", () => {
  reset();
  conv();
  snapshot([{ taskId: "task-a", threadId: "thread-a", status: "running", snapshotSeq: 1 }]);

  handleMessage({
    type: "stream_error",
    taskId: "task-a",
    threadId: "thread-a",
    data: { taskId: "task-a", threadId: "thread-a", errorMessage: "boom" },
  });

  assert.equal(
    deriveComposerAction("thread-a").mode,
    "stop",
    "后端还没说结束,前端不许自己判结束",
  );
  assert.equal(taskMirror["task-a"].status, "running");

  // 后端随后收回名单,状态才跟着走
  snapshot([]);
  assert.equal(deriveComposerAction("thread-a").mode, "send");
});

test("turn_complete 只是「快去看一眼」的提示", () => {
  reset();
  conv();
  snapshot([{ taskId: "task-a", threadId: "thread-a", status: "running", snapshotSeq: 1 }]);

  handleMessage({
    type: "turn_complete",
    taskId: "task-a",
    threadId: "thread-a",
    data: { taskId: "task-a", threadId: "thread-a", interrupted: false },
  });

  const probes = sent.filter((m) => m.type === "sync_state");
  assert.equal(probes.length, 1, "收到 turn_complete 应主动对账一次");
  assert.equal(probes[0].data.threadId, "thread-a");
});

test("显示刷新抛错也不能拦住状态收敛", () => {
  reset();
  conv();
  snapshot([{ taskId: "task-a", threadId: "thread-a", status: "running", snapshotSeq: 1 }]);

  // 故意的坏输入:threadId 指向一个没有会话状态的线程,让 syncAssetsThread 走异常分支
  handleMessage({
    type: "turn_complete",
    taskId: "task-a",
    threadId: "thread-ghost",
    data: { taskId: "task-a", threadId: "thread-ghost" },
  });

  assert.equal(
    sent.filter((m) => m.type === "sync_state").length,
    1,
    "对账必须发生在副作用之前",
  );
});

test("旧回合迟到的 turn_complete 不打乱新回合", () => {
  reset();
  conv();
  snapshot([{ taskId: "task-b", threadId: "thread-a", status: "running", snapshotSeq: 2 }]);

  handleMessage({
    type: "turn_complete",
    taskId: "task-a",
    threadId: "thread-a",
    data: { taskId: "task-a", threadId: "thread-a" },
  });

  assert.ok(taskMirror["task-b"], "新回合的镜像不能被旧帧清掉");
  assert.equal(deriveComposerAction("thread-a").mode, "stop");
});

test("同一会话残留的旧镜像行不会拔掉新回合的等待提示", () => {
  reset();
  conv();
  startTurn("task-new", 1);
  assert.equal(waitingRows(), 1);

  // 前端表里多出一条旧回合的镜像(它的 turn_complete 丢了)
  taskMirror["task-old"] = { taskId: "task-old", threadId: "thread-a", status: "running" };
  taskToConv["task-old"] = "thread-a";

  snapshot([
    { taskId: "task-new", threadId: "thread-a", status: "running", snapshotSeq: 2 },
  ]);

  assert.equal(taskMirror["task-old"], undefined, "旧行应被清掉");
  assert.ok(taskMirror["task-new"]);
  assert.equal(waitingRows(), 1, "新回合还在跑,等待提示不能被拔掉");
  assert.equal(deriveComposerAction("thread-a").mode, "stop");
});

test("两轮连续:出错 → 停止 → 再发一次,最后仍回到发送态", () => {
  reset();
  conv();

  // 第一轮开跑后出错(turn_complete 没到)
  startTurn("task-1", 1);
  handleMessage({
    type: "stream_error",
    taskId: "task-1",
    threadId: "thread-a",
    data: { taskId: "task-1", threadId: "thread-a", errorMessage: "工具超时" },
  });
  assert.equal(deriveComposerAction("thread-a").mode, "stop");

  // 用户点停止 → 后端收回名单
  snapshot([]);
  assert.equal(deriveComposerAction("thread-a").mode, "send");

  // 再发一次
  startTurn("task-2", 2);
  assert.equal(deriveComposerAction("thread-a").mode, "stop");

  // 这一轮也结束
  snapshot([]);
  assert.equal(deriveComposerAction("thread-a").mode, "send");
  assert.equal(waitingRows(), 0);
});

test("后端名单为空时不会凭空造出在跑状态", () => {
  reset();
  conv();
  snapshot([]);
  assert.equal(deriveComposerAction("thread-a").mode, "send");
  assert.deepEqual(Object.keys(taskMirror), []);
});

// ---------------------------------------------------------------- 自愈探测

test("探测只在还认为有回合在跑时发 sync_state", async () => {
  reset();
  conv();

  // 没有 inflight:3 秒内不该发
  await sleep(1200);
  assert.equal(
    sent.filter((m) => m.type === "sync_state").length,
    0,
    "没有在跑的回合就不该探测",
  );

  // 认为在跑:必须探测
  taskMirror["task-x"] = { taskId: "task-x", threadId: "thread-a", status: "running" };
  await sleep(2400);
  assert.ok(
    sent.filter((m) => m.type === "sync_state").length >= 1,
    "有在跑的回合就必须定期对账,否则丢帧会永久卡住",
  );
});

// ---------------------------------------------------------------- 执行

let failed = 0;
for (const { name, fn } of results) {
  try {
    await fn();
    console.log("ok   - " + name);
  } catch (error) {
    failed += 1;
    console.log("FAIL - " + name);
    console.log(String(error && error.stack ? error.stack : error).split("\n").slice(0, 6).join("\n"));
  }
}

console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed === 0 ? 0 : 1);
