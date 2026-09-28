var BASE_Z = 10000;
var _nextZ = BASE_Z + 1;
var _windows = new Map();
var _container = null;

function _getContainer() {
  if (_container) return _container;
  _container = document.getElementById("windowManagerContainer");
  if (!_container) {
    _container = document.createElement("div");
    _container.id = "windowManagerContainer";
    _container.style.cssText = "position:fixed;top:0;left:0;width:0;height:0;z-index:" + BASE_Z + ";pointer-events:none;";
    document.body.appendChild(_container);
  }
  return _container;
}

function _clamp(val, min, max) {
  return Math.max(min, Math.min(max, val));
}

function _bringToFront(win) {
  _nextZ++;
  win.el.style.zIndex = _nextZ;
}

function _getCoords(e) {
  if (e.touches && e.touches.length > 0) return { x: e.touches[0].clientX, y: e.touches[0].clientY };
  if (e.changedTouches && e.changedTouches.length > 0) return { x: e.changedTouches[0].clientX, y: e.changedTouches[0].clientY };
  return { x: e.clientX, y: e.clientY };
}

function _setupDrag(win) {
  var header = win.el.querySelector(".wm-header");
  if (!header) return function () {};
  var dragging = false;
  var startX = 0, startY = 0, origLeft = 0, origTop = 0;

  function onStart(e) {
    if (e.target.closest("button")) return;
    dragging = true;
    var c = _getCoords(e);
    startX = c.x;
    startY = c.y;
    origLeft = win.el.offsetLeft;
    origTop = win.el.offsetTop;
    _bringToFront(win);
    e.preventDefault();
  }
  function onMove(e) {
    if (!dragging) return;
    var c = _getCoords(e);
    var dx = c.x - startX;
    var dy = c.y - startY;
    var newLeft = _clamp(origLeft + dx, -(win.el.offsetWidth - 60), window.innerWidth - 60);
    var newTop = _clamp(origTop + dy, 0, window.innerHeight - 40);
    win.el.style.left = newLeft + "px";
    win.el.style.top = newTop + "px";
    e.preventDefault();
  }
  function onEnd() {
    dragging = false;
  }
  header.addEventListener("mousedown", onStart);
  header.addEventListener("touchstart", onStart, { passive: false });
  document.addEventListener("mousemove", onMove);
  document.addEventListener("touchmove", onMove, { passive: false });
  document.addEventListener("mouseup", onEnd);
  document.addEventListener("touchend", onEnd);
  return function () {
    header.removeEventListener("mousedown", onStart);
    header.removeEventListener("touchstart", onStart);
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("touchmove", onMove);
    document.removeEventListener("mouseup", onEnd);
    document.removeEventListener("touchend", onEnd);
  };
}

function _setupResize(win) {
  var directions = ["e", "s", "se", "sw", "w"];
  var cleanups = [];
  directions.forEach(function (dir) {
    var handle = document.createElement("div");
    handle.className = "wm-resize-handle wm-resize-" + dir;
    handle.dataset.dir = dir;
    win.el.appendChild(handle);

    var resizing = false;
    var startX = 0, startY = 0, startW = 0, startH = 0, startLeft = 0;

    function onStart(e) {
      resizing = true;
      var c = _getCoords(e);
      startX = c.x;
      startY = c.y;
      startW = win.el.offsetWidth;
      startH = win.el.offsetHeight;
      startLeft = win.el.offsetLeft;
      _bringToFront(win);
      e.preventDefault();
      e.stopPropagation();
    }
    function onMove(e) {
      if (!resizing) return;
      var c = _getCoords(e);
      var dx = c.x - startX;
      var dy = c.y - startY;
      var minW = win.minWidth || 280;
      var minH = win.minHeight || 200;
      if (dir.includes("e")) {
        win.el.style.width = Math.max(minW, startW + dx) + "px";
      }
      if (dir.includes("s")) {
        win.el.style.height = Math.max(minH, startH + dy) + "px";
      }
      if (dir.includes("w")) {
        var newW = Math.max(minW, startW - dx);
        var newLeft = startLeft + (startW - newW);
        win.el.style.width = newW + "px";
        win.el.style.left = newLeft + "px";
      }
      e.preventDefault();
    }
    function onEnd() {
      resizing = false;
    }
    handle.addEventListener("mousedown", onStart);
    handle.addEventListener("touchstart", onStart, { passive: false });
    document.addEventListener("mousemove", onMove);
    document.addEventListener("touchmove", onMove, { passive: false });
    document.addEventListener("mouseup", onEnd);
    document.addEventListener("touchend", onEnd);
    cleanups.push(function () {
      handle.removeEventListener("mousedown", onStart);
      handle.removeEventListener("touchstart", onStart);
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("touchmove", onMove);
      document.removeEventListener("mouseup", onEnd);
      document.removeEventListener("touchend", onEnd);
    });
  });
  return function () {
    cleanups.forEach(function (fn) { fn(); });
  };
}

function _createWindowEl(id, options) {
  var el = document.createElement("div");
  el.className = "wm-window";
  el.id = "wm-" + id;
  el.style.cssText = "position:fixed;pointer-events:auto;display:flex;flex-direction:column;";
  el.style.zIndex = ++_nextZ;

  var w = Math.min(options.width || 480, window.innerWidth - 16);
  var h = Math.min(options.height || 400, window.innerHeight - 16);
  var left = options.left != null ? options.left : Math.max(8, (window.innerWidth - w) / 2);
  var top = options.top != null ? options.top : Math.max(8, (window.innerHeight - h) / 2);
  el.style.width = w + "px";
  el.style.height = h + "px";
  el.style.left = left + "px";
  el.style.top = top + "px";

  var header = document.createElement("div");
  header.className = "wm-header";

  var titleEl = document.createElement("span");
  titleEl.className = "wm-title";
  titleEl.textContent = options.title || "";
  header.appendChild(titleEl);

  var actions = document.createElement("div");
  actions.className = "wm-actions";

  if (options.minimizable) {
    var minBtn = document.createElement("button");
    minBtn.className = "wm-btn wm-btn-min";
    minBtn.type = "button";
    minBtn.innerHTML = "&minus;";
    minBtn.title = "缩小";
    minBtn.onclick = function (e) {
      e.stopPropagation();
      minimizeWindow(id);
    };
    actions.appendChild(minBtn);
    var restoreBtn = document.createElement("button");
    restoreBtn.className = "wm-btn wm-btn-restore";
    restoreBtn.type = "button";
    restoreBtn.title = "还原";
    restoreBtn.innerHTML = '<svg width="10" height="10" viewBox="0 0 10 10"><rect x="0.7" y="2.4" width="6.6" height="6.6" fill="none" stroke="currentColor" stroke-width="1.2"/><path d="M2.6 2.4V0.7h6.7V7.4H7.6" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>';
    restoreBtn.onclick = function (e) {
      e.stopPropagation();
      restoreWindow(id);
    };
    actions.appendChild(restoreBtn);
  }
  if (options.closable !== false) {
    var closeBtn = document.createElement("button");
    closeBtn.className = "wm-btn wm-btn-close";
    closeBtn.type = "button";
    closeBtn.innerHTML = "&times;";
    closeBtn.onclick = function () { closeWindow(id); };
    actions.appendChild(closeBtn);
  }
  header.appendChild(actions);

  var body = document.createElement("div");
  body.className = "wm-body";

  el.appendChild(header);
  el.appendChild(body);

  // 操作按钮条钉在窗口底部,属于窗体而不是正文 —— 正文再长再滚,它都一直在。
  var footerActions = options.footerActions || [];
  if (footerActions.length) {
    var footer = document.createElement("div");
    footer.className = "wm-footer";
    footerActions.forEach(function (action) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "wm-footer-btn"
        + (action.variant === "primary" ? " wm-footer-primary" : "")
        + (action.variant === "danger" ? " wm-footer-danger" : "");
      btn.dataset.wmAction = action.id;
      btn.textContent = action.label;
      if (action.title) btn.title = action.title;
      btn.onclick = function (e) {
        e.stopPropagation();
        action.onClick();
      };
      footer.appendChild(btn);
    });
    el.appendChild(footer);
  }

  el.addEventListener("mousedown", function () { _bringToFront(_windows.get(id)); });
  el.addEventListener("touchstart", function () { _bringToFront(_windows.get(id)); }, { passive: true });

  return el;
}

export function openWindow(id, options) {
  options = options || {};
  var existing = _windows.get(id);
  if (existing) {
    _bringToFront(existing);
    existing.el.style.display = "flex";
    if (options.onFocus) options.onFocus(existing);
    return existing;
  }
  var el = _createWindowEl(id, options);
  var win = {
    id: id,
    el: el,
    type: options.type || "generic",
    minWidth: options.minWidth || 280,
    minHeight: options.minHeight || 200,
    minimized: false,
    restore: null,
    cleanup: [],
    onClose: typeof options.onClose === "function" ? options.onClose : null,
  };
  _windows.set(id, win);
  _getContainer().appendChild(el);
  win.cleanup.push(_setupDrag(win));
  win.cleanup.push(_setupResize(win));
  if (options.onMount) options.onMount(el.querySelector(".wm-body"), win);
  return win;
}

export function minimizeWindow(id) {
  var win = _windows.get(id);
  if (!win || win.minimized) return;
  var header = win.el.querySelector(".wm-header");
  if (!header) throw new Error("minimizeWindow missing header");
  win.restore = {
    height: win.el.style.height,
    minHeight: win.el.style.minHeight,
  };
  win.minimized = true;
  win.el.classList.add("is-minimized");
  win.el.style.minHeight = "0px";
  win.el.style.height = header.offsetHeight + "px";
}

export function restoreWindow(id) {
  var win = _windows.get(id);
  if (!win || !win.minimized) return win;
  win.minimized = false;
  win.el.classList.remove("is-minimized");
  if (win.restore) {
    win.el.style.height = win.restore.height;
    win.el.style.minHeight = win.restore.minHeight;
  }
  win.restore = null;
  _bringToFront(win);
  return win;
}

export function closeWindow(id) {
  var win = _windows.get(id);
  if (!win) return;
  if (typeof win.onClose === "function") {
    var cb = win.onClose;
    win.onClose = null;
    try { cb(); } catch (e) {}
  }
  (win.cleanup || []).forEach(function (fn) {
    if (typeof fn === "function") fn();
  });
  win.el.remove();
  _windows.delete(id);
}

export function focusWindow(id) {
  var win = _windows.get(id);
  if (!win) return null;
  _bringToFront(win);
  win.el.style.display = "flex";
  return win;
}

export function getWindow(id) {
  return _windows.get(id) || null;
}

export function updateWindowTitle(id, title) {
  var win = _windows.get(id);
  if (!win) return;
  var titleEl = win.el.querySelector(".wm-title");
  if (titleEl) titleEl.textContent = title;
}

export function getWindowBody(id) {
  var win = _windows.get(id);
  if (!win) return null;
  return win.el.querySelector(".wm-body");
}

export function getWindowAction(id, actionId) {
  var win = _windows.get(id);
  if (!win) return null;
  return win.el.querySelector('[data-wm-action="' + actionId + '"]');
}

export { registerWindowType, unregisterWindowType, hasWindowType, openRegisteredWindow } from "./registry.js";
