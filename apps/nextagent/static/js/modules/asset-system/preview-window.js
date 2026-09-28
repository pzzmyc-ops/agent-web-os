import { openWindow, closeWindow, focusWindow, getWindow, getWindowBody, updateWindowTitle } from "../window-manager/index.js";
import { downloadAsset } from "../../upload.js";
import { openTextEditor, isTextEditableNode } from "./text-editor-window.js";
import { isOfficeFile, renderOffice } from "./office-viewer.js";
import { fetchSiblingImageNodes, getEffectiveThreadId } from "./asset-store.js";
import { resolveFileUrl, workspaceReadUrl, resolveWorkspacePath } from "../../asset-url.js";
import { bindPreviewImage } from "../../media-loader.js";
import { upsertNode } from "../../asset-catalog.js";
import { naFetch } from "../../http.js";

var PREVIEW_ID_PREFIX = "asset-preview-";
var CHEVRON_LEFT_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15.75 19.5 8.25 12l7.5-7.5" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round"></path></svg>';
var CHEVRON_RIGHT_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8.25 4.5 15.75 12l-7.5 7.5" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round"></path></svg>';

function _previewId(node) {
  return PREVIEW_ID_PREFIX + (node.id || node.media_id || "unknown");
}

function _truncate(name, max) {
  if (!name || name.length <= max) return name || "";
  return name.substring(0, max - 3) + "...";
}

function _isImageNode(node) {
  return !!(node && node.asset_type === "image" && node.media_id);
}

function _sameNode(a, b) {
  if (!a || !b) return false;
  var aUrl = String(a.previewUrl || a.signed_url || "");
  var bUrl = String(b.previewUrl || b.signed_url || "");
  if (aUrl && bUrl && aUrl === bUrl) return true;
  if (a.id && b.id && a.id === b.id) return true;
  if (a.media_id && b.media_id && a.media_id === b.media_id) return true;
  return false;
}

function _findImageIndex(nodes, node) {
  for (var i = 0; i < nodes.length; i++) {
    if (_sameNode(nodes[i], node)) return i;
  }
  return -1;
}

async function _refreshImageNodes(win, currentNode) {
  if (!win || !_isImageNode(currentNode)) return;
  var imageNodes = await fetchSiblingImageNodes(getEffectiveThreadId(), currentNode);
  var currentIndex = _findImageIndex(imageNodes, currentNode);
  if (currentIndex < 0) {
    imageNodes.unshift(Object.assign({}, currentNode));
    currentIndex = 0;
  }
  win._previewImageNodes = imageNodes;
  win._previewImageIndex = currentIndex;
}

function _stepPreview(win, body, delta) {
  var nodes = win && win._previewImageNodes;
  if (!nodes || !nodes.length) return;
  var currentIndex = typeof win._previewImageIndex === "number" ? win._previewImageIndex : 0;
  var nextIndex = currentIndex + delta;
  if (nextIndex < 0 || nextIndex >= nodes.length) return;
  win._previewImageIndex = nextIndex;
  win._previewCurrentNode = Object.assign({}, nodes[nextIndex]);
  updateWindowTitle(win.id, _truncate(win._previewCurrentNode.name || win._previewCurrentNode.media_id, 40));
  _renderContent(body, win._previewCurrentNode, win);
}

function _buildNavButton(className, svgHtml, onClick) {
  var btn = document.createElement("button");
  btn.type = "button";
  btn.className = className;
  btn.innerHTML = svgHtml;
  btn.onclick = onClick;
  return btn;
}

function _setupImageZoom(container, img) {
  var fitScale = 1;
  var zoomScale = 1;
  var translateX = 0;
  var translateY = 0;
  var isDragging = false;
  var isPinching = false;
  var startX = 0, startY = 0;
  var startDist = 0;
  var cleanupFns = [];

  function getCombinedScale() {
    return fitScale * zoomScale;
  }

  function updateCursor() {
    container.style.cursor = zoomScale > 1.01 ? (isDragging ? "grabbing" : "grab") : "default";
  }

  function updateTransform() {
    img.style.transform = "translate(" + translateX + "px, " + translateY + "px) scale(" + getCombinedScale() + ")";
    updateCursor();
  }

  function computeFitScale() {
    var rect = container.getBoundingClientRect();
    if (!rect.width || !rect.height || !img.naturalWidth || !img.naturalHeight) return 1;
    return Math.min(rect.width / img.naturalWidth, rect.height / img.naturalHeight);
  }

  function applyFit(resetPosition) {
    var nextFitScale = computeFitScale();
    if (!isFinite(nextFitScale) || nextFitScale <= 0) nextFitScale = 1;
    if (resetPosition || zoomScale <= 1.01) {
      translateX = 0;
      translateY = 0;
      zoomScale = 1;
    } else if (fitScale > 0) {
      var ratio = nextFitScale / fitScale;
      translateX *= ratio;
      translateY *= ratio;
    }
    fitScale = nextFitScale;
    updateTransform();
  }

  function setZoomScaleAtPoint(cx, cy, targetZoomScale) {
    var rect = container.getBoundingClientRect();
    var pointerX = cx - rect.left - rect.width / 2;
    var pointerY = cy - rect.top - rect.height / 2;
    var currentScale = getCombinedScale();
    var nextScale = fitScale * targetZoomScale;
    if (!currentScale || !nextScale) return;
    var localX = (pointerX - translateX) / currentScale;
    var localY = (pointerY - translateY) / currentScale;
    translateX = pointerX - localX * nextScale;
    translateY = pointerY - localY * nextScale;
    zoomScale = targetZoomScale;
    if (Math.abs(zoomScale - 1) < 0.01) {
      zoomScale = 1;
      translateX = 0;
      translateY = 0;
    }
    updateTransform();
  }

  function zoom(delta, cx, cy) {
    var factor = delta > 0 ? 1.08 : 1 / 1.08;
    var maxZoomScale = Math.max(1, 20 / Math.max(fitScale, 0.001));
    var nextZoomScale = Math.max(0.2, Math.min(maxZoomScale, zoomScale * factor));
    setZoomScaleAtPoint(cx, cy, nextZoomScale);
  }

  function onWheel(e) {
    e.preventDefault();
    zoom(-e.deltaY, e.clientX, e.clientY);
  }

  function onMouseDown(e) {
    if (zoomScale <= 1.01) return;
    isDragging = true;
    startX = e.clientX - translateX;
    startY = e.clientY - translateY;
    updateCursor();
  }

  function onMouseMove(e) {
    if (!isDragging) return;
    translateX = e.clientX - startX;
    translateY = e.clientY - startY;
    updateTransform();
  }

  function stopDragging() {
    isDragging = false;
    updateCursor();
  }

  function onTouchStart(e) {
    if (e.touches.length === 2) {
      isPinching = true;
      startDist = Math.hypot(e.touches[0].pageX - e.touches[1].pageX, e.touches[0].pageY - e.touches[1].pageY);
      return;
    } else if (e.touches.length === 1) {
      if (zoomScale <= 1.01) return;
      isDragging = true;
      startX = e.touches[0].pageX - translateX;
      startY = e.touches[0].pageY - translateY;
    }
    updateCursor();
  }

  function onTouchMove(e) {
    e.preventDefault();
    if (isPinching && e.touches.length === 2) {
      var dist = Math.hypot(e.touches[0].pageX - e.touches[1].pageX, e.touches[0].pageY - e.touches[1].pageY);
      if (startDist > 0) {
        var pinchFactor = dist / startDist;
        var centerX = (e.touches[0].clientX + e.touches[1].clientX) / 2;
        var centerY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
        var maxZoomScale = Math.max(1, 20 / Math.max(fitScale, 0.001));
        var nextZoomScale = Math.max(0.2, Math.min(maxZoomScale, zoomScale * pinchFactor));
        setZoomScaleAtPoint(centerX, centerY, nextZoomScale);
      }
      startDist = dist;
    } else if (isDragging && e.touches.length === 1) {
      translateX = e.touches[0].pageX - startX;
      translateY = e.touches[0].pageY - startY;
      updateTransform();
    }
  }

  function onTouchEnd() {
    isPinching = false;
    isDragging = false;
    updateCursor();
  }

  function onDoubleClick(e) {
    if (zoomScale > 1.01) {
      zoomScale = 1;
      translateX = 0;
      translateY = 0;
      updateTransform();
    } else {
      setZoomScaleAtPoint(e.clientX, e.clientY, 2.5);
    }
  }

  function onDragStart(e) {
    e.preventDefault();
  }

  container.addEventListener("wheel", onWheel, { passive: false });
  container.addEventListener("mousedown", onMouseDown);
  container.addEventListener("mousemove", onMouseMove);
  container.addEventListener("mouseup", stopDragging);
  container.addEventListener("mouseleave", stopDragging);
  container.addEventListener("touchstart", onTouchStart, { passive: false });
  container.addEventListener("touchmove", onTouchMove, { passive: false });
  container.addEventListener("touchend", onTouchEnd, { passive: false });
  container.addEventListener("dblclick", onDoubleClick);
  container.addEventListener("dragstart", onDragStart);

  cleanupFns.push(function () { container.removeEventListener("wheel", onWheel); });
  cleanupFns.push(function () { container.removeEventListener("mousedown", onMouseDown); });
  cleanupFns.push(function () { container.removeEventListener("mousemove", onMouseMove); });
  cleanupFns.push(function () { container.removeEventListener("mouseup", stopDragging); });
  cleanupFns.push(function () { container.removeEventListener("mouseleave", stopDragging); });
  cleanupFns.push(function () { container.removeEventListener("touchstart", onTouchStart); });
  cleanupFns.push(function () { container.removeEventListener("touchmove", onTouchMove); });
  cleanupFns.push(function () { container.removeEventListener("touchend", onTouchEnd); });
  cleanupFns.push(function () { container.removeEventListener("dblclick", onDoubleClick); });
  cleanupFns.push(function () { container.removeEventListener("dragstart", onDragStart); });

  if (globalThis.ResizeObserver) {
    var observer = new ResizeObserver(function () {
      applyFit(false);
    });
    observer.observe(container);
    cleanupFns.push(function () { observer.disconnect(); });
  } else {
    var onResize = function () { applyFit(false); };
    window.addEventListener("resize", onResize);
    cleanupFns.push(function () { window.removeEventListener("resize", onResize); });
  }

  img.draggable = false;
  img.style.visibility = "";
  applyFit(true);

  return function () {
    cleanupFns.forEach(function (fn) { fn(); });
  };
}

var TEXT_EXTENSIONS = new Set([
  ".txt",".md",".markdown",".json",".jsonl",".yaml",".yml",".csv",".log",".ini",
  ".cfg",".conf",".xml",".toml",".env",".py",".js",".ts",".tsx",".jsx",
  ".java",".go",".rs",".sql",".html",".htm",".css",".scss",".less",".sh",
  ".bat",".ps1",".rb",".php",".c",".cc",".cpp",".h",".hpp",".cs",".swift",
  ".kt",".kts",".dart",".r",".vue",".svelte"
]);

function _isTextFile(name) {
  if (!name) return false;
  var dot = name.lastIndexOf(".");
  if (dot < 0) return false;
  return TEXT_EXTENSIONS.has(name.substring(dot).toLowerCase());
}

function _renderContent(body, node, win) {
  body.innerHTML = "";
  var type = node.asset_type || "";
  upsertNode(node);

  if (type === "image") {
    var navHost = document.createElement("div");
    navHost.className = "asset-preview-image-host";
    var imgContainer = document.createElement("div");
    imgContainer.className = "asset-preview-image-container";
    var img = document.createElement("img");
    img.alt = node.name || "";
    img.style.cssText = "display:block;max-width:none;max-height:none;user-select:none;-webkit-user-select:none;transform-origin:center center;visibility:hidden;";
    img.draggable = false;
    var imageViewerInitialized = false;
    function initImageViewer() {
      if (imageViewerInitialized) return;
      if (!img.naturalWidth) return;
      imageViewerInitialized = true;
      img.style.visibility = "";
      var cleanup = _setupImageZoom(imgContainer, img);
      if (win && Array.isArray(win.cleanup)) win.cleanup.push(cleanup);
    }
    img.onload = initImageViewer;
    img.onerror = function () {
      img.style.visibility = "";
      img.alt = "图片加载失败";
    };
    bindPreviewImage(img, node).then(function () {
      if (img.complete && img.naturalWidth) initImageViewer();
    });
    imgContainer.appendChild(img);
    navHost.appendChild(imgContainer);
    body.style.padding = "0";
    body.style.overflow = "hidden";
    body.appendChild(navHost);
    var imageNodes = win && Array.isArray(win._previewImageNodes) ? win._previewImageNodes : [];
    var imageIndex = win && typeof win._previewImageIndex === "number" ? win._previewImageIndex : -1;
    if (win && typeof win._previewImageIndex === "number" && win._previewImageIndex >= 0) {
      imageIndex = win._previewImageIndex;
    } else {
      var matchedIndex = _findImageIndex(imageNodes, node);
      if (matchedIndex >= 0) {
        win._previewImageIndex = matchedIndex;
        imageIndex = matchedIndex;
      }
    }
    if (imageNodes.length > 1 && imageIndex >= 0) {
      var leftBtn = _buildNavButton(
        "asset-preview-nav asset-preview-nav-left",
        CHEVRON_LEFT_SVG,
        function () { _stepPreview(win, body, -1); }
      );
      leftBtn.disabled = imageIndex <= 0;

      var rightBtn = _buildNavButton(
        "asset-preview-nav asset-preview-nav-right",
        CHEVRON_RIGHT_SVG,
        function () { _stepPreview(win, body, 1); }
      );
      rightBtn.disabled = imageIndex >= imageNodes.length - 1;

      var counter = document.createElement("div");
      counter.className = "asset-preview-counter";
      counter.textContent = (imageIndex + 1) + " / " + imageNodes.length;

      navHost.appendChild(leftBtn);
      navHost.appendChild(rightBtn);
      navHost.appendChild(counter);
    }
  } else if (type === "video") {
    var mediaUrl = workspaceReadUrl(resolveWorkspacePath(node)) || resolveFileUrl(node);
    if (!mediaUrl) return;
    var videoWrap = document.createElement("div");
    videoWrap.style.cssText = "width:100%;height:100%;display:flex;align-items:center;justify-content:center;overflow:hidden;";
    var vid = document.createElement("video");
    vid.src = mediaUrl;
    vid.controls = true;
    vid.preload = "metadata";
    vid.autoplay = false;
    vid.style.cssText = "width:100%;height:100%;display:block;object-fit:contain;";
    body.style.display = "flex";
    body.style.alignItems = "center";
    body.style.justifyContent = "center";
    body.style.padding = "0";
    body.style.overflow = "hidden";
    videoWrap.appendChild(vid);
    body.appendChild(videoWrap);
  } else if (type === "audio") {
    var mediaUrl = workspaceReadUrl(resolveWorkspacePath(node)) || resolveFileUrl(node);
    if (!mediaUrl) return;
    var wrap = document.createElement("div");
    wrap.style.cssText = "display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;gap:16px;";
    var label = document.createElement("div");
    label.textContent = node.name || "audio";
    label.style.cssText = "font-size:14px;color:var(--text-muted,#888);";
    var audio = document.createElement("audio");
    audio.src = mediaUrl;
    audio.controls = true;
    audio.preload = "metadata";
    audio.style.cssText = "width:90%;max-width:400px;";
    wrap.appendChild(label);
    wrap.appendChild(audio);
    body.appendChild(wrap);
  } else if (isOfficeFile(node.name)) {
    var officeUrl = resolveFileUrl(node);
    if (!officeUrl) return;
    body.style.padding = "0";
    body.style.overflow = "hidden";
    renderOffice(body, officeUrl, node.name);
  } else if (_isTextFile(node.name)) {
    var textUrl = resolveFileUrl(node);
    if (!textUrl) return;
    body.style.padding = "0";
    body.style.overflow = "hidden";
    var loading = document.createElement("div");
    loading.style.cssText = "padding:16px;color:var(--text-muted,#888);font-size:13px;";
    loading.textContent = "Loading...";
    body.appendChild(loading);
    naFetch(textUrl)
      .then(function (r) { return r.text(); })
      .then(function (text) {
        body.innerHTML = "";
        var pre = document.createElement("pre");
        pre.style.cssText = "width:100%;height:100%;margin:0;padding:12px;overflow:auto;font-size:13px;line-height:1.5;"
          + "font-family:Consolas,'Courier New',monospace;white-space:pre-wrap;word-break:break-all;"
          + "background:var(--bg-secondary,#f8f8f8);color:var(--text,#333);box-sizing:border-box;";
        pre.textContent = text;
        body.appendChild(pre);
      })
      .catch(function () {
        loading.textContent = "Failed to load content";
      });
  } else {
    var info = document.createElement("div");
    info.style.cssText = "display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;gap:12px;";
    var nameEl = document.createElement("div");
    nameEl.textContent = node.name || "file";
    nameEl.style.cssText = "font-size:14px;";
    var dlBtn = document.createElement("button");
    dlBtn.type = "button";
    dlBtn.textContent = "下载文件";
    dlBtn.style.cssText = "padding:6px 14px;border:1px solid var(--border,#ddd);background:var(--bg,#fff);border-radius:6px;cursor:pointer;font-size:13px;";
    dlBtn.onclick = function () { downloadAsset(node); };
    var link = document.createElement("a");
    link.href = resolveFileUrl(node);
    link.target = "_blank";
    link.textContent = "在新标签打开";
    link.style.cssText = "color:var(--accent,#4f46e5);font-size:13px;";
    info.appendChild(nameEl);
    info.appendChild(dlBtn);
    info.appendChild(link);
    body.appendChild(info);
  }
}

function _applyPreviewGallery(win, node, options) {
  options = options || {};
  if (options.imageNodes && options.imageNodes.length) {
    win._previewImageNodes = options.imageNodes.map(function (n) {
      return Object.assign({}, n);
    });
    win._previewImageIndex = typeof options.imageIndex === "number" ? options.imageIndex : 0;
    if (win._previewImageIndex < 0 || win._previewImageIndex >= win._previewImageNodes.length) {
      win._previewImageIndex = 0;
    }
    win._previewCurrentNode = Object.assign({}, win._previewImageNodes[win._previewImageIndex]);
    return;
  }
  win._previewCurrentNode = Object.assign({}, node);
  win._previewImageNodes = [win._previewCurrentNode];
  win._previewImageIndex = 0;
}

function _previewWindowId(node, options) {
  options = options || {};
  if (options.previewKey) {
    return PREVIEW_ID_PREFIX + options.previewKey;
  }
  return _previewId(node);
}

export function openPreview(node, options) {
  options = options || {};
  if (!node || !node.media_id) return;
  if (isTextEditableNode(node)) {
    openTextEditor(node);
    return;
  }
  var id = _previewWindowId(node, options);
  var existing = getWindow(id);
  if (existing) {
    _applyPreviewGallery(existing, node, options);
    var body = getWindowBody(id);
    if (body && existing._previewCurrentNode) {
      if (Array.isArray(existing.cleanup)) {
        existing.cleanup.forEach(function (fn) {
          if (typeof fn === "function") fn();
        });
        existing.cleanup.length = 0;
      }
      _renderContent(body, existing._previewCurrentNode, existing);
    }
    updateWindowTitle(id, _truncate(existing._previewCurrentNode.name || existing._previewCurrentNode.media_id, 40));
    focusWindow(id);
    return;
  }
  var viewportWidth = Math.max(320, Math.floor(window.innerWidth * 0.8));
  var viewportHeight = Math.max(240, Math.floor(window.innerHeight * 0.8));
  openWindow(id, {
    type: "asset_preview",
    title: _truncate(node.name || node.media_id, 40),
    width: viewportWidth,
    height: viewportHeight,
    minWidth: 280,
    minHeight: 200,
    onMount: function (body, win) {
      var liveNode = Object.assign({}, node);
      _applyPreviewGallery(win, liveNode, options);
      if (options.imageNodes && options.imageNodes.length) {
        _renderContent(body, win._previewCurrentNode || liveNode, win);
        return;
      }
      win._previewCurrentNode = liveNode;
      win._previewImageNodes = [liveNode];
      win._previewImageIndex = 0;
      _refreshImageNodes(win, liveNode).then(function () {
        _renderContent(body, win._previewCurrentNode || liveNode, win);
      }).catch(function () {
        _renderContent(body, liveNode, win);
      });
    },
  });
}

export function closePreview(node) {
  if (!node) return;
  closeWindow(_previewId(node));
}
