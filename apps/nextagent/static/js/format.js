import { coerceWorkspacePath, stampWorkspaceUrlsInRoot, toWorkspaceReadUrl } from "./asset-url.js";

function escapeHtml(s) {
  const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  return String(s).replace(/[&<>"']/g, function (c) { return map[c]; });
}

function escapeAttrUrl(url) {
  return String(url || "").replace(/"/g, "&quot;");
}

function stampMediaUrlsInRoot(root) {
  stampWorkspaceUrlsInRoot(root);
}

function getMarked() {
  return typeof globalThis.marked !== "undefined" ? globalThis.marked : null;
}

function getHljs() {
  return typeof globalThis.hljs !== "undefined" ? globalThis.hljs : null;
}

function getRenderMathInElement() {
  return typeof globalThis.renderMathInElement !== "undefined" ? globalThis.renderMathInElement : null;
}

function getMermaid() {
  return typeof globalThis.mermaid !== "undefined" ? globalThis.mermaid : null;
}

const markedInstance = getMarked();
var _mermaidInitialized = false;
var _mermaidRenderSeq = 0;
var _quietUnmarkedFence = false;

var VIDEO_EXT_RE = /\.(mp4|webm|mov|avi)(\?|$)/i;
var AUDIO_EXT_RE = /\.(mp3|wav|ogg|flac|m4a|aac)(\?|$)/i;

function isVideoUrl(href) {
  return VIDEO_EXT_RE.test(href);
}

function isVideoLink(href, text) {
  var assetHref = coerceWorkspacePath(href) || href;
  var extMatch = isVideoUrl(href) || isVideoUrl(assetHref);
  return extMatch;
}

function isAudioUrl(href) {
  return AUDIO_EXT_RE.test(href);
}

function isAudioLink(href, text) {
  return isAudioUrl(href) || (/\/media\/[a-f0-9]{32}/i.test(href) && /audio|音频/i.test(text || ""));
}

function buildImageHtml(href, safeTitle, safeAlt) {
  return "<div class=\"chat-image-wrap\"><img src=\"" + href + "\" alt=\"" + safeAlt + "\"" + safeTitle + "></div>";
}

function buildVideoHtml(safeHref) {
  return '<div class="chat-media-wrap"><video controls preload="metadata" class="chat-video"><source src="' + safeHref + '"></video></div>';
}

function buildAudioHtml(safeHref) {
  return '<div class="chat-media-wrap"><audio controls preload="metadata" class="chat-audio"><source src="' + safeHref + '"></audio></div>';
}

function downloadLabelName(text) {
  var raw = String(text || "").trim();
  if (/^download:/i.test(raw)) {
    return raw.slice(raw.indexOf(":") + 1).trim() || "download";
  }
  if (/^(file|download|media)$/i.test(raw)) {
    return "download";
  }
  return "";
}

function buildDownloadHtml(safeHref, filename) {
  var name = escapeHtml(filename || "download");
  return (
    '<div class="chat-file-wrap" data-file-url="' +
    safeHref +
    '" data-file-name="' +
    name +
    '">' +
    '<div class="chat-file-name">' +
    name +
    '</div>' +
    '<button type="button" class="chat-file-download-btn">下载文件</button>' +
    "</div>"
  );
}

function convertDownloadMarkdown(raw) {
  return String(raw || "").replace(
    /\[download:([^\]]*)\]\(([^)]+)\)/gi,
    function (_m, name, href) {
      var mediaHref = toWorkspaceReadUrl(href) || href;
      return buildDownloadHtml(escapeAttrUrl(mediaHref), name || "download");
    }
  );
}

if (markedInstance) {
  markedInstance.use({
    renderer: {
      image(a1, a2, a3) {
        let href, title, text;
        if (typeof a1 === "object" && a1 !== null && "href" in a1) {
          href = a1.href != null ? a1.href : "";
          title = a1.title != null ? a1.title : "";
          text = a1.text != null ? a1.text : "";
        } else {
          href = (a1 != null ? a1 : "") + "";
          title = (a2 != null ? a2 : "") + "";
          text = (a3 != null ? a3 : "") + "";
        }
        const resolvedHref = escapeAttrUrl(toWorkspaceReadUrl(href) || href);
        const safeTitle = title ? " title=\"" + escapeHtml(title) + "\"" : "";
        const safeAlt = escapeHtml(text);
        if (isVideoUrl(href)) return buildVideoHtml(resolvedHref);
        if (isAudioUrl(href)) return buildAudioHtml(resolvedHref);
        return buildImageHtml(resolvedHref, safeTitle, safeAlt);
      },
      link(a1, a2, a3) {
        let href, title, text;
        if (typeof a1 === "object" && a1 !== null && "href" in a1) {
          href = a1.href != null ? a1.href : "";
          title = a1.title != null ? a1.title : "";
          text = a1.tokens ? this.parser.parseInline(a1.tokens) : (a1.text != null ? a1.text : "");
        } else {
          href = (a1 != null ? a1 : "") + "";
          title = (a2 != null ? a2 : "") + "";
          text = (a3 != null ? a3 : "") + "";
        }
        var safeHref = escapeHtml(href);
        var plainText = typeof text === "string" ? text : "";
        var dlName = downloadLabelName(plainText);
        if (dlName) return buildDownloadHtml(escapeAttrUrl(toWorkspaceReadUrl(href) || href), dlName);
        if (isVideoLink(href, plainText)) return buildVideoHtml(escapeAttrUrl(toWorkspaceReadUrl(href) || href));
        if (isAudioLink(href, plainText)) return buildAudioHtml(escapeAttrUrl(toWorkspaceReadUrl(href) || href));
        var titleAttr = title ? ' title="' + escapeHtml(title) + '"' : "";
        return '<a href="' + safeHref + '"' + titleAttr + ' target="_blank" rel="noopener">' + text + '</a>';
      },
      code(a1, a2, a3) {
        let content, lang, escaped;
        if (typeof a1 === "object" && a1 !== null && "text" in a1) {
          content = (a1.text != null ? a1.text : "") + "";
          lang = (a1.lang != null ? a1.lang : "") + "";
          escaped = a1.escaped;
        } else {
          content = (a1 != null ? a1 : "") + "";
          lang = (a2 != null ? a2 : "") + "";
          escaped = a3;
        }
        const safe = escaped ? content : escapeHtml(content);
        if (_quietUnmarkedFence && !lang) {
          return '<pre class="doc-fence"><code>' + safe + "</code></pre>";
        }
        const langLabel = lang || "plaintext";
        return buildFenceCard(langLabel, lang, safe, true);
      }
    }
  });
}

export function formatContent(raw, options) {
  if (typeof raw !== "string") return "";
  if (!raw) return "";
  options = options || {};
  raw = raw.replace(/\[async_task:[^\]]+\]/g, "");
  raw = raw.replace(/\[media_(?:task|pending|done):[^\]]+\]/g, "");
  if (!markedInstance) {
    return '<div style="white-space: pre-wrap;">' + escapeHtml(convertDownloadMarkdown(raw)) + "</div>";
  }
  var html;
  _quietUnmarkedFence = !!options.document;
  try {
    html = markedInstance.parse(raw);
  } finally {
    _quietUnmarkedFence = false;
  }
  const div = document.createElement("div");
  div.innerHTML = html;
  stampMediaUrlsInRoot(div);
  div.querySelectorAll("table").forEach(function (table) {
    if (table.parentElement && table.parentElement.classList.contains("md-table-scroll")) return;
    var wrap = document.createElement("div");
    wrap.className = "md-table-scroll";
    table.parentNode.insertBefore(wrap, table);
    wrap.appendChild(table);
  });
  div.querySelectorAll("p, li, td, th, div, span").forEach(function (el) {
    if (el.querySelector(".chat-file-wrap")) return;
    var text = el.textContent || "";
    if (text.indexOf("[download:") < 0) return;
    if (el.children.length > 0 && !/^\[download:/i.test(text.trim())) return;
    var converted = convertDownloadMarkdown(text);
    if (converted !== text) el.innerHTML = converted;
  });
  div.querySelectorAll("img").forEach(function (img) {
    if (!img.closest(".chat-image-wrap") && !img.closest(".chat-media-wrap")) {
      const wrap = document.createElement("div");
      wrap.className = "chat-image-wrap";
      img.parentNode.insertBefore(wrap, img);
      wrap.appendChild(img);
    }
  });
  div.querySelectorAll("a").forEach(function (a) {
    var href = a.getAttribute("href") || "";
    if (!/\/media\/[a-f0-9]{32}/i.test(href) && !toWorkspaceReadUrl(href)) {
      return;
    }
    if (a.closest(".chat-media-wrap") || a.closest(".chat-image-wrap") || a.closest(".chat-file-wrap")) {
      return;
    }
    var text = a.textContent || "";
    var mediaHref = toWorkspaceReadUrl(href) || href;
    var dlName = downloadLabelName(text);
    if (dlName) {
      var fWrap = document.createElement("div");
      fWrap.innerHTML = buildDownloadHtml(escapeAttrUrl(mediaHref), dlName);
      a.parentNode.replaceChild(fWrap.firstChild, a);
    } else if (isVideoLink(href, text)) {
      var vWrap = document.createElement("div");
      vWrap.className = "chat-media-wrap";
      var video = document.createElement("video");
      video.controls = true;
      video.preload = "metadata";
      video.className = "chat-video";
      var source = document.createElement("source");
      source.src = mediaHref;
      video.appendChild(source);
      vWrap.appendChild(video);
      a.parentNode.replaceChild(vWrap, a);
    } else if (isAudioLink(href, text)) {
      var aWrap = document.createElement("div");
      aWrap.className = "chat-media-wrap";
      var audio = document.createElement("audio");
      audio.controls = true;
      audio.preload = "metadata";
      audio.className = "chat-audio";
      var asource = document.createElement("source");
      asource.src = mediaHref;
      audio.appendChild(asource);
      aWrap.appendChild(audio);
      a.parentNode.replaceChild(aWrap, a);
    }
  });
  div.querySelectorAll("pre code").forEach(function (el) {
    const hljsInstance = getHljs();
    if (hljsInstance) hljsInstance.highlightElement(el);
  });
  function fixCasesLinebreaks(math) {
    return math.replace(/\\begin\{cases\}([\s\S]*?)\\end\{cases\}/g, function (_, inner) {
      let fixed = inner.replace(/\\\s+/g, " \\\\ ");
      fixed = fixed.replace(/\s*\n\s*/g, " \\\\ ");
      fixed = fixed.replace(/(\d+)\s+(\d+)/g, "$1 \\\\ $2");
      return "\\begin{cases}" + fixed + "\\end{cases}";
    });
  }

  const renderMath = getRenderMathInElement();
  if (renderMath) {
    renderMath(div, {
      delimiters: [
        { left: "$$", right: "$$", display: true },
        { left: "\\[", right: "\\]", display: true },
        { left: "\\(", right: "\\)", display: false }
      ],
      throwOnError: false,
      preProcess: fixCasesLinebreaks
    });
  }
  return div.innerHTML;
}

export function initImageLoaders(container) {
  if (!container || !container.querySelectorAll) return;
  container.querySelectorAll(".chat-image-wrap").forEach(function (wrap) {
    const img = wrap.querySelector("img.chat-image-img");
    const loading = wrap.querySelector(".chat-image-loading");
    if (!img || !loading) return;
    if (img.complete && img.naturalWidth > 0) {
      loading.classList.add("loaded");
      img.classList.add("loaded");
      return;
    }
    img.onload = function () {
      loading.classList.add("loaded");
      img.classList.add("loaded");
    };
    img.onerror = function () {
      loading.classList.add("loaded");
      img.classList.add("loaded");
    };
  });
}

function isMarkdownExtension(ext) {
  return ext === ".md" || ext === ".markdown";
}

function isCodeExtension(ext) {
  return [
    ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".jsonl", ".yaml", ".yml", ".xml", ".toml", ".sql",
    ".html", ".htm", ".css", ".scss", ".less", ".sh", ".bat", ".ps1", ".rb", ".php", ".c",
    ".cc", ".cpp", ".h", ".hpp", ".cs", ".java", ".go", ".rs", ".swift", ".kt", ".kts",
    ".dart", ".r", ".vue", ".svelte", ".ini", ".cfg", ".conf", ".env", ".log", ".csv"
  ].indexOf(ext) >= 0;
}

function extensionToCodeLang(ext) {
  return {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".json": "json",
    ".jsonl": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".xml": "xml",
    ".toml": "toml",
    ".sql": "sql",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".sh": "bash",
    ".bat": "bat",
    ".ps1": "powershell",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".swift": "swift",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".dart": "dart",
    ".r": "r",
    ".vue": "vue",
    ".svelte": "svelte",
    ".ini": "ini",
    ".cfg": "ini",
    ".conf": "ini",
    ".env": "bash",
    ".log": "plaintext",
    ".csv": "plaintext"
  }[ext] || "plaintext";
}

function buildFenceCard(label, lang, safe, actions) {
  var act = actions
    ? '<button type="button" class="tool-code-copy">\u590d\u5236</button><button type="button" class="tool-code-copy tool-code-download">\u4e0b\u8f7d</button>'
    : "";
  return (
    '<div class="tool-code-card">' +
    '<div class="tool-code-banner">' +
    '<span class="tool-code-title">' + escapeHtml(label) + "</span>" +
    '<span class="tool-code-actions">' + act + "</span>" +
    "</div>" +
    '<pre><code class="language-' + escapeHtml(lang) + '">' + safe + "</code></pre>" +
    "</div>"
  );
}

function buildCodePreviewHtml(raw, ext) {
  var safe = escapeHtml(typeof raw === "string" ? raw : "");
  var lang = extensionToCodeLang(ext || "");
  return buildFenceCard(lang, lang, safe, false);
}

async function renderMermaidBlocks(container) {
  if (!container || !container.querySelectorAll) return;
  var mermaid = getMermaid();
  if (!mermaid) return;
  if (!_mermaidInitialized) {
    mermaid.initialize({ startOnLoad: false, securityLevel: "loose", theme: "default" });
    _mermaidInitialized = true;
  }
  var nodes = Array.from(container.querySelectorAll("code.language-mermaid"));
  for (var i = 0; i < nodes.length; i++) {
    var codeEl = nodes[i];
    var pre = codeEl.closest("pre");
    if (!pre) continue;
    var host = document.createElement("div");
    host.className = "mermaid-preview-block";
    pre.parentNode.replaceChild(host, pre);
    try {
      _mermaidRenderSeq += 1;
      var renderId = "mermaid-preview-" + _mermaidRenderSeq;
      var result = await mermaid.render(renderId, codeEl.textContent || "");
      host.innerHTML = result.svg;
    } catch (err) {
      host.innerHTML = '<pre style="margin:0;white-space:pre-wrap;color:#b91c1c;">Mermaid 渲染失败\n\n' + escapeHtml(err && err.message ? err.message : String(err)) + '</pre>';
    }
  }
}

export async function renderRichTextPreview(container, raw, options) {
  if (!container) return;
  options = options || {};
  var ext = String(options.extension || "").toLowerCase();
  var text = typeof raw === "string" ? raw : "";
  container.innerHTML = "";
  container.classList.remove("te-render-surface");

  var surface = document.createElement("div");
  surface.className = "te-render-surface";
  container.appendChild(surface);

  if (isMarkdownExtension(ext)) {
    surface.innerHTML = formatContent(text);
    await renderMermaidBlocks(surface);
    return;
  }

  if (isCodeExtension(ext)) {
    surface.innerHTML = buildCodePreviewHtml(text, ext);
    surface.querySelectorAll("pre code").forEach(function (el) {
      var hljsInstance = getHljs();
      if (hljsInstance) hljsInstance.highlightElement(el);
    });
    return;
  }

  var pre = document.createElement("pre");
  pre.style.cssText = "margin:0;padding:12px;white-space:pre-wrap;word-break:break-word;font-family:Consolas,'Courier New',monospace;line-height:1.6;";
  pre.textContent = text;
  surface.appendChild(pre);
}
