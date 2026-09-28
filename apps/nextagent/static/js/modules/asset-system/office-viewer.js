var CDN_JSDELIVR = "https://cdn.jsdelivr.net/npm";
var CDN_ESMSH = "https://esm.sh";

var OFFICE_EXTENSIONS = new Set([
  ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"
]);

var moduleCache = {};

function _loadModule(pkg) {
  if (!moduleCache[pkg]) {
    moduleCache[pkg] = import(CDN_JSDELIVR + "/" + pkg + "/+esm");
  }
  return moduleCache[pkg];
}

function _loadModuleEsmSh(pkg) {
  if (!moduleCache[pkg]) {
    moduleCache[pkg] = import(CDN_ESMSH + "/" + pkg);
  }
  return moduleCache[pkg];
}

function _loadCSS(url) {
  if (document.querySelector('link[href="' + url + '"]')) return;
  var link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = url;
  document.head.appendChild(link);
}

function _cdn(path) {
  return CDN_JSDELIVR + "/" + path;
}

function _getExt(name) {
  if (!name) return "";
  var dot = name.lastIndexOf(".");
  if (dot < 0) return "";
  return name.substring(dot).toLowerCase();
}

function _fetchBuffer(url) {
  return fetch(url, { cache: "no-store" }).then(function (r) {
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.arrayBuffer();
  });
}

function _showLoading(el, text) {
  el.innerHTML = "";
  var d = document.createElement("div");
  d.style.cssText = "display:flex;justify-content:center;align-items:center;height:100%;color:var(--text-muted,#888);font-size:14px;";
  d.textContent = text || "Loading...";
  el.appendChild(d);
}

function _showError(el, msg) {
  el.innerHTML = "";
  var d = document.createElement("div");
  d.style.cssText = "display:flex;justify-content:center;align-items:center;height:100%;color:#e74c3c;font-size:14px;padding:20px;text-align:center;";
  d.textContent = msg;
  el.appendChild(d);
}

function _createWrapper(container) {
  container.innerHTML = "";
  var wrapper = document.createElement("div");
  wrapper.style.cssText = "width:100%;height:100%;overflow:auto;background:var(--bg,#fff);";
  container.appendChild(wrapper);
  return wrapper;
}

export function isOfficeFile(name) {
  return OFFICE_EXTENSIONS.has(_getExt(name));
}

export function renderOffice(body, mediaUrl, fileName) {
  var ext = _getExt(fileName);
  body.style.padding = "0";
  body.style.overflow = "hidden";

  switch (ext) {
    case ".docx":
      _renderDocx(body, mediaUrl);
      break;
    case ".xlsx":
    case ".xls":
      _renderXlsx(body, mediaUrl);
      break;
    case ".pdf":
      _renderPdf(body, mediaUrl);
      break;
    case ".pptx":
      _renderPptx(body, mediaUrl);
      break;
    default:
      body.innerHTML = "";
      var info = document.createElement("div");
      info.style.cssText = "display:flex;flex-direction:column;justify-content:center;align-items:center;height:100%;color:var(--text-muted,#888);font-size:14px;gap:8px;";
      info.innerHTML = "<p>此格式(" + ext + ")暂不支持在线预览</p><p style='font-size:12px;color:var(--text-muted,#aaa);'>支持: .docx .xlsx .xls .pdf .pptx</p>";
      body.appendChild(info);
  }
}

function _renderDocx(body, mediaUrl) {
  _showLoading(body, "正在加载预览组件...");
  _loadCSS(_cdn("@js-preview/docx/lib/index.css"));
  _loadModule("@js-preview/docx").then(function (mod) {
    _showLoading(body, "正在渲染文档...");
    return _fetchBuffer(mediaUrl).then(function (buffer) {
      var wrapper = _createWrapper(body);
      var previewer = mod.default.init(wrapper);
      return previewer.preview(buffer);
    });
  }).catch(function (err) {
    _showError(body, "Word文档加载失败: " + err.message);
  });
}

function _renderXlsx(body, mediaUrl) {
  _showLoading(body, "正在加载预览组件...");
  _loadCSS(_cdn("@js-preview/excel/lib/index.css"));
  _loadModule("@js-preview/excel").then(function (mod) {
    _showLoading(body, "正在渲染表格...");
    return _fetchBuffer(mediaUrl).then(function (buffer) {
      var wrapper = _createWrapper(body);
      var previewer = mod.default.init(wrapper, {
        minColLength: 0,
        showContextmenu: false
      });
      return previewer.preview(buffer);
    });
  }).catch(function (err) {
    _showError(body, "Excel文件加载失败: " + err.message);
  });
}

function _renderPdf(body, mediaUrl) {
  _showLoading(body, "正在加载预览组件...");
  _loadModule("@js-preview/pdf").then(function (mod) {
    _showLoading(body, "正在渲染PDF...");
    return _fetchBuffer(mediaUrl).then(function (buffer) {
      var wrapper = _createWrapper(body);
      var previewer = mod.default.init(wrapper);
      return previewer.preview(buffer);
    });
  }).catch(function (err) {
    _showError(body, "PDF加载失败: " + err.message);
  });
}

function _renderPptx(body, mediaUrl) {
  _showLoading(body, "正在加载预览组件(首次可能较慢)...");
  _loadModuleEsmSh("pptx-preview").then(function (mod) {
    _showLoading(body, "正在渲染演示文稿...");
    return _fetchBuffer(mediaUrl).then(function (buffer) {
      var lastW = 0, lastH = 0;
      var debounceTimer = null;

      function render() {
        var w = body.clientWidth;
        var h = body.clientHeight;
        if (!w || !h || (w === lastW && h === lastH)) return;
        lastW = w;
        lastH = h;
        body.innerHTML = "";
        body.style.padding = "0";
        body.style.overflow = "hidden";
        var previewer = mod.init(body, { width: w, height: h });
        previewer.preview(buffer);
      }

      render();

      if (globalThis.ResizeObserver) {
        new ResizeObserver(function () {
          if (debounceTimer) clearTimeout(debounceTimer);
          debounceTimer = setTimeout(render, 300);
        }).observe(body);
      }
    });
  }).catch(function (err) {
    _showError(body, "PPT加载失败: " + err.message);
  });
}
