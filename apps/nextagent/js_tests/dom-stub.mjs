/**
 * 最小 DOM / 浏览器桩:让 static/js 下的浏览器 ES 模块能在 node 里被 import。
 *
 * 目的只有一个 —— 让「回合结束谁说了算」这条状态机可以被真正跑起来测,
 * 而不是只靠读源码猜。所以只实现这些模块实际用到的少数接口,
 * 选择器也只支持 .a.b 和 [attr="v"] 这两种写法,认不出的一律不匹配。
 */

class FakeClassList {
  constructor(el) {
    this.el = el;
  }
  _tokens() {
    return String(this.el.className || "").split(/\s+/).filter(Boolean);
  }
  _set(tokens) {
    this.el.className = tokens.join(" ");
  }
  add(...names) {
    const t = new Set(this._tokens());
    names.forEach((n) => t.add(n));
    this._set([...t]);
  }
  remove(...names) {
    const t = new Set(this._tokens());
    names.forEach((n) => t.delete(n));
    this._set([...t]);
  }
  contains(name) {
    return this._tokens().includes(name);
  }
  toggle(name, force) {
    const has = this.contains(name);
    const want = force === undefined ? !has : !!force;
    if (want) this.add(name);
    else this.remove(name);
    return want;
  }
}

const SELECTOR = /^((?:\.[A-Za-z0-9_-]+)*)(?:\[([A-Za-z0-9_-]+)(?:="([^"]*)")?\])?$/;

function matches(el, selector) {
  const m = SELECTOR.exec(String(selector || "").trim());
  if (!m) return false;
  const classes = m[1] ? m[1].split(".").filter(Boolean) : [];
  const attr = m[2];
  if (!classes.length && !attr) return false;
  for (const c of classes) {
    if (!el.classList.contains(c)) return false;
  }
  if (attr) {
    const have = el.getAttribute(attr);
    if (have === undefined || have === null) return false;
    if (m[3] !== undefined && String(have) !== m[3]) return false;
  }
  return true;
}

function descendants(el, out) {
  for (const child of el.children) {
    out.push(child);
    descendants(child, out);
  }
  return out;
}

export class FakeElement {
  constructor(tagName = "div") {
    this.tagName = String(tagName).toUpperCase();
    this.id = "";
    this.className = "";
    this.classList = new FakeClassList(this);
    this.style = { cssText: "" };
    this.dataset = {};
    this.attributes = {};
    this.children = [];
    this.parentNode = null;
    this._text = "";
    this._html = "";
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.offsetHeight = 0;
    this.offsetWidth = 0;
    this.clientHeight = 0;
    this.disabled = false;
    this.hidden = false;
    this.title = "";
    this.value = "";
    this.checked = false;
    this.href = "";
    this.src = "";
  }
  get textContent() {
    return this._text;
  }
  set textContent(v) {
    this._text = v === null || v === undefined ? "" : String(v);
    this.children = [];
  }
  get innerHTML() {
    return this._html;
  }
  set innerHTML(v) {
    this._html = v === null || v === undefined ? "" : String(v);
    this.children = [];
  }
  get firstChild() {
    return this.children[0] || null;
  }
  get lastChild() {
    return this.children[this.children.length - 1] || null;
  }
  get isConnected() {
    return !!this.parentNode;
  }
  appendChild(child) {
    if (child.parentNode) child.parentNode.removeChild(child);
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  insertBefore(child, ref) {
    const i = ref ? this.children.indexOf(ref) : -1;
    if (i < 0) return this.appendChild(child);
    child.parentNode = this;
    this.children.splice(i, 0, child);
    return child;
  }
  removeChild(child) {
    const i = this.children.indexOf(child);
    if (i >= 0) this.children.splice(i, 1);
    child.parentNode = null;
    return child;
  }
  replaceChildren(...kids) {
    this.children = [];
    kids.forEach((k) => this.appendChild(k));
  }
  remove() {
    if (this.parentNode) this.parentNode.removeChild(this);
  }
  setAttribute(name, value) {
    const key = String(name);
    this.attributes[key] = String(value);
    if (key === "id") this.id = String(value);
    if (key === "class") this.className = String(value);
    if (key.startsWith("data-")) {
      const camel = key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
      this.dataset[camel] = String(value);
    }
  }
  getAttribute(name) {
    const key = String(name);
    if (key === "id") return this.id || undefined;
    if (key === "class") return this.className || undefined;
    return this.attributes[key];
  }
  hasAttribute(name) {
    return this.getAttribute(name) !== undefined;
  }
  removeAttribute(name) {
    delete this.attributes[String(name)];
  }
  addEventListener() {}
  removeEventListener() {}
  dispatchEvent() {
    return true;
  }
  querySelectorAll(selector) {
    return descendants(this, []).filter((el) => matches(el, selector));
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
  closest() {
    return null;
  }
  contains(node) {
    return descendants(this, []).includes(node);
  }
  cloneNode() {
    return new FakeElement(this.tagName);
  }
  focus() {}
  blur() {}
  click() {}
  scrollIntoView() {}
  getBoundingClientRect() {
    return { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 };
  }
}

function makeStorage() {
  const data = new Map();
  return {
    getItem: (k) => (data.has(String(k)) ? data.get(String(k)) : null),
    setItem: (k, v) => data.set(String(k), String(v)),
    removeItem: (k) => data.delete(String(k)),
    clear: () => data.clear(),
    key: (i) => [...data.keys()][i] ?? null,
    get length() {
      return data.size;
    },
  };
}

function defineGlobal(name, value) {
  try {
    Object.defineProperty(globalThis, name, {
      value,
      configurable: true,
      writable: true,
    });
  } catch {
    /* node 已经把某些全局钉死了,忽略即可 */
  }
}

export function installDom() {
  const byId = new Map();
  const document = {
    createElement: (tag) => new FakeElement(tag),
    createElementNS: (_ns, tag) => new FakeElement(tag),
    createTextNode: (t) => {
      const el = new FakeElement("#text");
      el.textContent = t;
      return el;
    },
    createDocumentFragment: () => new FakeElement("#fragment"),
    getElementById: (id) => {
      const key = String(id);
      if (!byId.has(key)) {
        const el = new FakeElement("div");
        el.id = key;
        byId.set(key, el);
      }
      return byId.get(key);
    },
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: () => {},
    removeEventListener: () => {},
    body: new FakeElement("body"),
    head: new FakeElement("head"),
    documentElement: new FakeElement("html"),
    visibilityState: "visible",
    readyState: "complete",
    cookie: "",
    title: "",
  };

  const window = {
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => true,
    document,
    location: {
      href: "http://localhost/",
      pathname: "/",
      search: "",
      origin: "http://localhost",
      protocol: "http:",
      host: "localhost",
    },
    localStorage: makeStorage(),
    sessionStorage: makeStorage(),
    matchMedia: () => ({
      matches: false,
      addEventListener: () => {},
      removeEventListener: () => {},
    }),
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    queueMicrotask,
    requestAnimationFrame: (fn) => setTimeout(fn, 0),
    cancelAnimationFrame: (handle) => clearTimeout(handle),
    getComputedStyle: () => ({ getPropertyValue: () => "" }),
    navigator: { userAgent: "node", clipboard: { writeText: async () => {} } },
    innerWidth: 1280,
    innerHeight: 800,
    crypto: globalThis.crypto,
  };
  window.window = window;
  window.self = window;
  window.top = window;

  defineGlobal("document", document);
  defineGlobal("window", window);
  defineGlobal("self", window);
  defineGlobal("location", window.location);
  defineGlobal("localStorage", window.localStorage);
  defineGlobal("sessionStorage", window.sessionStorage);
  defineGlobal("navigator", window.navigator);
  defineGlobal("requestAnimationFrame", window.requestAnimationFrame);
  defineGlobal("cancelAnimationFrame", window.cancelAnimationFrame);
  defineGlobal("getComputedStyle", window.getComputedStyle);

  return { document, window };
}
