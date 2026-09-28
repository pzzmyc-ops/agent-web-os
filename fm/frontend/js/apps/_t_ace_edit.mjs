// 验证 doc.deleteLines 的范围算术。
//
// 真实 ace 需要 DOM,node 里跑不了;但「整行删除对应哪个字符范围」是纯算术,
// 而末行/整文件这两个边界正是唯一容易错的地方。所以:
//   1. 从 aceEditor.js 里取出纯函数 deleteRangeOf
//   2. 用一个忠实的文本 splice 模型代替 ace 的 session.remove
//      (ace 的 remove 就是按 (row,col) 做字符串 splice,replace 也走同一条路)
//   3. 断言删除后的文本 = 直接把那些行从数组里删掉
//
//   node fm/frontend/js/apps/_t_ace_edit.mjs

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

const here = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));
const src = fs.readFileSync(path.join(here, "aceEditor.js"), "utf8");

// aceEditor.js 的 IIFE 在加载时只定义函数 + 调一次 kodApp.add,不碰 DOM,
// 所以 stub 掉几个全局就能在 vm 里加载。
const sandbox = { console, kodApp: { add: () => {} }, window: {}, document: undefined };
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const { deleteRangeOf } = sandbox.window.AceAgentInternals;

// ---- 忠实的 ace 文本模型:按 (row,col) 做 splice ----
function offsetOf(lines, row, col) {
  let n = 0;
  for (let i = 0; i < row; i++) n += lines[i].length + 1; // +1 是换行符
  return n + col;
}
function removeRange(text, [sr, sc, er, ec]) {
  const lines = text.split("\n");
  const a = offsetOf(lines, sr, sc);
  const b = offsetOf(lines, er, ec);
  return text.slice(0, a) + text.slice(b);
}

let fails = 0;
function tryDelete(label, lines, start, end) {
  const text = lines.join("\n");
  const lineLen = (row) => lines[row].length;
  const range = deleteRangeOf(start, end, lines.length, lineLen);
  const got = removeRange(text, range);
  const want = lines.filter((_, i) => i + 1 < start || i + 1 > end).join("\n");
  const ok = got === want;
  if (!ok) fails++;
  console.log(`${ok ? "  ok  " : "  FAIL"} ${label}`);
  if (!ok) {
    console.log(`        range=${JSON.stringify(range)}`);
    console.log(`        得到=${JSON.stringify(got)}`);
    console.log(`        期望=${JSON.stringify(want)}`);
  }
  return got;
}

const L = ["def hello():", '    return "x"', "", "hello()"];

console.log("=== 常规:中间几行 ===");
tryDelete("删第 2 行", L, 2, 2);
tryDelete("删第 2-3 行", L, 2, 3);
tryDelete("删第 1 行(首行,后面还有内容)", L, 1, 1);

console.log("=== 边界:删到末行 ===");
tryDelete("删末行(第 4 行)", L, 4, 4);
tryDelete("删第 3-4 行(到末尾)", L, 3, 4);
tryDelete("删第 2-4 行(到末尾)", L, 2, 4);

console.log("=== 边界:整个文件 ===");
tryDelete("删第 1-4 行(全删)", L, 1, 4);
tryDelete("单行文件全删", ["only line"], 1, 1);
tryDelete("两行文件删末行", ["a", "b"], 2, 2);
tryDelete("两行文件删首行", ["a", "b"], 1, 1);

console.log("=== 有空行/长行 ===");
tryDelete("删一个空行", ["a", "", "b"], 2, 2);
tryDelete("末行是空行,删它", ["a", "b", ""], 3, 3);
tryDelete("末行是空行,删 2-3", ["a", "b", ""], 2, 3);

console.log("\n=== 对照:用 replaceLines 替空串会留下空行(所以 deleteLines 不可缺) ===");
{
  const lines = [...L];
  const text = lines.join("\n");
  const lastCol = lines[1].length;
  const replaced = removeRange(text, [1, 0, 1, lastCol]); // replace(range,"") 等价于 remove
  const leftEmptyLine = replaced.split("\n").length === lines.length;
  console.log(`${leftEmptyLine ? "  ok  " : "  FAIL"} replaceLines(2,2,"") 后行数不变(${lines.length} → ${replaced.split("\n").length}),确实留了空行`);
  if (!leftEmptyLine) fails++;
  const deleted = removeRange(text, deleteRangeOf(2, 2, lines.length, (r) => lines[r].length));
  const realDelete = deleted.split("\n").length === lines.length - 1;
  console.log(`${realDelete ? "  ok  " : "  FAIL"} deleteLines(2,2) 后行数 -1(${lines.length} → ${deleted.split("\n").length})`);
  if (!realDelete) fails++;
}

console.log(fails ? `\nFAIL — ${fails} 项不通过` : "\nPASS — 整行删除的范围算术在所有边界上都正确");
process.exit(fails ? 1 : 0);
