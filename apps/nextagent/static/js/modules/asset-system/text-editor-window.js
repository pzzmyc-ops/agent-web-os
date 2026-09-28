import { API_BASE } from "../../config.js";
import { resolveFileUrl } from "../../asset-url.js";
import { naFetch } from "../../http.js";
import { renderRichTextPreview } from "../../format.js";
import { getEffectiveThreadId, replaceNodeMedia } from "./asset-store.js";
import { getWindow, focusWindow, openWindow, openRegisteredWindow, registerWindowType, updateWindowTitle } from "../window-manager/index.js";

var TEXT_EDITOR_TYPE = "text_editor";
var TEXT_EDITOR_PREFIX = "asset-text-editor-";
var TEXT_EXTENSIONS = new Set([
  ".txt", ".md", ".markdown", ".json", ".jsonl", ".yaml", ".yml", ".csv", ".log", ".ini",
  ".cfg", ".conf", ".xml", ".toml", ".env", ".py", ".js", ".ts", ".tsx", ".jsx",
  ".java", ".go", ".rs", ".sql", ".html", ".htm", ".css", ".scss", ".less", ".sh",
  ".bat", ".ps1", ".rb", ".php", ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".swift",
  ".kt", ".kts", ".dart", ".r", ".vue", ".svelte"
]);

function _editorId(node) {
  return TEXT_EDITOR_PREFIX + (node.id || node.media_id || "unknown");
}

function _truncate(name, max) {
  if (!name || name.length <= max) return name || "";
  return name.substring(0, max - 3) + "...";
}

function _getExtension(name) {
  var text = String(name || "");
  var dot = text.lastIndexOf(".");
  if (dot < 0) return "";
  return text.substring(dot).toLowerCase();
}

function _buildTitle(node, dirty) {
  return (dirty ? "* " : "") + _truncate(node.name || node.path || node.media_id || "文本编辑器", 48);
}

function _buildMimeType(name) {
  var ext = _getExtension(name);
  if (ext === ".md" || ext === ".markdown") return "text/markdown";
  if (ext === ".json" || ext === ".jsonl") return "application/json";
  if (ext === ".yaml" || ext === ".yml") return "application/yaml";
  if (ext === ".xml") return "application/xml";
  if (ext === ".toml") return "application/toml";
  if (ext === ".sql") return "application/sql";
  if (ext === ".js" || ext === ".jsx") return "application/javascript";
  if (ext === ".ts" || ext === ".tsx") return "application/typescript";
  if (ext === ".html" || ext === ".htm") return "text/html";
  if (ext === ".css" || ext === ".scss" || ext === ".less") return "text/css";
  return "text/plain";
}

async function _readNodeText(node) {
  var url = resolveFileUrl(node);
  if (!url) throw new Error("invalid asset uri");
  var resp = await naFetch(url, { cache: "no-store" });
  if (!resp.ok) throw new Error("读取文件失败: HTTP " + resp.status);
  return await resp.text();
}

function _setWindowTitle(id, node, dirty) {
  updateWindowTitle(id, _buildTitle(node, dirty));
}

function _setStatus(statusEl, text) {
  statusEl.textContent = text || "";
}

function _mountEditor(body, win, node, threadId) {
  body.innerHTML = [
    '<div class="te-window">',
    '<div class="te-toolbar">',
    '<div class="te-path"></div>',
    '<div class="te-actions">',
    '<span class="te-status"></span>',
    '<button type="button" class="ae-dialog-btn te-mode-edit-btn">编辑模式</button>',
    '<button type="button" class="ae-dialog-btn te-mode-preview-btn">渲染模式</button>',
    '<button type="button" class="ae-dialog-btn te-copy-btn">复制</button>',
    '<button type="button" class="ae-dialog-btn te-reload-btn">重新加载</button>',
    '<button type="button" class="ae-dialog-btn ae-dialog-btn-primary te-save-btn">保存</button>',
    '</div>',
    '</div>',
    '<div class="te-content">',
    '<textarea class="te-textarea" spellcheck="false"></textarea>',
    '<div class="te-render"></div>',
    '</div>',
    '</div>'
  ].join("");
  body.style.padding = "0";
  body.style.overflow = "hidden";

  var pathEl = body.querySelector(".te-path");
  var statusEl = body.querySelector(".te-status");
  var editModeBtn = body.querySelector(".te-mode-edit-btn");
  var previewModeBtn = body.querySelector(".te-mode-preview-btn");
  var copyBtn = body.querySelector(".te-copy-btn");
  var reloadBtn = body.querySelector(".te-reload-btn");
  var saveBtn = body.querySelector(".te-save-btn");
  var textarea = body.querySelector(".te-textarea");
  var renderEl = body.querySelector(".te-render");

  pathEl.textContent = node.path || node.name || "";
  saveBtn.disabled = true;
  reloadBtn.disabled = true;
  textarea.readOnly = true;

  var originalContent = "";
  var saving = false;
  var reloading = false;
  var statusTimer = 0;
  var latestKnownNode = Object.assign({}, node);
  var currentMode = "edit";
  var previewRendering = false;

  function clearStatusTimer() {
    if (statusTimer) {
      clearTimeout(statusTimer);
      statusTimer = 0;
    }
  }

  function isDirty() {
    return textarea.value !== originalContent;
  }

  function syncDirty() {
    var dirty = isDirty();
    saveBtn.disabled = currentMode === "preview" || saving || reloading || !dirty;
    reloadBtn.disabled = saving || reloading;
    _setWindowTitle(win.id, node, dirty);
  }

  function setPersistentStatus(text) {
    clearStatusTimer();
    _setStatus(statusEl, text || "");
  }

  function getCurrentExtension() {
    return _getExtension(node.name || latestKnownNode.name || "");
  }

  async function refreshPreview() {
    if (currentMode !== "preview") return;
    previewRendering = true;
    renderEl.innerHTML = '<div style="padding:8px 0;color:var(--text-muted,#888);font-size:13px;">正在渲染...</div>';
    try {
      await renderRichTextPreview(renderEl, textarea.value, { extension: getCurrentExtension() });
    } catch (err) {
      renderEl.innerHTML = '<pre style="margin:0;color:#b91c1c;white-space:pre-wrap;">渲染失败\n\n' + (err && err.message ? err.message : String(err)) + '</pre>';
    } finally {
      previewRendering = false;
    }
  }

  function applyMode(mode) {
    currentMode = mode === "preview" ? "preview" : "edit";
    var isPreview = currentMode === "preview";
    textarea.style.display = isPreview ? "none" : "block";
    renderEl.style.display = isPreview ? "block" : "none";
    textarea.readOnly = isPreview || reloading;
    editModeBtn.disabled = false;
    previewModeBtn.disabled = false;
    editModeBtn.classList.toggle("is-active", !isPreview);
    previewModeBtn.classList.toggle("is-active", isPreview);
    saveBtn.disabled = isPreview || saving || reloading || !isDirty();
    if (isPreview) {
      refreshPreview();
    } else {
      textarea.focus();
    }
  }

  function showStatus(text) {
    clearStatusTimer();
    _setStatus(statusEl, text || "");
    if (text) {
      statusTimer = window.setTimeout(function () {
        _setStatus(statusEl, "");
        statusTimer = 0;
      }, 2000);
    }
  }

  function rememberLatestNode(nextNode) {
    latestKnownNode = Object.assign({}, latestKnownNode, nextNode || {});
  }

  function applyLoadedNode(nextNode, text) {
    rememberLatestNode(nextNode);
    if (nextNode && nextNode.name) node.name = nextNode.name;
    if (nextNode && nextNode.path) node.path = nextNode.path;
    if (nextNode && Object.prototype.hasOwnProperty.call(nextNode, "media_id")) {
      node.media_id = nextNode.media_id || "";
    }
    pathEl.textContent = node.path || node.name || "";
    originalContent = text;
    textarea.value = text;
    syncDirty();
    if (currentMode === "preview") {
      refreshPreview();
    }
  }

  async function loadFromRemote(nextNode, options) {
    options = options || {};
    var targetNode = nextNode && nextNode.media_id ? nextNode : latestKnownNode;

    if (!targetNode || !targetNode.media_id) {
      throw new Error("缺少最新文件信息，无法重新加载");
    }

    if (!options.force && isDirty()) {
      return false;
    }

    reloading = true;
    textarea.readOnly = true;
    syncDirty();
    setPersistentStatus(options.loadingText || "正在刷新...");

    try {
      var text = await _readNodeText(targetNode);
      applyLoadedNode(targetNode, text);
      if (options.successText) {
        showStatus(options.successText);
      } else {
        clearStatusTimer();
        _setStatus(statusEl, "");
      }
      return true;
    } catch (err) {
      setPersistentStatus(options.errorText || "刷新失败");
      throw err;
    } finally {
      reloading = false;
      textarea.readOnly = false;
      syncDirty();
    }
  }

  async function copyContent() {
    await navigator.clipboard.writeText(textarea.value);
    showStatus("已复制");
  }

  async function reloadContent() {
    if (saving || reloading) return;
    if (!latestKnownNode || !latestKnownNode.media_id) return;

    if (isDirty()) {
      var ok = window.confirm("重新加载将覆盖当前未保存修改，是否继续？");
      if (!ok) return;
    }

    await loadFromRemote(latestKnownNode, {
      force: true,
      loadingText: "正在刷新...",
      successText: "已重新加载最新内容",
      errorText: "刷新失败",
    });
  }

  async function saveContent() {
    if (saving || reloading || textarea.value === originalContent) return;

    saving = true;
    saveBtn.disabled = true;
    reloadBtn.disabled = true;
    saveBtn.textContent = "保存中...";
    setPersistentStatus("正在保存...");

    try {
      var file = new File([textarea.value], node.name || "untitled.txt", { type: _buildMimeType(node.name) });
      var result = await replaceNodeMedia(threadId, node.id, file, { source: "edited", toolName: "text_editor", nodePath: node.path || "" });

      originalContent = textarea.value;

      if (result.node) {
        rememberLatestNode(result.node);
        if (result.node.name) node.name = result.node.name;
        if (result.node.path) node.path = result.node.path;
      }
      if (result.media_id) node.media_id = result.media_id;

      rememberLatestNode({
        id: node.id,
        name: node.name,
        path: node.path,
        media_id: node.media_id,
      });

      pathEl.textContent = node.path || node.name || "";
      showStatus("已保存");
      syncDirty();
      if (currentMode === "preview") {
        refreshPreview();
      }
    } catch (err) {
      setPersistentStatus("保存失败");
      throw err;
    } finally {
      saving = false;
      saveBtn.textContent = "保存";
      syncDirty();
    }
  }

  editModeBtn.onclick = function () {
    applyMode("edit");
    syncDirty();
  };

  previewModeBtn.onclick = function () {
    applyMode("preview");
    syncDirty();
  };

  copyBtn.onclick = function () {
    copyContent().catch(function (err) {
      alert(err.message || err);
    });
  };

  reloadBtn.onclick = function () {
    reloadContent().catch(function (err) {
      alert(err.message || err);
    });
  };

  saveBtn.onclick = function () {
    saveContent().catch(function (err) {
      alert(err.message || err);
    });
  };

  textarea.addEventListener("input", function () {
    syncDirty();
    if (currentMode === "preview" && !previewRendering) {
      refreshPreview();
    }
  });

  textarea.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
      e.preventDefault();
      saveContent().catch(function (err) {
        alert(err.message || err);
      });
    }
  });

  applyMode("edit");

  loadFromRemote(node, {
    force: true,
    loadingText: "正在加载..."
  }).then(function () {
    if (currentMode === "edit") textarea.focus();
  }).catch(function (err) {
    textarea.value = err.message || String(err);
    setPersistentStatus("加载失败");
  });

  if (Array.isArray(win.cleanup)) {
    win.cleanup.push(function () {
      clearStatusTimer();
    });
  }
}

function _openTextEditorWindow(node, threadId) {
  if (!node || !node.id || !node.media_id) throw new Error("invalid text node");
  var id = _editorId(node);
  var existing = getWindow(id);
  if (existing) {
    focusWindow(id);
    return existing;
  }
  var viewportWidth = Math.max(520, Math.floor(window.innerWidth * 0.82));
  var viewportHeight = Math.max(360, Math.floor(window.innerHeight * 0.82));
  return openWindow(id, {
    type: TEXT_EDITOR_TYPE,
    title: _buildTitle(node, false),
    width: viewportWidth,
    height: viewportHeight,
    minWidth: 420,
    minHeight: 280,
    onMount: function (body, win) {
      _mountEditor(body, win, node, threadId || getEffectiveThreadId());
    }
  });
}

export function isTextEditableNode(node) {
  if (!node || !node.media_id || node.node_type !== "file") return false;
  return TEXT_EXTENSIONS.has(_getExtension(node.name || ""));
}

export function registerTextEditorWindow() {
  registerWindowType(TEXT_EDITOR_TYPE, function (payload) {
    return _openTextEditorWindow(payload.node, payload.threadId);
  });
}

export function initAssetWindows() {
  registerTextEditorWindow();
}

export function openTextEditor(node, options) {
  registerTextEditorWindow();
  return openRegisteredWindow(TEXT_EDITOR_TYPE, {
    node: node,
    threadId: options && options.threadId ? options.threadId : "",
  });
}
