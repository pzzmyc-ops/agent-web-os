import { formatContent } from "./format.js";
import {
  scrollToBottom,
  ensureToolStepMedia,
  finishMediaTaskBubble,
  finishFileMediaBubble,
  finishTextMediaBubble,
  resolveMediaContentHtml,
  isFileMediaPayload,
} from "./chat-ui.js";
import {
  applyTodoToolStart,
  isTodoToolName,
  mountTodoToolResult,
  todoToolDisplayLabel,
} from "./todo-tool-ui.js";
import { state } from "./state.js";

function escapeHtml(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatToolInput(input) {
  if (input == null) return "";
  if (typeof input === "string") return formatJsonText(input).text;
  return JSON.stringify(input, null, 2);
}

function formatJsonText(text) {
  var raw = String(text == null ? "" : text);
  var trimmed = raw.replace(/^\s+/, "").replace(/\s+$/, "");
  if (!trimmed) return { text: raw, json: false };
  var first = trimmed.charAt(0);
  if (first !== "{" && first !== "[") return { text: raw, json: false };
  // 首字符只说明「看起来像 JSON」,不说明它合法 —— 被裁剪过的工具结果就是以 { 开头的
  // 残缺 JSON。这个函数本来就有「不是 JSON 就原样返回」这条路,只是以前靠猜首字符,
  // 猜错就在 parse 那行抛异常,把整页历史渲染打断。改成实际解析来判定。
  var parsed;
  try {
    parsed = JSON.parse(trimmed);
  } catch (e) {
    return { text: raw, json: false };
  }
  return { text: JSON.stringify(parsed, null, 2), json: true };
}

function ensureBlocksState(cs) {
  if (!cs.blocks) cs.blocks = {};
  return cs.blocks;
}

function rowHasLaterUser(row) {
  if (!row) return false;
  var n = row.nextElementSibling;
  while (n) {
    if (n.classList && n.classList.contains("msg-row") && n.classList.contains("user")) return true;
    n = n.nextElementSibling;
  }
  return false;
}

function reuseLastTimelineRow(cs) {
  if (!cs.el) return null;
  var tid = String(cs.currentTaskId || "");
  if (!tid) return null;
  var rows = cs.el.querySelectorAll(".turn-timeline-row");
  if (!rows.length) return null;
  var row = rows[rows.length - 1];
  if (rowHasLaterUser(row)) return null;
  var rowTid = row.dataset.streamTaskId || "";
  if (rowTid && rowTid !== tid) return null;
  if (!rowTid) row.dataset.streamTaskId = tid;
  var tl = row.querySelector(".chat-turn-timeline");
  if (!tl) return null;
  cs.blkTimelineRow = row;
  cs.blkTimelineEl = tl;
  return tl;
}

function timelineRowTaskId(timelineEl) {
  if (!timelineEl) return "";
  var row = timelineEl.closest(".turn-timeline-row");
  return row ? String(row.dataset.streamTaskId || "") : "";
}

function ensureTimeline(cs) {
  var tid = String(cs.currentTaskId || "");
  if (cs.blkTimelineEl && cs.blkTimelineEl.isConnected) {
    var boundRow = cs.blkTimelineEl.closest(".turn-timeline-row");
    if (rowHasLaterUser(boundRow)) {
      cs.blkTimelineEl = null;
      cs.blkTimelineRow = null;
    } else {
      var boundTid = timelineRowTaskId(cs.blkTimelineEl);
      if (!tid || !boundTid || boundTid === tid) {
        return cs.blkTimelineEl;
      }
      cs.blkTimelineEl = null;
      cs.blkTimelineRow = null;
    }
  }
  var bound = bindTimelineFromDom(cs);
  if (bound && bound.isConnected) {
    return bound;
  }
  var reused = reuseLastTimelineRow(cs);
  if (reused && reused.isConnected) {
    return reused;
  }
  var host = cs.el;
  if (!host) return null;
  var row = document.createElement("div");
  row.className = "msg-row ai turn-timeline-row";
  if (cs.currentTaskId) row.dataset.streamTaskId = String(cs.currentTaskId);
  var avatar = document.createElement("div");
  avatar.className = "avatar ai-avatar tool-avatar";
  avatar.textContent = "A";
  var timeline = document.createElement("div");
  timeline.className = "chat-turn-timeline";
  row.appendChild(avatar);
  row.appendChild(timeline);
  host.appendChild(row);
  cs.blkTimelineEl = timeline;
  cs.blkTimelineRow = row;
  scrollToBottom(host);
  return timeline;
}

export function blocksResetTurn(cs) {
  cs.blocks = {};
  cs.blkTimelineEl = null;
  cs.blkTimelineRow = null;
}

export function showTurnWaiting(cs, source) {
  if (!cs || !cs.el) {
    return;
  }
  var rows = cs.el.querySelectorAll(".turn-waiting-row");
  var keep = rows[0] || null;
  for (var i = 1; i < rows.length; i++) {
    if (rows[i].parentNode) rows[i].parentNode.removeChild(rows[i]);
  }
  if (keep) {
    cs.waitingEl = keep;
    return;
  }
  var row = document.createElement("div");
  row.className = "msg-row ai turn-waiting-row";
  var hint = document.createElement("div");
  hint.className = "chat-waiting-hint";
  hint.textContent = "\u7b49\u5f85\u56de\u590d\u4e2d";
  row.appendChild(hint);
  cs.el.appendChild(row);
  cs.waitingEl = row;
  scrollToBottom(cs.el);
}

export function removeTurnWaiting(cs) {
  if (!cs) return;
  if (cs.el) {
    var rows = cs.el.querySelectorAll(".turn-waiting-row");
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].parentNode) rows[i].parentNode.removeChild(rows[i]);
    }
  } else if (cs.waitingEl && cs.waitingEl.parentNode) {
    cs.waitingEl.parentNode.removeChild(cs.waitingEl);
  }
  cs.waitingEl = null;
}

export function clearStreamTimeline(cs) {
  if (!cs) return;
  var tid = String(cs.currentTaskId || "");
  if (cs.el && tid) {
    var rows = cs.el.querySelectorAll(".turn-timeline-row");
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].dataset.streamTaskId === tid) rows[i].remove();
    }
  } else if (cs.blkTimelineRow && cs.blkTimelineRow.parentNode) {
    cs.blkTimelineRow.parentNode.removeChild(cs.blkTimelineRow);
  } else if (cs.el && cs.isStreaming) {
    var last = cs.el.querySelector(".turn-timeline-row:last-child");
    if (last) last.remove();
  }
  blocksResetTurn(cs);
}

function bindTimelineFromDom(cs) {
  if (!cs.el) return null;
  var tid = String(cs.currentTaskId || "");
  if (tid) {
    var rows = cs.el.querySelectorAll('.turn-timeline-row[data-stream-task-id="' + tid + '"]');
    if (rows.length) {
      var row = rows[rows.length - 1];
      if (!rowHasLaterUser(row)) {
        var tl = row.querySelector(".chat-turn-timeline");
        if (tl) {
          cs.blkTimelineRow = row;
          cs.blkTimelineEl = tl;
          return tl;
        }
      }
    }
  }
  return reuseLastTimelineRow(cs);
}

function findSegmentByBlockId(cs, blockId) {
  var id = String(blockId || "");
  if (!id || !cs.el) return null;
  var timelines = [];
  if (cs.blkTimelineEl && cs.blkTimelineEl.isConnected) {
    timelines.push(cs.blkTimelineEl);
  } else {
    bindTimelineFromDom(cs);
    if (cs.blkTimelineEl && cs.blkTimelineEl.isConnected) timelines.push(cs.blkTimelineEl);
    var all = cs.el.querySelectorAll(".chat-turn-timeline");
    for (var a = 0; a < all.length; a++) {
      if (timelines.indexOf(all[a]) < 0) timelines.push(all[a]);
    }
  }
  for (var t = 0; t < timelines.length; t++) {
    var segs = timelines[t].querySelectorAll("[data-block-id]");
    for (var i = 0; i < segs.length; i++) {
      if (segs[i].dataset.blockId === id) return segs[i];
    }
  }
  return null;
}

export function hasRenderedBlock(cs, blockId) {
  return !!findSegmentByBlockId(cs, blockId);
}

function dedupeSegmentDom(cs, blockId) {
  var id = String(blockId || "");
  if (!id || !cs.el) return;
  var hits = cs.el.querySelectorAll('[data-block-id="' + id + '"]');
  for (var j = 1; j < hits.length; j++) hits[j].remove();
}

function inferKindFromSegment(seg) {
  if (!seg) return "text";
  if (seg.classList.contains("chat-stream-notice")) return "notice";
  if (seg.classList.contains("chat-stream-assistant")) return "text";
  if (seg.querySelector(".tool-step-thinking")) return "thinking";
  if (seg.querySelector(".tool-step")) return "tool";
  return "text";
}

function bindBlockRefsFromSegment(b, seg) {
  b.segEl = seg;
  if (!b.kind || b.kind === "text") {
    var inferred = inferKindFromSegment(seg);
    if (inferred !== "text" || !b.kind) b.kind = inferred;
  }
  if (b.kind === "notice" || b.kind === "render_error") {
    b.noticeEl = seg.querySelector(".system-msg");
  } else if (b.kind === "text") {
    b.bubbleEl = seg.querySelector(".hermes-assistant-bubble");
    b.labelEl = seg.querySelector(".speaker-label");
  } else if (
    b.kind === "thinking" || b.kind === "recall"
    || b.kind === "program_timer" || b.kind === "compaction"
  ) {
    b.rowEl = seg.querySelector(".tool-step");
    b.reasoningEl = seg.querySelector(".tool-step-reasoning");
    b.inputEl = seg.querySelector(".tool-step-input");
  } else {
    b.rowEl = seg.querySelector(".tool-step");
  }
}

function appendToTimeline(timeline, seg) {
  timeline.appendChild(seg);
}

export function rebindTaskTimeline(cs) {
  cs.blkTimelineEl = null;
  cs.blkTimelineRow = null;
  bindTimelineFromDom(cs);
}

function getBlock(cs, blockId) {
  var blocks = ensureBlocksState(cs);
  return blocks[String(blockId || "")] || null;
}

function firstLine(text) {
  var s = String(text || "");
  var n = s.indexOf("\n");
  return n === -1 ? s : s.slice(0, n);
}

function latestLine(text) {
  var s = String(text || "").replace(/\s+$/, "");
  var n = s.lastIndexOf("\n");
  return n === -1 ? s : s.slice(n + 1);
}

function toolSummaryFromInput(input) {
  if (input == null) return "";
  if (typeof input === "string") return firstLine(input);
  if (typeof input !== "object") return String(input);
  if (input.description) return firstLine(input.description);
  if (input.command) return firstLine(input.command);
  if (input.path) return String(input.path);
  if (input.file_path) return String(input.file_path);
  if (input.file_name) return String(input.file_name);
  if (input.name) return String(input.name);
  if (input.question) return firstLine(input.question);
  if (input.op) return String(input.op);
  try {
    return JSON.stringify(input);
  } catch (e) {
    return String(input);
  }
}

function classifyToolVariant(name, isThinking) {
  if (isThinking) return "think";
  var n = String(name || "").toLowerCase();
  if (n === "thinking" || n === "\u601d\u8003") return "think";
  if (n === "terminal") return "bash";
  if (/(grep|glob|search)/.test(n) || /(^|_)ls(_|$)/.test(n)) return "search";
  if (/(^|_)read(_|$)/.test(n)) return "read";
  if (/(write|replace|delete|insert|append)/.test(n)) return "write";
  return "others";
}

function toolDisplayTitle(name, isThinking) {
  if (isThinking) return "\u601d\u8003";
  var n = String(name || "").toLowerCase();
  var map = {
    terminal: "Terminal",
    process: "Process",
    file_access_read: "Read",
    file_access_write: "Write",
    file_access_ls: "List",
    file_access_grep: "Search",
    file_access_glob: "Glob",
    grep: "Grep",
    glob: "Glob",
    semantic_search: "Semantic",
    file_access_replace: "Edit",
    file_access_replace_lines: "Edit",
    file_access_delete: "Delete",
    clarify: "Ask",
    tool_help: "Help",
  };
  if (map[n]) return map[n];
  return name || "Tool";
}

function toolIconSvg(variant) {
  var icons = {
    think: '<path d="M8 1.6A4.4 4.4 0 0 0 3.6 6c0 1.65.86 3.1 2.15 3.9v1.15h4.5V9.9A4.4 4.4 0 0 0 12.4 6 4.4 4.4 0 0 0 8 1.6z" stroke="currentColor" stroke-width="1.3"/><path d="M6.2 12.4h3.6M6.7 13.8h2.6" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
    bash: '<path d="M3.2 5.2L6.4 8 3.2 10.8" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/><path d="M8.2 11.2H13" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>',
    read: '<path d="M4 2.4h5.2L12.4 5.6V13.6H4V2.4z" stroke="currentColor" stroke-width="1.3"/><path d="M9.2 2.4V5.6H12.4" stroke="currentColor" stroke-width="1.3"/><path d="M6 8.2h4M6 10.4h4" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>',
    write: '<path d="M9.1 3.1l3.8 3.8-7.4 7.4H1.7V10.5L9.1 3.1z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>',
    others: '<path d="M8 2.1l1.05 4.05L13.2 7.2l-4.15 1.05L8 12.3 6.95 8.25 2.8 7.2l4.15-1.05L8 2.1z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>',
  };
  icons.search = '<circle cx="7" cy="7" r="4.1" stroke="currentColor" stroke-width="1.4"/><path d="M10.2 10.2L13.8 13.8" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>';
  var d = icons[variant] || icons.others;
  return '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" xmlns="http://www.w3.org/2000/svg">' + d + "</svg>";
}

function extLang(path) {
  var m = String(path || "").match(/\.([a-z0-9]+)$/i);
  return m ? m[1].toLowerCase() : "";
}

function filePathFromInput(input) {
  if (!input || typeof input !== "object") return "";
  return String(input.path || input.file_path || input.file_name || input.file || "");
}

function contentLines(text) {
  if (text == null || text === "") return [];
  var body = String(text);
  if (body.charAt(body.length - 1) === "\n") body = body.slice(0, -1);
  return body.split("\n");
}

function diffHunksFromInput(input, name) {
  if (!input || typeof input !== "object") return null;
  var path = filePathFromInput(input);
  var n = String(name || "").toLowerCase();
  if (n.indexOf("replace_lines") !== -1) {
    if (!Array.isArray(input.edits) || !input.edits.length) return null;
    var hunks = [];
    for (var i = 0; i < input.edits.length; i++) {
      var ed = input.edits[i];
      if (!ed || typeof ed !== "object") return null;
      hunks.push({
        path: path,
        oldText: null,
        newText: ed.new_line != null ? String(ed.new_line) : "",
      });
    }
    return hunks;
  }
  if (input.old_string != null && input.new_string != null) {
    return [{ path: path, oldText: String(input.old_string), newText: String(input.new_string) }];
  }
  if (/(write|insert|append)/.test(n) && input.content != null) {
    return [{ path: path, oldText: null, newText: String(input.content) }];
  }
  return null;
}

function buildDiffRows(diffs) {
  var rows = [];
  var added = 0;
  var removed = 0;
  var seen = {};
  var files = 0;
  var prevPath;
  for (var i = 0; i < diffs.length; i++) {
    var diff = diffs[i];
    var p = diff.path || "";
    if (!seen[p]) {
      seen[p] = 1;
      files += 1;
    }
    if (p !== prevPath) rows.push({ kind: "path", text: p });
    else rows.push({ kind: "gap", text: "\u22ef" });
    prevPath = p;
    if (diff.oldText !== null && diff.oldText !== undefined) {
      var dels = contentLines(diff.oldText);
      for (var d = 0; d < dels.length; d++) {
        rows.push({ kind: "del", text: dels[d] });
        removed += 1;
      }
    }
    var adds = contentLines(diff.newText);
    for (var a = 0; a < adds.length; a++) {
      rows.push({ kind: "add", text: adds[a] });
      added += 1;
    }
  }
  return { rows: rows, added: added, removed: removed, files: files };
}

function copyDiffText(rows) {
  var out = [];
  for (var i = 0; i < rows.length; i++) {
    var row = rows[i];
    if (row.kind === "del") out.push("- " + row.text);
    else if (row.kind === "add") out.push("+ " + row.text);
    else out.push(row.text);
  }
  return out.join("\n");
}

var CODE_MAX_LINES = 16;

function fillCodeLines(bodyEl, text) {
  if (!bodyEl) return;
  var raw = String(text || "");
  var lines = raw.split("\n");
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  if (!lines.length) lines = [""];
  bodyEl.innerHTML = "";
  bodyEl._rawText = raw;
  var hidden = lines.length - CODE_MAX_LINES;
  var expanded = !!bodyEl._expanded;
  var capped = hidden > 0 && !expanded;
  var head = Math.ceil(CODE_MAX_LINES / 2);
  var tail = CODE_MAX_LINES - head;
  function addLine(num, line) {
    var row = document.createElement("div");
    row.className = "tool-code-line";
    var g = document.createElement("span");
    g.className = "tool-code-gutter";
    g.textContent = String(num);
    var c = document.createElement("span");
    c.className = "tool-code-content";
    c.textContent = line;
    row.appendChild(g);
    row.appendChild(c);
    bodyEl.appendChild(row);
  }
  if (!capped) {
    for (var i = 0; i < lines.length; i++) addLine(i + 1, lines[i]);
    if (hidden > 0 && expanded) {
      var fold = document.createElement("button");
      fold.type = "button";
      fold.className = "tool-code-expand";
      fold.textContent = "\u6536\u8d77";
      fold.onclick = function (e) {
        e.stopPropagation();
        bodyEl._expanded = false;
        fillCodeLines(bodyEl, raw);
      };
      bodyEl.appendChild(fold);
    }
    return;
  }
  for (var h = 0; h < head; h++) addLine(h + 1, lines[h]);
  var more = document.createElement("button");
  more.type = "button";
  more.className = "tool-code-expand";
  more.textContent = "\u2026 \u5176\u4f59 " + hidden + " \u884c";
  more.onclick = function (e) {
    e.stopPropagation();
    bodyEl._expanded = true;
    fillCodeLines(bodyEl, raw);
  };
  bodyEl.appendChild(more);
  for (var t = lines.length - tail; t < lines.length; t++) addLine(t + 1, lines[t]);
}

function bindCodeCopy(btn, bodyEl) {
  if (!btn || btn._bound) return;
  btn._bound = true;
  btn.onclick = function (e) {
    e.stopPropagation();
    var text = bodyEl && bodyEl._rawText != null ? bodyEl._rawText : "";
    if (!navigator.clipboard || !navigator.clipboard.writeText) {
      throw new Error("clipboard unavailable");
    }
    navigator.clipboard.writeText(text).then(function () {
      btn.textContent = "\u5df2\u590d\u5236";
      setTimeout(function () { btn.textContent = "\u590d\u5236"; }, 1000);
    });
  };
}

function resetCodeCardChrome(section) {
  if (!section) return;
  section.classList.remove("is-diff", "is-term", "is-search");
  section.removeAttribute("data-running");
  var prompt = section.querySelector(".tool-term-prompt");
  var status = section.querySelector(".tool-term-status");
  var titleEl = section.querySelector(".tool-code-title");
  var copy = section.querySelector(".tool-code-copy");
  var footer = section.querySelector(".tool-diff-footer");
  if (prompt) {
    prompt.hidden = true;
    prompt.innerHTML = "";
  }
  if (status) {
    status.hidden = true;
    status.textContent = "";
  }
  if (titleEl) titleEl.hidden = false;
  if (copy) copy.hidden = false;
  if (footer) footer.hidden = true;
}

function paintCodeCard(section, title, lang, text) {
  if (!section) return;
  resetCodeCardChrome(section);
  var titleEl = section.querySelector(".tool-code-title");
  var langEl = section.querySelector(".tool-code-lang");
  var body = section.querySelector(".tool-code-body");
  var copy = section.querySelector(".tool-code-copy");
  if (titleEl) titleEl.textContent = title || "";
  if (langEl) langEl.textContent = lang || "";
  fillCodeLines(body, text);
  bindCodeCopy(copy, body);
}

function fillDiffLines(bodyEl, rows) {
  if (!bodyEl) return;
  bodyEl.innerHTML = "";
  bodyEl._rawText = copyDiffText(rows);
  var hidden = rows.length - CODE_MAX_LINES;
  var expanded = !!bodyEl._expanded;
  var capped = hidden > 0 && !expanded;
  var headN = Math.ceil(CODE_MAX_LINES / 2);
  var tailN = CODE_MAX_LINES - headN;
  function addRow(row) {
    var el = document.createElement("div");
    el.className = "tool-diff-line tool-diff-" + row.kind;
    el.textContent = row.text;
    bodyEl.appendChild(el);
  }
  var head = capped ? rows.slice(0, headN) : rows;
  var tail = capped ? rows.slice(rows.length - tailN) : [];
  for (var i = 0; i < head.length; i++) addRow(head[i]);
  if (hidden > 0) {
    var more = document.createElement("button");
    more.type = "button";
    more.className = "tool-code-expand";
    more.textContent = expanded ? "\u6536\u8d77" : "\u2026 \u5176\u4f59 " + hidden + " \u884c";
    more.onclick = function (e) {
      e.stopPropagation();
      bodyEl._expanded = !expanded;
      fillDiffLines(bodyEl, rows);
    };
    bodyEl.appendChild(more);
  }
  for (var t = 0; t < tail.length; t++) addRow(tail[t]);
}

function promptLabel(cwd) {
  var trimmed = String(cwd || "").replace(/[/\\]+$/, "");
  if (!trimmed) return "$";
  var segs = trimmed.split(/[/\\]/);
  var last = segs[segs.length - 1];
  return last || trimmed;
}

function parseTermResult(text) {
  var raw = String(text || "");
  var trimmed = raw.replace(/^\s+/, "").replace(/\s+$/, "");
  if (!trimmed || trimmed.charAt(0) !== "{") {
    return { output: raw, exitCode: undefined, error: "" };
  }
  var obj;
  try {
    obj = JSON.parse(trimmed);
  } catch (e) {
    // 以 { 开头但不合法,按纯文本走 —— 这条路下面本来就有。
    return { output: raw, exitCode: undefined, error: "" };
  }
  if (!obj || typeof obj !== "object" || (!("output" in obj) && !("exit_code" in obj))) {
    return { output: raw, exitCode: undefined, error: "" };
  }
  return {
    output: obj.output != null ? String(obj.output) : "",
    exitCode: typeof obj.exit_code === "number" ? obj.exit_code : undefined,
    error: obj.error ? String(obj.error) : "",
  };
}

function fillTermLines(bodyEl, text) {
  if (!bodyEl) return;
  var raw = String(text || "");
  bodyEl.innerHTML = "";
  bodyEl._rawText = raw;
  var lines = contentLines(raw);
  var visible = false;
  for (var v = 0; v < lines.length; v++) {
    if (lines[v].replace(/\s+/g, "") !== "") {
      visible = true;
      break;
    }
  }
  if (!visible) {
    var empty = document.createElement("div");
    empty.className = "tool-term-empty";
    empty.textContent = "\u65e0\u8f93\u51fa";
    bodyEl.appendChild(empty);
    return;
  }
  var hidden = lines.length - CODE_MAX_LINES;
  var expanded = !!bodyEl._expanded;
  var capped = hidden > 0 && !expanded;
  var headN = Math.ceil(CODE_MAX_LINES / 2);
  var tailN = CODE_MAX_LINES - headN;
  function addLine(line) {
    var el = document.createElement("div");
    el.className = "tool-term-line";
    el.textContent = line;
    bodyEl.appendChild(el);
  }
  var head = capped ? lines.slice(0, headN) : lines;
  var tail = capped ? lines.slice(lines.length - tailN) : [];
  for (var i = 0; i < head.length; i++) addLine(head[i]);
  if (hidden > 0) {
    var more = document.createElement("button");
    more.type = "button";
    more.className = "tool-code-expand";
    more.textContent = expanded ? "\u6536\u8d77" : "\u2026 \u5176\u4f59 " + hidden + " \u884c";
    more.onclick = function (e) {
      e.stopPropagation();
      bodyEl._expanded = !expanded;
      fillTermLines(bodyEl, raw);
    };
    bodyEl.appendChild(more);
  }
  for (var t = 0; t < tail.length; t++) addLine(tail[t]);
}

function paintTermCard(section, opts) {
  if (!section) return;
  resetCodeCardChrome(section);
  section.classList.add("is-term");
  var running = !!opts.running;
  if (running) section.setAttribute("data-running", "");
  var prompt = section.querySelector(".tool-term-prompt");
  var status = section.querySelector(".tool-term-status");
  var titleEl = section.querySelector(".tool-code-title");
  var langEl = section.querySelector(".tool-code-lang");
  var body = section.querySelector(".tool-code-body");
  var copy = section.querySelector(".tool-code-copy");
  if (titleEl) titleEl.hidden = true;
  if (langEl) langEl.textContent = "";
  var cmd = String(opts.command || "");
  var cmdBody = cmd.charAt(cmd.length - 1) === "\n" ? cmd.slice(0, -1) : cmd;
  var cmdLines = cmdBody.split("\n");
  var failed = !running && ((opts.exitCode !== undefined && opts.exitCode !== 0) || !!opts.error);
  var state = running ? "running" : failed ? "error" : "done";
  var cwd = promptLabel(opts.cwd);
  if (prompt) {
    prompt.hidden = false;
    prompt.innerHTML = "";
    for (var i = 0; i < cmdLines.length; i++) {
      var row = document.createElement("div");
      row.className = "tool-term-prompt-line";
      if (i === 0) {
        var dot = document.createElement("span");
        dot.className = "tool-term-dot";
        dot.setAttribute("data-state", state);
        row.appendChild(dot);
      }
      var cwdEl = document.createElement("span");
      cwdEl.className = "tool-term-cwd";
      cwdEl.textContent = i === 0 ? cwd : "$";
      var cmdEl = document.createElement("span");
      cmdEl.className = "tool-term-command";
      cmdEl.textContent = cmdLines[i];
      row.appendChild(cwdEl);
      row.appendChild(cmdEl);
      prompt.appendChild(row);
    }
  }
  if (status) {
    if (!running && opts.exitCode !== undefined && opts.exitCode !== 0) {
      status.hidden = false;
      status.textContent = "\u9000\u51fa\u7801 " + opts.exitCode;
    } else {
      status.hidden = true;
    }
  }
  var output = opts.output || "";
  if (!output && opts.error) output = opts.error;
  fillTermLines(body, output);
  bindCodeCopy(copy, body);
  var empty = !String(output).replace(/\s+/g, "");
  if (copy) copy.hidden = !!(running || empty);
}

function paintDiffCard(section, diffs) {
  if (!section) return;
  resetCodeCardChrome(section);
  section.classList.add("is-diff");
  var built = buildDiffRows(diffs);
  var titleEl = section.querySelector(".tool-code-title");
  var langEl = section.querySelector(".tool-code-lang");
  var body = section.querySelector(".tool-code-body");
  var copy = section.querySelector(".tool-code-copy");
  var footer = section.querySelector(".tool-diff-footer");
  if (titleEl) titleEl.textContent = "";
  if (langEl) langEl.textContent = "";
  fillDiffLines(body, built.rows);
  bindCodeCopy(copy, body);
  if (footer) {
    footer.hidden = false;
    footer.textContent =
      "\u2514 +" +
      built.added +
      " -" +
      built.removed +
      " \u00b7 " +
      built.files +
      (built.files === 1 ? " file" : " files");
  }
}

function parseSearchResult(text) {
  var raw = String(text || "");
  var trimmed = raw.replace(/^\s+/, "").replace(/\s+$/, "");
  if (!trimmed) return { kind: "paths", paths: [], total: 0 };
  if (trimmed.charAt(0) !== "[") return null;
  var data;
  try {
    data = JSON.parse(trimmed);
  } catch (e) {
    // 返回 null 表示「不是搜索结果」,调用方 refreshToolSurfaces 会退回通用渲染。
    return null;
  }
  if (!Array.isArray(data)) return null;
  if (!data.length) return { kind: "paths", paths: [], total: 0 };
  var first = data[0];
  if (typeof first === "string") {
    return { kind: "paths", paths: data.map(String), total: data.length };
  }
  if (!first || typeof first !== "object") return null;
  if ("matching_lines" in first || "file_name" in first) {
    var files = [];
    var matchTotal = 0;
    for (var i = 0; i < data.length; i++) {
      var item = data[i];
      if (!item || typeof item !== "object") return null;
      var matches = [];
      var lines = Array.isArray(item.matching_lines) ? item.matching_lines : [];
      for (var j = 0; j < lines.length; j++) {
        var m = lines[j];
        if (!m || typeof m !== "object") return null;
        matches.push({
          lineNumber: typeof m.line_number === "number" ? m.line_number : 0,
          line: String(m.line || ""),
        });
      }
      matchTotal += matches.length;
      files.push({ path: String(item.file_name || ""), matches: matches });
    }
    return { kind: "matches", files: files, total: matchTotal };
  }
  if ("name" in first) {
    var paths = [];
    for (var k = 0; k < data.length; k++) {
      var e = data[k];
      if (!e || typeof e !== "object") return null;
      var label = String(e.name || "");
      if (e.type === "directory" && label && label.charAt(label.length - 1) !== "/") {
        label += "/";
      }
      paths.push(label);
    }
    return { kind: "paths", paths: paths, total: paths.length };
  }
  return null;
}

function searchRows(card) {
  if (card.kind === "paths") {
    return card.paths.map(function (path) {
      return { type: "path", path: path };
    });
  }
  var rows = [];
  for (var i = 0; i < card.files.length; i++) {
    var file = card.files[i];
    rows.push({ type: "file", path: file.path, count: file.matches.length });
    for (var j = 0; j < file.matches.length; j++) {
      rows.push({
        type: "match",
        lineNumber: file.matches[j].lineNumber,
        line: file.matches[j].line,
      });
    }
  }
  return rows;
}

function searchCopyText(card) {
  if (card.kind === "paths") return card.paths.join("\n");
  return card.files.map(function (file) {
    return [file.path].concat(file.matches.map(function (m) {
      return m.lineNumber + ": " + m.line;
    })).join("\n");
  }).join("\n\n");
}

function searchSummary(card) {
  if (card.kind === "paths") return card.total + " \u4e2a\u8def\u5f84";
  return card.total + " \u5904\u5339\u914d \u00b7 " + card.files.length + " \u4e2a\u6587\u4ef6";
}

function fillSearchLines(bodyEl, card) {
  if (!bodyEl) return;
  var rows = searchRows(card);
  bodyEl.innerHTML = "";
  bodyEl._rawText = searchCopyText(card);
  if (!rows.length) {
    var empty = document.createElement("div");
    empty.className = "tool-search-empty";
    empty.textContent = "\u65e0\u7ed3\u679c";
    bodyEl.appendChild(empty);
    return;
  }
  var hidden = rows.length - CODE_MAX_LINES;
  var expanded = !!bodyEl._expanded;
  var capped = hidden > 0 && !expanded;
  var headN = Math.ceil(CODE_MAX_LINES / 2);
  var tailN = CODE_MAX_LINES - headN;
  function addRow(row) {
    if (row.type === "file") {
      var head = document.createElement("div");
      head.className = "tool-search-file";
      var pathEl = document.createElement("span");
      pathEl.className = "tool-search-path";
      pathEl.textContent = row.path;
      var countEl = document.createElement("span");
      countEl.className = "tool-search-count";
      countEl.textContent = String(row.count);
      head.appendChild(pathEl);
      head.appendChild(countEl);
      bodyEl.appendChild(head);
      return;
    }
    var el = document.createElement("div");
    el.className = "tool-search-line";
    if (row.type === "match") {
      var num = document.createElement("span");
      num.className = "tool-search-lineno";
      num.textContent = row.lineNumber + ": ";
      el.appendChild(num);
      el.appendChild(document.createTextNode(row.line));
    } else {
      el.textContent = row.path;
    }
    bodyEl.appendChild(el);
  }
  var head = capped ? rows.slice(0, headN) : rows;
  var tail = capped ? rows.slice(rows.length - tailN) : [];
  for (var i = 0; i < head.length; i++) addRow(head[i]);
  if (hidden > 0) {
    var more = document.createElement("button");
    more.type = "button";
    more.className = "tool-code-expand";
    more.textContent = expanded ? "\u6536\u8d77" : "\u2026 \u5176\u4f59 " + hidden + " \u884c";
    more.onclick = function (e) {
      e.stopPropagation();
      bodyEl._expanded = !expanded;
      fillSearchLines(bodyEl, card);
    };
    bodyEl.appendChild(more);
  }
  for (var t = 0; t < tail.length; t++) addRow(tail[t]);
}

function paintSearchCard(section, card) {
  if (!section) return;
  resetCodeCardChrome(section);
  section.classList.add("is-search");
  var titleEl = section.querySelector(".tool-code-title");
  var langEl = section.querySelector(".tool-code-lang");
  var body = section.querySelector(".tool-code-body");
  var copy = section.querySelector(".tool-code-copy");
  if (titleEl) titleEl.textContent = searchSummary(card);
  if (langEl) langEl.textContent = "";
  fillSearchLines(body, card);
  bindCodeCopy(copy, body);
  if (copy) copy.hidden = !card.total;
}

function refreshToolSurfaces(card, inputObj) {
  if (!card) return;
  if (inputObj !== undefined) card._toolInput = inputObj;
  var input = card._toolInput;
  var name = card.getAttribute("data-tool-name") || "";
  var variant = classifyToolVariant(name, card.getAttribute("data-variant") === "think");
  var inn = card.querySelector(".tool-step-io-in");
  var out = card.querySelector(".tool-step-io-out");
  var resEl = card.querySelector(".tool-step-result");
  var todoEl = card.querySelector(".tool-step-todo");
  var resultText = resEl ? String(resEl.textContent || "") : "";
  var hasTodo = !!(todoEl && todoEl.style.display !== "none" && todoEl.innerHTML);
  if (hasTodo) {
    if (inn) inn.hidden = true;
    if (out) out.hidden = true;
    return;
  }
  var path = filePathFromInput(input);
  if ((variant === "read" || variant === "search") && !resultText) {
    if (inn) inn.hidden = true;
    if (out) out.hidden = true;
    return;
  }
  if ((variant === "read" || variant === "search") && resultText) {
    if (variant === "search") {
      var search = parseSearchResult(resultText);
      if (search) {
        if (inn) inn.hidden = true;
        paintSearchCard(out, search);
        if (out) out.hidden = false;
        return;
      }
    }
    var outTitle = path;
    if (!outTitle && input && typeof input === "object") {
      outTitle = String(input.pattern || input.query || input.glob || input.directory || "");
    }
    if (inn) inn.hidden = true;
    var readFmt = formatJsonText(resultText);
    paintCodeCard(out, outTitle || "output", readFmt.json ? "json" : extLang(path), readFmt.text);
    if (out) out.hidden = false;
    return;
  }
  if (variant === "write") {
    var hunks = diffHunksFromInput(input, name);
    if (hunks) {
      if (inn) inn.hidden = true;
      paintDiffCard(out, hunks);
      if (out) out.hidden = false;
      return;
    }
  }
  if (variant === "bash") {
    var cmd = input && input.command ? String(input.command) : "";
    if (cmd) {
      if (inn) inn.hidden = true;
      var parsed = resultText ? parseTermResult(resultText) : null;
      paintTermCard(out, {
        command: cmd,
        cwd: input && (input.workdir || input.cwd) ? String(input.workdir || input.cwd) : "",
        output: parsed ? parsed.output : "",
        error: parsed ? parsed.error : "",
        exitCode: parsed ? parsed.exitCode : undefined,
        running: !parsed,
      });
      if (out) out.hidden = false;
      return;
    }
  }
  var inText = input == null ? "" : formatToolInput(input);
  if (inn) {
    if (inText) {
      paintCodeCard(inn, "IN", typeof input === "object" ? "json" : "", inText);
      inn.hidden = false;
    } else {
      inn.hidden = true;
    }
  }
  if (out) {
    if (resultText) {
      var outFmt = formatJsonText(resultText);
      paintCodeCard(out, "OUT", outFmt.json ? "json" : "", outFmt.text);
      out.hidden = false;
    } else {
      out.hidden = true;
    }
  }
}

function toolCardOf(el) {
  if (!el) return null;
  if (el.classList && el.classList.contains("tool-step-card")) return el;
  return el.querySelector ? el.querySelector(".tool-step-card") : null;
}

function setToolSummary(card, text, isError) {
  var sum = card && card.querySelector(".tool-step-summary");
  var sep = card && card.querySelector(".tool-step-sep");
  if (!sum) return;
  var line = String(text || "").replace(/\s+/g, " ").trim();
  sum.textContent = line;
  sum.classList.toggle("is-error", !!isError);
  if (sep) sep.style.display = line ? "" : "none";
}

function setToolStatusClass(toolEl, status) {
  toolEl.classList.remove(
    "tool-step-waiting",
    "tool-step-streaming",
    "tool-step-done",
    "tool-step-error"
  );
  var st = toolEl.querySelector(".tool-step-status");
  var card = toolCardOf(toolEl);
  var state = "waiting";
  if (status === "error") {
    toolEl.classList.add("tool-step-error");
    state = "error";
    if (st) {
      st.className = "tool-step-status tool-step-status-error";
      st.textContent = "error";
    }
  } else if (status === "done") {
    toolEl.classList.add("tool-step-done");
    state = "ok";
    if (st) {
      st.className = "tool-step-status tool-step-status-done";
      st.textContent = "done";
    }
  } else if (status === "running") {
    toolEl.classList.add("tool-step-streaming");
    state = "running";
    if (st) {
      st.className = "tool-step-status tool-step-status-running";
      st.textContent = "running";
    }
  } else {
    toolEl.classList.add("tool-step-waiting");
    if (st) {
      st.className = "tool-step-status tool-step-status-waiting";
      st.textContent = "wait";
    }
  }
  if (card) card.setAttribute("data-state", state);
}

function bindHeaderToggle(card) {
  var header = card.querySelector(".tool-step-header");
  if (!header) return;
  header.onclick = function () {
    setBodyExpanded(card, !card.classList.contains("is-open"));
  };
}

function codeCardHtml(kind) {
  return (
    '<div class="tool-code-card tool-step-io-' + kind + '" hidden>' +
    '<div class="tool-code-banner">' +
    '<div class="tool-term-prompt" hidden></div>' +
    '<span class="tool-code-title"></span>' +
    '<span class="tool-code-actions">' +
    '<span class="tool-term-status" hidden></span>' +
    '<span class="tool-code-lang"></span>' +
    '<button type="button" class="tool-code-copy">\u590d\u5236</button>' +
    "</span></div>" +
    '<div class="tool-code-body"></div>' +
    '<div class="tool-diff-footer" hidden></div></div>'
  );
}

function buildToolCard(label, opts) {
  var isThinking = !!(opts && opts.thinking);
  var toolName = (opts && opts.toolName) || (isThinking ? "thinking" : "");
  var variant = classifyToolVariant(toolName, isThinking);
  var card = document.createElement("div");
  card.className = "tool-step-card";
  card.setAttribute("data-state", isThinking ? "running" : "waiting");
  card.setAttribute("data-variant", variant);
  if (toolName) card.setAttribute("data-tool-name", toolName);
  card.innerHTML =
    '<div class="tool-step-header">' +
    '<span class="tool-step-leading" aria-hidden="true">' + toolIconSvg(variant) + "</span>" +
    '<span class="tool-step-label">' + escapeHtml(label) + "</span>" +
    '<span class="tool-step-sep" style="display:none"></span>' +
    '<span class="tool-step-summary"></span>' +
    '<span class="tool-step-status tool-step-status-waiting">wait</span>' +
    '<span class="tool-step-duration"></span>' +
    '<span class="tool-step-time"></span>' +
    '<span class="tool-step-chevron" aria-hidden="true"></span>' +
    "</div>" +
    '<div class="tool-step-body">' +
    (isThinking
      ? '<pre class="tool-step-reasoning"></pre>'
      : '<div class="tool-step-io">' +
        '<pre class="tool-step-input" hidden></pre>' +
        '<pre class="tool-step-result" hidden></pre>' +
        '<div class="tool-step-todo" style="display:none;"></div>' +
        codeCardHtml("in") +
        codeCardHtml("out") +
        "</div>") +
    "</div>";
  bindHeaderToggle(card);
  setBodyExpanded(card, false);
  return card;
}

function setBodyExpanded(card, expanded) {
  if (!card) return;
  var body = card.querySelector(".tool-step-body");
  card.classList.toggle("is-open", !!expanded);
  if (body) body.hidden = !expanded;
}

function syncIoVisibility(card) {
  refreshToolSurfaces(card);
}

function createSegment(cs, b) {
  var existing = findSegmentByBlockId(cs, b.blockId);
  if (existing) {
    bindBlockRefsFromSegment(b, existing);
    return existing;
  }
  var timeline = ensureTimeline(cs);
  if (!timeline) return null;
  var seg = document.createElement("div");
  seg.dataset.blockId = b.blockId;
  if (b.kind === "notice" || b.kind === "render_error") {
    seg.className = "chat-stream-notice";
    var notice = document.createElement("div");
    notice.className = b.kind === "render_error" ? "system-msg render-error-msg" : "system-msg";
    seg.appendChild(notice);
    b.noticeEl = notice;
  } else if (b.kind === "compaction") {
    // 前段全损压缩的结果。原文已经不在了,摘要正文就放在折叠体里,
    // 收起时只露第一行 —— 和思考、召回那些卡是同一种。
    seg.className = "chat-stream-tool";
    var cRow = document.createElement("div");
    cRow.className = "tool-step tool-step-compaction tool-step-done";
    var cCard = buildToolCard(b.name || "\u4e0a\u4e0b\u6587\u538b\u7f29", { toolName: "compaction" });
    var cStatus = cCard.querySelector(".tool-step-status");
    if (cStatus) {
      cStatus.className = "tool-step-status tool-step-status-done";
      cStatus.textContent = "done";
    }
    var cReasoning = cCard.querySelector(".tool-step-reasoning");
    var cInput = cCard.querySelector(".tool-step-input");
    var cResult = cCard.querySelector(".tool-step-result");
    if (cReasoning) cReasoning.style.display = "none";
    if (cResult) cResult.style.display = "none";
    if (cInput) {
      cInput.hidden = true;
      cInput.textContent = b.text || "";
    }
    setToolSummary(cCard, firstLine(b.text || ""), false);
    refreshToolSurfaces(cCard, b.text || "");
    setBodyExpanded(cCard, false);
    cRow.appendChild(cCard);
    setToolStatusClass(cRow, "done");
    seg.appendChild(cRow);
    b.rowEl = cRow;
    b.inputEl = cInput;
  } else if (b.kind === "text") {
    seg.className = "chat-stream-assistant";
    if (b.name) {
      var label = document.createElement("div");
      label.className = "speaker-label";
      label.textContent = b.name;
      seg.appendChild(label);
      b.labelEl = label;
    }
    var bubble = document.createElement("div");
    bubble.className = "bubble ai-bubble hermes-assistant-bubble";
    seg.appendChild(bubble);
    b.bubbleEl = bubble;
  } else if (b.kind === "thinking") {
    seg.className = "chat-stream-tool";
    var trow = document.createElement("div");
    trow.className = "tool-step tool-step-thinking tool-step-waiting";
    var tcard = buildToolCard("\u601d\u8003", { thinking: true, toolName: "thinking" });
    var st = tcard.querySelector(".tool-step-status");
    if (st) {
      st.className = "tool-step-status tool-step-status-running";
      st.textContent = "thinking";
    }
    tcard.setAttribute("data-state", "running");
    var inEl = tcard.querySelector(".tool-step-input");
    var rsEl = tcard.querySelector(".tool-step-result");
    if (inEl) inEl.style.display = "none";
    if (rsEl) rsEl.style.display = "none";
    setBodyExpanded(tcard, false);
    trow.appendChild(tcard);
    seg.appendChild(trow);
    b.rowEl = trow;
    b.reasoningEl = tcard.querySelector(".tool-step-reasoning");
  } else if (b.kind === "recall") {
    seg.className = "chat-stream-tool";
    var recallRow = document.createElement("div");
    recallRow.className = "tool-step tool-step-recall tool-step-done";
    var recallCard = buildToolCard(b.name || "Agent \u53ec\u56de", { toolName: "recall" });
    var recallStatus = recallCard.querySelector(".tool-step-status");
    if (recallStatus) {
      recallStatus.className = "tool-step-status tool-step-status-done";
      recallStatus.textContent = "done";
    }
    var recallReasoning = recallCard.querySelector(".tool-step-reasoning");
    var recallInput = recallCard.querySelector(".tool-step-input");
    var recallResult = recallCard.querySelector(".tool-step-result");
    if (recallReasoning) recallReasoning.style.display = "none";
    if (recallResult) recallResult.style.display = "none";
    if (recallInput) {
      recallInput.hidden = true;
      recallInput.textContent = b.text || "";
    }
    setToolSummary(recallCard, firstLine(b.text || ""), false);
    refreshToolSurfaces(recallCard, b.text || "");
    setBodyExpanded(recallCard, true);
    recallRow.appendChild(recallCard);
    setToolStatusClass(recallRow, "done");
    seg.appendChild(recallRow);
    b.rowEl = recallRow;
    b.inputEl = recallInput;
  } else if (b.kind === "program_timer") {
    seg.className = "chat-stream-tool";
    var pRow = document.createElement("div");
    pRow.className = "tool-step tool-step-program tool-step-done";
    var pCard = buildToolCard(b.name || "程序定时", { toolName: "program_timer" });
    var pStatus = pCard.querySelector(".tool-step-status");
    if (pStatus) {
      pStatus.className = "tool-step-status tool-step-status-done";
      pStatus.textContent = "done";
    }
    var pReasoning = pCard.querySelector(".tool-step-reasoning");
    var pInput = pCard.querySelector(".tool-step-input");
    var pResult = pCard.querySelector(".tool-step-result");
    if (pReasoning) pReasoning.style.display = "none";
    if (pResult) pResult.style.display = "none";
    if (pInput) {
      pInput.hidden = true;
      pInput.textContent = b.text || "";
    }
    setToolSummary(pCard, firstLine(b.text || ""), false);
    refreshToolSurfaces(pCard, b.text || "");
    setBodyExpanded(pCard, true);
    pRow.appendChild(pCard);
    setToolStatusClass(pRow, "done");
    seg.appendChild(pRow);
    b.rowEl = pRow;
    b.inputEl = pInput;
  } else {
    seg.className = "chat-stream-tool";
    var row = document.createElement("div");
    row.className = "tool-step tool-step-waiting";
    if (b.blockId) row.dataset.toolId = b.blockId;
    row.dataset.toolName = String(b.name || "").trim().toLowerCase();
    if (isTodoToolName(b.name)) row.dataset.todoTool = "1";
    var label = isTodoToolName(b.name) ? todoToolDisplayLabel(b.name) : toolDisplayTitle(b.name || "tool");
    var card = buildToolCard(label, { toolName: b.name || "tool" });
    setBodyExpanded(card, false);
    row.appendChild(card);
    seg.appendChild(row);
    b.rowEl = row;
    if (!cs.hermesToolElements) cs.hermesToolElements = {};
    cs.hermesToolElements[b.blockId] = row;
  }
  appendToTimeline(timeline, seg);
  b.segEl = seg;
  return seg;
}

function renderTextBlock(cs, b) {
  if (!b.bubbleEl || !b.bubbleEl.isConnected) {
    var seg = findSegmentByBlockId(cs, b.blockId);
    if (seg) bindBlockRefsFromSegment(b, seg);
    else createSegment(cs, b);
  }
  if (!b.bubbleEl) return;
  syncSpeakerLabel(b);
  var text = b.text || "";
  if (!text) {
    b.bubbleEl.textContent = "";
    return;
  }
  b.bubbleEl.innerHTML = formatContent(text);
  b.bubbleEl.querySelectorAll("pre code").forEach(function (el) {
    if (typeof hljs !== "undefined") hljs.highlightElement(el);
  });
}

// 多角色群聊:气泡上方显示发言人名字(单 agent 对话 name 为空,不显示)
function syncSpeakerLabel(b) {
  if (!b.name) return;
  if (b.labelEl && b.labelEl.isConnected) {
    if (b.labelEl.textContent !== b.name) b.labelEl.textContent = b.name;
    return;
  }
  var seg = b.segEl || (b.bubbleEl && b.bubbleEl.parentNode);
  if (!seg) return;
  var label = seg.querySelector(".speaker-label");
  if (!label) {
    label = document.createElement("div");
    label.className = "speaker-label";
    seg.insertBefore(label, seg.firstChild);
  }
  label.textContent = b.name;
  b.labelEl = label;
}

function renderNoticeBlock(cs, b) {
  if (!b.noticeEl || !b.noticeEl.isConnected) {
    var segN = findSegmentByBlockId(cs, b.blockId);
    if (segN) bindBlockRefsFromSegment(b, segN);
    else createSegment(cs, b);
  }
  if (!b.noticeEl) return;
  b.noticeEl.textContent = b.text || "";
}

function renderThinkingBlock(cs, b) {
  if (!b.reasoningEl || !b.reasoningEl.isConnected) {
    var segT = findSegmentByBlockId(cs, b.blockId);
    if (segT) bindBlockRefsFromSegment(b, segT);
    else createSegment(cs, b);
  }
  if (!b.reasoningEl) return;
  var text = b.text || "";
  b.reasoningEl.textContent = text;
  b.reasoningEl.style.display = "block";
  var card = toolCardOf(b.rowEl);
  var running = b.status !== "done" && b.status !== "error";
  setToolSummary(card, running ? latestLine(text) : firstLine(text), false);
  if (b.status === "done" && b.rowEl) {
    setToolStatusClass(b.rowEl, "done");
  } else if (b.rowEl) {
    setToolStatusClass(b.rowEl, "running");
  }
}

function applyToolInput(cs, b) {
  var row = b.rowEl;
  if (!row) return;
  var inputEl = row.querySelector(".tool-step-input");
  if (!applyTodoToolStart(row, b.name, b.input)) {
    if (inputEl) inputEl.textContent = formatToolInput(b.input);
  }
  var card = toolCardOf(row);
  if (card && !card.classList.contains("is-error-summary")) {
    setToolSummary(card, toolSummaryFromInput(b.input), false);
  }
  refreshToolSurfaces(card, b.input);
}

function applyToolResult(cs, b) {
  var row = b.rowEl;
  if (!row) return;
  var res = row.querySelector(".tool-step-result");
  if (!res) return;
  var preview = b.result || "";
  if (!preview) return;
  res.classList.remove("tool-step-result-todos");
  res.hidden = true;
  var todoMount = row.querySelector(".tool-step-todo");
  if (todoMount && mountTodoToolResult(todoMount, preview)) {
    todoMount.style.display = "block";
    var inputPre = row.querySelector(".tool-step-input");
    if (inputPre) inputPre.hidden = true;
  } else {
    if (todoMount) {
      todoMount.style.display = "none";
      todoMount.innerHTML = "";
    }
    if (b.status === "error") {
      var errCard = toolCardOf(row);
      setToolSummary(errCard, firstLine(preview), true);
      if (errCard) errCard.classList.add("is-error-summary");
    }
    res.textContent = preview;
  }
  refreshToolSurfaces(toolCardOf(row), b.input);
}

function applyToolMedia(cs, b) {
  var row = b.rowEl;
  var media = b.media;
  if (!row || !media) return;
  var areaKey = b.blockId;
  var skill = media.skill || b.mediaSkill || b.name || "exec_skill";
  var resPre = row.querySelector(".tool-step-result");
  if (resPre) resPre.style.display = "none";

  var rtype = String(media.mediaType || media.media_type || media.type || "").toLowerCase();
  if (rtype === "text") {
    var text = media.textContent || media.content || media.mediaContent || "";
    if (finishTextMediaBubble(row, text, areaKey)) {
      setToolStatusClass(row, "done");
    }
    return;
  }

  ensureToolStepMedia(row, areaKey, skill);

  if (isFileMediaPayload(media)) {
    var fileMounted = finishFileMediaBubble(row, media, areaKey, media.textContent || "");
    if (!fileMounted) {
      fileMounted = finishFileMediaBubble(
        row,
        media.content || media.mediaContent || media.error || resolveMediaContentHtml(media) || "",
        areaKey,
        media.textContent || ""
      );
    }
    if (fileMounted) {
      setToolStatusClass(row, "done");
      return;
    }
  }

  var mediaVersion = String(media.bgTaskId || media.mediaTaskId || "");
  var content = resolveMediaContentHtml(media);
  if (finishFileMediaBubble(row, content, areaKey, media.textContent || "")) {
    setToolStatusClass(row, "done");
    return;
  }
  finishMediaTaskBubble(row, content, areaKey, media.textContent || "", mediaVersion);
  setToolStatusClass(row, "done");
}

function renderClarify(cs, b) {
  var row = b.rowEl;
  var clarify = b.clarify;
  if (!row || !clarify) return;
  var card = row.querySelector(".tool-step-card");
  var body = card && card.querySelector(".tool-step-body");
  if (!body || body.querySelector(".tool-step-clarify")) return;
  var question = clarify.question || "";
  var choices = Array.isArray(clarify.choices) ? clarify.choices : [];
  var answered = clarify.answer != null && String(clarify.answer).length > 0;
  var wrap = document.createElement("div");
  wrap.className = "tool-step-clarify";
  var html = '<div class="clarify-question">' + escapeHtml(question) + "</div>";
  if (choices.length) {
    html += '<div class="clarify-options">';
    for (var i = 0; i < choices.length; i++) {
      html += '<button type="button" class="clarify-option" data-answer="' + escapeHtml(choices[i]) + '">' + (i + 1) + ". " + escapeHtml(choices[i]) + "</button>";
    }
    html += "</div>";
  }
  if (!answered) {
    html += '<div class="clarify-input-row"><input type="text" class="clarify-input" placeholder="\u8f93\u5165\u4f60\u7684\u56de\u7b54..."><button type="button" class="clarify-send">\u53d1\u9001</button></div>';
  }
  html += '<div class="clarify-answered"' + (answered ? "" : ' style="display:none"') + ">" + (answered ? "\u5df2\u56de\u7b54\uff1a" + escapeHtml(String(clarify.answer)) : "") + "</div>";
  wrap.innerHTML = html;
  body.appendChild(wrap);
  setBodyExpanded(card, true);
  if (answered) return;
  function submit(answer) {
    answer = String(answer || "").trim();
    if (!answer) return;
    if (state.api && state.api.sendWsMsg) {
      state.api.sendWsMsg("clarify_respond", {
        toolCallId: b.blockId,
        answer: answer,
        threadId: state.currentConversationId || "",
      });
    }
    var ans = wrap.querySelector(".clarify-answered");
    if (ans) {
      ans.textContent = "\u5df2\u56de\u7b54\uff1a" + answer;
      ans.style.display = "";
    }
    ["clarify-options", "clarify-input-row"].forEach(function (cls) {
      var n = wrap.querySelector("." + cls);
      if (n) n.style.display = "none";
    });
  }
  var optBtns = wrap.querySelectorAll(".clarify-option");
  Array.prototype.forEach.call(optBtns, function (btn) {
    btn.onclick = function () { submit(btn.getAttribute("data-answer")); };
  });
  var input = wrap.querySelector(".clarify-input");
  var sendBtn = wrap.querySelector(".clarify-send");
  if (sendBtn && input) sendBtn.onclick = function () { submit(input.value); };
  if (input) input.addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); submit(input.value); }
  });
}

function parseLeadingJsonObject(text) {
  var raw = String(text || "");
  var start = raw.indexOf("{");
  if (start < 0) return null;
  var depth = 0;
  var inStr = false;
  var esc = false;
  for (var i = start; i < raw.length; i++) {
    var ch = raw.charAt(i);
    if (inStr) {
      if (esc) {
        esc = false;
        continue;
      }
      if (ch === "\\") {
        esc = true;
        continue;
      }
      if (ch === "\"") inStr = false;
      continue;
    }
    if (ch === "\"") {
      inStr = true;
      continue;
    }
    if (ch === "{") depth++;
    else if (ch === "}") {
      depth--;
      if (depth === 0) {
        return JSON.parse(raw.slice(start, i + 1));
      }
    }
  }
  return null;
}

function viewImageMediaFromBlock(b) {
  var obj = parseLeadingJsonObject(b.result);
  if (!obj || obj.ok === false) return null;
  var paths = Array.isArray(obj.paths) ? obj.paths : obj.path ? [obj.path] : [];
  var cleaned = [];
  for (var i = 0; i < paths.length; i++) {
    var p = String(paths[i] || "").trim();
    if (p) cleaned.push(p);
  }
  if (!cleaned.length) return null;
  var md = [];
  for (var j = 0; j < cleaned.length; j++) {
    md.push("![image](" + cleaned[j] + ")");
  }
  return {
    status: "succeeded",
    skill: "view_image",
    toolName: "view_image",
    mediaType: "image",
    content: md.join("\n"),
    mediaContent: md.join("\n"),
    urls: cleaned,
  };
}

function renderToolBlock(cs, b) {
  if (!b.rowEl || !b.rowEl.isConnected) {
    var segTool = findSegmentByBlockId(cs, b.blockId);
    if (segTool) {
      bindBlockRefsFromSegment(b, segTool);
      if (!cs.hermesToolElements) cs.hermesToolElements = {};
      cs.hermesToolElements[b.blockId] = b.rowEl;
    } else {
      createSegment(cs, b);
    }
  }
  if (!b.rowEl) return;
  applyToolInput(cs, b);
  if (b.clarify) renderClarify(cs, b);
  if (!b.media && b.status === "done" && String(b.name || "") === "view_image") {
    var viewed = viewImageMediaFromBlock(b);
    if (viewed) b.media = viewed;
  }
  if (b.media) {
    applyToolMedia(cs, b);
    if (String(b.name || "") === "view_image") {
      var outCard = b.rowEl.querySelector(".tool-step-io-out");
      if (outCard) outCard.hidden = true;
    }
  } else if (b.status === "done" || b.status === "error") {
    applyToolResult(cs, b);
  }
  if (b.status === "done" || b.status === "error") {
    var toolSeg = b.rowEl.closest ? b.rowEl.closest(".chat-stream-tool") : null;
    var proseNext = toolSeg && toolSeg.nextElementSibling && toolSeg.nextElementSibling.classList.contains("render-text-prose");
    var hasMedia = b.rowEl.querySelector(".media-image-content") || b.rowEl.querySelector(".media-image-area") || b.rowEl.querySelector(".media-text-box") || proseNext;
    if (!(b.media && hasMedia)) {
      setToolStatusClass(b.rowEl, b.status);
    } else {
      setToolStatusClass(b.rowEl, b.status === "error" ? "error" : "done");
    }
    var dur = b.rowEl.querySelector(".tool-step-duration");
    if (dur && b.durationMs != null) dur.textContent = (b.durationMs / 1000).toFixed(1) + "s";
    var tm = b.rowEl.querySelector(".tool-step-time");
    if (tm && b.completedAt != null) {
      var d = new Date(b.completedAt);
      tm.textContent = d.getHours().toString().padStart(2, "0") + ":" +
        d.getMinutes().toString().padStart(2, "0") + ":" +
        d.getSeconds().toString().padStart(2, "0");
    }
  } else {
    setToolStatusClass(b.rowEl, "waiting");
  }
}

function renderRecallBlock(cs, b) {
  if (!b.rowEl || !b.rowEl.isConnected) {
    var segRecall = findSegmentByBlockId(cs, b.blockId);
    if (segRecall) bindBlockRefsFromSegment(b, segRecall);
    else createSegment(cs, b);
  }
  if (b.inputEl && b.text != null) {
    b.inputEl.textContent = b.text;
    setToolSummary(toolCardOf(b.rowEl), firstLine(b.text), false);
    syncIoVisibility(toolCardOf(b.rowEl));
  }
  var tm = b.rowEl && b.rowEl.querySelector(".tool-step-time");
  if (tm && b.completedAt != null) {
    var d = new Date(b.completedAt);
    tm.textContent = d.getHours().toString().padStart(2, "0") + ":" +
      d.getMinutes().toString().padStart(2, "0") + ":" +
      d.getSeconds().toString().padStart(2, "0");
  }
  if (b.rowEl) setToolStatusClass(b.rowEl, "done");
}

function recallSourceLabel(source) {
  var s = String(source || "").trim();
  if (s === "timer") return "\u5b9a\u65f6\u4efb\u52a1";
  if (s === "media") return "\u5a92\u4f53\u4efb\u52a1";
  if (s === "http") return "HTTP";
  if (s === "skill") return "\u6280\u80fd";
  if (s === "system") return "\u7cfb\u7edf";
  return s || "\u7cfb\u7edf";
}

export function renderAgentRecallTrigger(cs, content, source, taskId) {
  if (!cs) return null;
  var tid = String(taskId || cs.currentTaskId || "").trim();
  if (tid) cs.currentTaskId = tid;
  var blockId = "recall:" + (tid || "pending");
  var label = "Agent \u53ec\u56de \u00b7 " + recallSourceLabel(source);
  return upsertBlock(cs, {
    blockId: blockId,
    kind: "recall",
    name: label,
    text: String(content || ""),
    status: "done",
  });
}

function renderBlock(cs, b) {
  removeTurnWaiting(cs);
  if (b.kind === "text") renderTextBlock(cs, b);
  else if (b.kind === "notice" || b.kind === "render_error") renderNoticeBlock(cs, b);
  else if (b.kind === "compaction") renderRecallBlock(cs, b);
  else if (b.kind === "thinking") renderThinkingBlock(cs, b);
  else if (b.kind === "recall") renderRecallBlock(cs, b);
  else if (b.kind === "program_timer") renderRecallBlock(cs, b);
  else renderToolBlock(cs, b);
  scrollToBottom(cs.el);
}

export function upsertBlock(cs, data) {
  var blocks = ensureBlocksState(cs);
  var id = String(data.blockId || "");
  if (!id) return;
  var b = blocks[id];
  if (!b) {
    b = {
      blockId: id,
      kind: data.kind || "text",
      name: data.name || "",
      input: data.input || {},
      text: data.text || "",
      result: data.result || "",
      status: data.status || "open",
      durationMs: data.durationMs != null ? data.durationMs : null,
      completedAt: data.completedAt != null ? data.completedAt : null,
      media: data.media || null,
      clarify: data.clarify || null,
      mediaSkill: data.mediaSkill || "",
    };
    var domSeg = findSegmentByBlockId(cs, id);
    if (domSeg) bindBlockRefsFromSegment(b, domSeg);
    blocks[id] = b;
    if (!b.segEl) createSegment(cs, b);
  } else {
    if (data.kind) b.kind = data.kind;
    if (data.name) b.name = data.name;
    if (data.input && Object.keys(data.input).length) b.input = data.input;
    if (data.text != null) b.text = data.text;
    if (data.result != null) b.result = data.result;
    if (data.status) b.status = data.status;
    if (data.durationMs != null) b.durationMs = data.durationMs;
    if (data.completedAt != null) b.completedAt = data.completedAt;
    if (data.media) b.media = Object.assign({}, b.media || {}, data.media);
    if (data.clarify) b.clarify = data.clarify;
    if (data.mediaSkill) b.mediaSkill = data.mediaSkill;
  }
  renderBlock(cs, b);
  dedupeSegmentDom(cs, id);
  return b;
}

export function blockOpen(cs, data) {
  upsertBlock(cs, {
    blockId: data.blockId,
    kind: data.kind,
    name: data.name,
    input: data.input,
    status: "open",
    mediaSkill: data.mediaSkill,
  });
}

// 撤掉一个块(例如某个发言人一个字都没输出,不要留空气泡)
export function blockRemove(cs, data) {
  var id = String((data && data.blockId) || "");
  if (!id || !cs) return;
  var blocks = ensureBlocksState(cs);
  var b = blocks[id];
  if (b && b.segEl && b.segEl.parentNode) b.segEl.parentNode.removeChild(b.segEl);
  if (cs.el) {
    var hits = cs.el.querySelectorAll('[data-block-id="' + id + '"]');
    for (var i = 0; i < hits.length; i++) hits[i].remove();
  }
  delete blocks[id];
}

export function blockDelta(cs, data) {
  var b = getBlock(cs, data.blockId);
  if (!b) {
    b = upsertBlock(cs, {
      blockId: data.blockId,
      kind: data.kind || "text",
      status: "open",
    });
  }
  var text = String(data.text || "");
  if (String(data.target || "text") === "result") {
    b.result = (b.result || "") + text;
  } else {
    b.text = (b.text || "") + text;
  }
  renderBlock(cs, b);
}

export function blockEnd(cs, data) {
  var b = getBlock(cs, data.blockId);
  if (!b) {
    b = upsertBlock(cs, {
      blockId: data.blockId,
      kind: data.kind || "tool",
    });
  }
  b.status = data.status || "done";
  if (data.name) b.name = data.name;
  if (data.result != null) b.result = String(data.result);
  if (data.content != null) b.text = String(data.content);
  if (data.durationMs != null) b.durationMs = data.durationMs;
  if (data.completedAt != null) b.completedAt = data.completedAt;
  if (data.mediaSkill) b.mediaSkill = data.mediaSkill;
  if (data.media && typeof data.media === "object") {
    blockPatch(cs, {
      blockId: data.blockId,
      media: data.media,
      status: data.status || b.status,
    });
    return;
  }
  renderBlock(cs, b);
}

export function blockPatch(cs, data) {
  var b = getBlock(cs, data.blockId);
  if (!b) {
    b = upsertBlock(cs, { blockId: data.blockId, kind: "tool", status: "open" });
  }
  if (data.media) {
    b.media = Object.assign({}, b.media || {}, data.media);
    if (data.status == null) b.status = "done";
  }
  if (data.clarify) b.clarify = data.clarify;
  if (data.planReview) b.planReview = data.planReview;
  if (data.status) b.status = data.status;
  renderBlock(cs, b);
}

export function applyBlocksSnapshot(cs, blocks) {
  if (!Array.isArray(blocks)) return;
  for (var i = 0; i < blocks.length; i++) {
    upsertBlock(cs, blocks[i]);
  }
}

export function applyReconnectBlocksSnapshot(cs, blocks) {
  if (!Array.isArray(blocks)) return;
  rebindTaskTimeline(cs);
  var openOnly = blocks.filter(function (b) {
    return String(b.status || "open") === "open";
  });
  applyBlocksSnapshot(cs, openOnly);
}
