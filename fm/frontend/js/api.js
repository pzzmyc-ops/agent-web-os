const API = {
  async request(url, options = {}) {
    const res = await fetch(url, { ...options, cache: "no-store" });
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) {
      const data = await res.json();
      if (!data.code) {
        throw new Error(typeof data.data === "string" ? data.data : "请求失败");
      }
      return data;
    }
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    return res;
  },
  get(url) {
    return this.request(url);
  },
  post(url, body) {
    return this.request(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  },
  root() {
    return this.get("/api/root");
  },
  list(path) {
    return this.get(`/api/list?path=${encodeURIComponent(path)}`);
  },
  info(path) {
    return this.get(`/api/info?path=${encodeURIComponent(path)}`);
  },
  stat(paths) {
    return this.post("/api/info/stat", { paths });
  },
  mkdir(path, name) {
    return this.post("/api/mkdir", { path, name });
  },
  mkfile(path, name, content = "") {
    return this.post("/api/mkfile", { path, name, content });
  },
  rename(path, newName) {
    return this.post("/api/rename", { path, newName });
  },
  remove(paths) {
    return this.post("/api/delete", { paths });
  },
  copy(paths, dest) {
    return this.post("/api/copy", { paths, dest });
  },
  move(paths, dest) {
    return this.post("/api/move", { paths, dest });
  },
  search(path, keyword) {
    return this.post("/api/search", { path, keyword });
  },
  save(path, content) {
    return this.post("/api/save", { path, content });
  },
  zip(paths, dest, name) {
    return this.post("/api/zip", { paths, dest, name });
  },
  unzip(path, dest) {
    return this.post("/api/unzip", { path, dest });
  },
  archiveList(path) {
    return this.post("/api/archive/list", { path });
  },
  async _ndjson(url, body, onEvent, signal) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
      signal,
    });
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) {
      const data = await res.json();
      throw new Error(typeof data.data === "string" ? data.data : "请求失败");
    }
    if (!res.ok) throw new Error("HTTP " + res.status);
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buf += dec.decode(chunk.value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop();
      for (let i = 0; i < lines.length; i += 1) {
        const line = lines[i].trim();
        if (!line) continue;
        onEvent(JSON.parse(line));
      }
    }
    if (buf.trim()) onEvent(JSON.parse(buf.trim()));
  },
  archiveExtract(path, dest, onEvent, signal, members) {
    const body = { path, dest };
    if (members && members.length) body.members = members;
    return this._ndjson("/api/archive/extract", body, onEvent, signal);
  },
  archiveOpen(path, member, onEvent, signal) {
    return this._ndjson("/api/archive/open", { path, member }, onEvent, signal);
  },
  async upload(path, file) {
    const fd = new FormData();
    fd.append("path", path);
    fd.append("file", file, file.name);
    return this.request("/api/upload", { method: "POST", body: fd });
  },
  downloadUrl(path) {
    return `/api/download?path=${encodeURIComponent(path)}`;
  },
  mediaUrl(item) {
    if (!item || typeof item !== "object" || !item.path) throw new Error("mediaUrl 需要文件条目");
    if (item.modifyTime == null || item.size == null) throw new Error(`mediaUrl 缺少版本信息: ${item.path}`);
    return `/api/media?path=${encodeURIComponent(item.path)}&v=${encodeURIComponent(`${item.modifyTime}-${item.size}`)}`;
  },
  thumbnailUrl(path, version) {
    return `/api/thumbnail?path=${encodeURIComponent(path)}&v=${encodeURIComponent(version)}`;
  },
  previewUrl(path) {
    return `/api/preview?path=${encodeURIComponent(path)}`;
  },
  browseUrl(path) {
    if (!path) throw new Error("browseUrl 需要路径");
    const norm = String(path).replace(/\\/g, "/");
    const rel = norm.replace(/^\/+/, "");
    return "/api/browse/" + rel.split("/").map(encodeURIComponent).join("/");
  },
  preview(path) {
    return this.get(this.previewUrl(path));
  },
  onlyofficeConfig(path, mode = "edit") {
    return this.get(`/api/onlyoffice/config?path=${encodeURIComponent(path)}&mode=${encodeURIComponent(mode)}`);
  },
  onlyofficeStamp(path) {
    return this.get(`/api/onlyoffice/stamp?path=${encodeURIComponent(path)}`);
  },
};

const ICON_BASE = "/assets/kod/images/file_icon";
const FOLDER_ICON = `${ICON_BASE}/icon_others/folder_win11.png`;
const THUMBNAIL_IMAGE_EXT = new Set(["png", "jpg", "jpeg", "gif", "bmp", "webp", "svg", "ico"]);
const THUMBNAIL_VIDEO_EXT = new Set(["mp4", "mov", "avi", "mkv", "webm", "m4v", "mpg", "mpeg", "wmv", "flv"]);
const FILE_ICON_MAP = {
  png: "png.png", jpg: "jpg.png", jpeg: "jpg.png", gif: "gif.png", bmp: "bmp.png",
  svg: "svg.png", webp: "png.png", ico: "ico.png",
  txt: "txt.png", md: "md.png", pdf: "pdf.png",
  zip: "zip.png", rar: "rar.png", "7z": "7z.png", tar: "tar.png", gz: "gz.png",
  doc: "doc.png", docx: "docx.png", xls: "xls.png", xlsx: "xlsx.png",
  ppt: "ppt.png", pptx: "pptx.png",
  js: "js.png", ts: "js.png", json: "json.png", css: "css.png", html: "html.png",
  py: "py.png", java: "java.png", php: "php.png", xml: "xml.png", sql: "sql.png",
  mp3: "music.png", wav: "music.png", flac: "music.png",
  mp4: "movie.png", avi: "movie.png", mkv: "movie.png", mov: "movie.png",
  exe: "exe.png", dll: "dll.png",
};

function fileIcon(item) {
  if (item.type === "folder") return FOLDER_ICON;
  const ext = (item.ext || "").toLowerCase();
  const name = FILE_ICON_MAP[ext] || "file.png";
  if (name === "movie.png") return `${ICON_BASE}/icon_file/movie/movie.png`;
  return `${ICON_BASE}/icon_file/${name}`;
}

function setFileArtwork(element, item) {
  const ext = String(item.ext || "").toLowerCase();
  const isImage = item.type === "file" && THUMBNAIL_IMAGE_EXT.has(ext);
  const isVideo = item.type === "file" && THUMBNAIL_VIDEO_EXT.has(ext);
  element.classList.toggle("media-thumbnail", isImage || isVideo);
  element.classList.toggle("media-video", isVideo);
  const source = isImage || isVideo
    ? API.thumbnailUrl(item.path, `${item.modifyTime}-${item.size}`)
    : fileIcon(item);
  element.style.backgroundImage = `url("${source}")`;
}

function formatSize(n) {
  if (n == null) return "-";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${u[i]}`;
}

function formatSpeed(n) {
  if (n == null || n <= 0) return "";
  return `${formatSize(n)}/s`;
}

function formatTime(ts) {
  const d = new Date(ts * 1000);
  const p = (x) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

window.FMEnv = { workspace: "", desktop: "", roots: [], has7z: false };

async function FMLoadEnv() {
  const res = await API.root();
  window.FMEnv.workspace = normalizeFsPath(res.data.workspace);
  window.FMEnv.desktop = normalizeFsPath(res.data.desktop);
  window.FMEnv.roots = (res.data.roots || []).map((p) => normalizeFsPath(p));
  window.FMEnv.has7z = !!res.data.has7z;
  return window.FMEnv;
}

const COMPUTER_NAME = "此电脑";

function isDrivePath(path) {
  return /^[A-Za-z]:(\/|$)/.test(String(path || ""));
}

function isFsRoot(path) {
  const p = normalizeFsPath(path);
  return p === "/" || /^[A-Z]:\/$/.test(p);
}

function joinPath(base, name) {
  const b = normalizeFsPath(base);
  if (b === "/") return normalizeFsPath(name);
  return `${b.replace(/\/$/, "")}/${name}`;
}

function parentPath(path) {
  const p = normalizeFsPath(path);
  if (p === "/") return "/";
  const dm = p.match(/^([A-Z]:)(?:\/(.*))?$/);
  if (dm) {
    const parts = (dm[2] || "").split("/").filter(Boolean);
    if (!parts.length) return "/";
    parts.pop();
    return parts.length ? dm[1] + "/" + parts.join("/") : dm[1] + "/";
  }
  const parts = p.split("/").filter(Boolean);
  parts.pop();
  return parts.length ? `/${parts.join("/")}` : "/";
}

function pathBaseName(path) {
  const p = normalizeFsPath(path);
  if (p === "/") return COMPUTER_NAME;
  if (/^[A-Z]:\/$/.test(p)) return p.slice(0, 2);
  return p.split("/").filter(Boolean).pop();
}

function pathChain(path) {
  const chain = [];
  let cur = normalizeFsPath(path);
  while (true) {
    chain.unshift(cur);
    if (cur === "/") break;
    cur = parentPath(cur);
  }
  return chain;
}

function pathIsUnder(path, base) {
  const p = normalizeFsPath(path);
  const b = normalizeFsPath(base);
  if (b === "/") return true;
  const prefix = b.endsWith("/") ? b : `${b}/`;
  return p === b || p.startsWith(prefix);
}

function fmIsArchiveName(name) {
  return /\.(tar\.gz|tar\.bz2|tar\.xz|tgz|tbz2|txz|zip|7z|rar|tar|gz|bz2|xz)$/i.test(String(name || ""));
}

function toast(msg) {
  const old = document.querySelector(".toast");
  if (old) old.remove();
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2200);
}

function promptModal(title, defaultValue = "") {
  return new Promise((resolve) => {
    const mask = document.getElementById("modal-mask");
    mask.querySelector(".modal-title").textContent = title;
    mask.querySelector(".modal-body").innerHTML = `<input type="text" id="modal-input" />`;
    const input = mask.querySelector("#modal-input");
    input.value = defaultValue;
    mask.classList.remove("hidden");
    input.focus();
    input.select();
    const done = (val) => {
      mask.classList.add("hidden");
      mask.querySelector('[data-act="ok"]').onclick = null;
      mask.querySelector('[data-act="cancel"]').onclick = null;
      resolve(val);
    };
    mask.querySelector('[data-act="ok"]').onclick = () => done(input.value.trim());
    mask.querySelector('[data-act="cancel"]').onclick = () => done(null);
    input.onkeydown = (e) => {
      if (e.key === "Enter") done(input.value.trim());
      if (e.key === "Escape") done(null);
    };
  });
}

function confirmModal(title, message) {
  return new Promise((resolve) => {
    const mask = document.getElementById("modal-mask");
    mask.querySelector(".modal-title").textContent = title;
    mask.querySelector(".modal-body").innerHTML = `<div>${message}</div>`;
    mask.classList.remove("hidden");
    const done = (val) => {
      mask.classList.add("hidden");
      mask.querySelector('[data-act="ok"]').onclick = null;
      mask.querySelector('[data-act="cancel"]').onclick = null;
      resolve(val);
    };
    mask.querySelector('[data-act="ok"]').onclick = () => done(true);
    mask.querySelector('[data-act="cancel"]').onclick = () => done(false);
  });
}

function infoModal(title, html) {
  return new Promise((resolve) => {
    const mask = document.getElementById("modal-mask");
    const cancel = mask.querySelector('[data-act="cancel"]');
    mask.querySelector(".modal-title").textContent = title;
    mask.querySelector(".modal-body").innerHTML = html;
    cancel.style.display = "none";
    cancel.onclick = null;
    mask.classList.remove("hidden");
    mask.querySelector('[data-act="ok"]').onclick = () => {
      mask.classList.add("hidden");
      cancel.style.display = "";
      mask.querySelector('[data-act="ok"]').onclick = null;
      resolve();
    };
  });
}

function fmCopyText(text) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.left = "-9999px";
  document.body.appendChild(ta);
  ta.select();
  const copied = document.execCommand("copy");
  ta.remove();
  if (!copied) throw new Error("复制失败");
}

function fmCopyPaths(items) {
  if (!items.length) throw new Error("请先选择项目");
  fmCopyText(items.map((it) => it.path).join("\n"));
  toast("已复制路径");
}

async function fmShowProps(items) {
  if (!items.length) throw new Error("请先选择项目");
  const row = (key, val, cls) =>
    `<span class="fm-props-key">${key}</span><span class="fm-props-val${cls ? ` ${cls}` : ""}">${val}</span>`;
  const rows = [];
  let title = "";
  let withContains = false;
  if (items.length === 1) {
    const it = items[0];
    const ext = String(it.ext || "").toUpperCase();
    title = `${it.name} 属性`;
    withContains = it.type === "folder";
    rows.push(row("名称", escapeHtml(it.name)));
    rows.push(row("类型", it.type === "folder" ? "文件夹" : (ext ? `${ext} 文件` : "文件")));
    rows.push(row("位置", escapeHtml(parentPath(it.path))));
    rows.push(row("大小", "计算中...", "fm-props-size"));
    rows.push(row("修改时间", formatTime(it.modifyTime)));
    rows.push(row("创建时间", formatTime(it.createTime)));
  } else {
    const folders = items.filter((it) => it.type === "folder").length;
    title = `${items.length} 个项目的属性`;
    withContains = true;
    rows.push(row("项目数", `${items.length - folders} 个文件，${folders} 个文件夹`));
    rows.push(row("位置", escapeHtml(parentPath(items[0].path))));
    rows.push(row("大小", "计算中...", "fm-props-size"));
  }
  const pending = infoModal(title, `<div class="fm-props">${rows.join("")}</div>`);
  const sizeEl = document.querySelector("#modal-mask .fm-props-size");
  API.stat(items.map((it) => it.path)).then((res) => {
    const d = res.data;
    let text = `${formatSize(d.size)} (${d.size.toLocaleString()} 字节)`;
    if (withContains) text += `<br>包含 ${d.files} 个文件，${d.folders} 个文件夹`;
    sizeEl.innerHTML = text;
  }).catch((err) => toast(err.message || String(err)));
  await pending;
}

const FM_TOUCH_MODE_TTL_MS = 1500;
let fmLastTouchTs = 0;

function fmMarkTouchInput() {
  fmLastTouchTs = Date.now();
}

function fmIsTouchMode() {
  return Date.now() - fmLastTouchTs < FM_TOUCH_MODE_TTL_MS;
}

window.FMTouchDrop = { el: null, cls: "" };

function fmTouchGhost(name, count) {
  const el = document.createElement("div");
  el.className = "fm-touch-ghost";
  el.textContent = count > 1 ? `${name} +${count - 1}` : name;
  document.body.appendChild(el);
  return el;
}

function fmTouchMoveGhost(el, x, y) {
  el.style.left = `${x + 12}px`;
  el.style.top = `${y - 28}px`;
}

function fmTouchDropHitAt(x, y) {
  const none = { el: null, path: "", cls: "" };
  const el = document.elementFromPoint(x, y);
  if (!el) return none;
  const item = el.closest(".file-item, tr[data-path]");
  if (item) return { el: item, path: item.dataset.type === "folder" ? item.dataset.path : "", cls: "drop-target" };
  const tree = el.closest(".tree-node[data-path]");
  if (tree) return { el: tree, path: tree.dataset.path, cls: "drop-target" };
  const crumb = el.closest(".header-address-item a[data-path]");
  if (crumb) return { el: crumb, path: crumb.dataset.path, cls: "drop-target" };
  const icon = el.closest(".desk-icon");
  if (icon) {
    if (icon.dataset.special === "home") return { el: icon, path: window.FMEnv.workspace, cls: "drop-target" };
    return { el: icon, path: icon.dataset.type === "folder" ? icon.dataset.path : "", cls: "drop-target" };
  }
  const zone = el.closest("[data-drop-path]");
  if (zone) return { el: zone, path: zone.dataset.dropPath, cls: "dragover" };
  if (Desktop.layer.contains(el) && !el.closest(".win, #taskbar")) {
    return { el: Desktop.layer, path: Desktop.path, cls: "desktop-dragover" };
  }
  return none;
}

function fmTouchDropTargetAt(x, y) {
  return fmTouchDropHitAt(x, y).path;
}

function fmTouchHighlight(hit) {
  const cur = window.FMTouchDrop;
  if (cur.el && (!hit || cur.el !== hit.el)) {
    cur.el.classList.remove(cur.cls);
    cur.el = null;
    cur.cls = "";
  }
  if (hit && hit.el) {
    hit.el.classList.add(hit.cls);
    cur.el = hit.el;
    cur.cls = hit.cls;
  }
}

window.AppClipboard = { mode: null, paths: [] };
window.FMExplorerViews = new Set();
window.FMMediaViews = new Set();
window.FMDragState = { paths: [] };

const FM_DRAG_TYPE = "application/x-fm-paths";

function fmDragStart(e, paths) {
  const list = [...new Set((paths || []).filter(Boolean))].map((p) => normalizeFsPath(p));
  if (!list.length) {
    e.preventDefault();
    return;
  }
  window.FMDragState.paths = list;
  e.dataTransfer.effectAllowed = "copyMove";
  e.dataTransfer.setData(FM_DRAG_TYPE, JSON.stringify(list));
  e.dataTransfer.setData("text/plain", list.join("\n"));
}

function fmDragIsInternal(e) {
  return e.dataTransfer ? [...e.dataTransfer.types].includes(FM_DRAG_TYPE) : false;
}

function fmDragHasFiles(e) {
  return e.dataTransfer ? [...e.dataTransfer.types].includes("Files") : false;
}

function fmDragEffect(e) {
  return e.ctrlKey ? "copy" : "move";
}

function fmDragPaths(e) {
  const raw = e.dataTransfer.getData(FM_DRAG_TYPE);
  const list = raw ? JSON.parse(raw) : window.FMDragState.paths;
  return list.map((p) => normalizeFsPath(p));
}

function fmPathsCanDrop(paths, destPath) {
  const dest = normalizeFsPath(destPath);
  if (!paths.length) return false;
  return paths.every((p) => !pathIsUnder(dest, p));
}

function fmDragCanDrop(e, destPath) {
  if (fmDragIsInternal(e)) {
    return fmPathsCanDrop(window.FMDragState.paths, destPath);
  }
  return fmDragHasFiles(e);
}

async function fmTransferPaths(paths, destPath, copy) {
  const dest = normalizeFsPath(destPath);
  const list = [...new Set((paths || []).filter(Boolean))].map((p) => normalizeFsPath(p));
  list.forEach((p) => {
    if (pathIsUnder(dest, p)) throw new Error("不能把文件夹放进它自己里面");
  });
  const todo = copy ? list : list.filter((p) => parentPath(p) !== dest);
  if (!todo.length) return;
  if (copy) await API.copy(todo, dest);
  else await API.move(todo, dest);
  await FMNotifyFsChanged([...todo.map((p) => parentPath(p)), dest]);
  toast(`${copy ? "已复制" : "已移动"} ${todo.length} 项到 ${dest === window.FMEnv.workspace ? "我的文件" : dest}`);
}

async function fmDropInto(e, destPath) {
  const dest = normalizeFsPath(destPath);
  if (!fmDragIsInternal(e)) {
    await Uploader.addFromDataTransfer(e.dataTransfer, dest);
    return;
  }
  const paths = fmDragPaths(e);
  const copy = fmDragEffect(e) === "copy";
  window.FMDragState.paths = [];
  await fmTransferPaths(paths, dest, copy);
}

function normalizeFsPath(path) {
  let p = String(path || "/").replace(/\\/g, "/").trim();
  if (!p) return "/";
  const dm = p.match(/^([A-Za-z]):(?:\/(.*))?$/);
  if (dm) {
    const letter = dm[1].toUpperCase();
    const rest = (dm[2] || "").replace(/\/+/g, "/").replace(/\/$/, "");
    const parts = rest.split("/").filter((x) => x && x !== ".");
    if (parts.includes("..")) throw new Error("path traversal denied");
    return parts.length ? letter + ":/" + parts.join("/") : letter + ":/";
  }
  if (!p.startsWith("/")) p = `/${p}`;
  p = p.replace(/\/+/g, "/");
  if (p.length > 1 && p.endsWith("/")) p = p.slice(0, -1);
  return p || "/";
}

async function FMUpdateMediaViews(files) {
  const targets = [...new Set((files || []).map((p) => normalizeFsPath(p)))];
  if (!targets.length) return;
  const jobs = [];
  targets.forEach((path) => {
    const hit = [...window.FMMediaViews].filter((view) => view.hasPath(path));
    if (!hit.length) return;
    jobs.push(API.info(path).then((res) => {
      hit.forEach((view) => view.update(res.data));
    }));
  });
  await Promise.all(jobs);
}

async function FMNotifyFsChanged(paths, files) {
  const dirs = [...new Set((paths || []).map((p) => normalizeFsPath(p)))];
  const jobs = [];
  if (dirs.includes(window.FMEnv.desktop) && typeof window.FMRefreshDesktop === "function") {
    jobs.push(Promise.resolve(window.FMRefreshDesktop()));
  }
  if (dirs.length) {
    window.FMExplorerViews.forEach((view) => {
      jobs.push(Promise.resolve(view.refreshPaths(dirs)));
    });
  }
  jobs.push(FMUpdateMediaViews(files));
  await Promise.all(jobs);
}

window.normalizeFsPath = normalizeFsPath;
window.isFsRoot = isFsRoot;
window.isDrivePath = isDrivePath;
window.pathBaseName = pathBaseName;
window.pathChain = pathChain;
window.pathIsUnder = pathIsUnder;
window.joinPath = joinPath;
window.parentPath = parentPath;
window.FMLoadEnv = FMLoadEnv;
window.FMNotifyFsChanged = FMNotifyFsChanged;
window.FMUpdateMediaViews = FMUpdateMediaViews;
window.FMRefreshExplorer = (path) => {
  if (path) return FMNotifyFsChanged([path]);
  const jobs = [];
  if (typeof window.FMRefreshDesktop === "function") {
    jobs.push(Promise.resolve(window.FMRefreshDesktop()));
  }
  window.FMExplorerViews.forEach((view) => {
    jobs.push(Promise.resolve(view.refreshPaths([view.getPath()])));
  });
  return Promise.all(jobs);
};
