const Loader = {
  css: new Set(),
  js: new Map(),

  loadCss(href) {
    if (this.css.has(href)) return;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    document.head.appendChild(link);
    this.css.add(href);
  },

  loadJs(src) {
    if (this.js.has(src)) return this.js.get(src);
    const p = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = () => resolve();
      s.onerror = () => reject(new Error(`脚本加载失败: ${src}`));
      document.head.appendChild(s);
    });
    this.js.set(src, p);
    return p;
  },
};
