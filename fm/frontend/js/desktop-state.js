const DesktopState = {
  data: { windows: [], iconPos: {}, rects: {} },
  _timer: 0,

  async load() {
    const res = await fetch("/api/desktop/state", { cache: "no-store" });
    if (!res.ok) throw new Error("读取桌面状态失败: HTTP " + res.status);
    const body = await res.json();
    if (!body.ok || !body.state || typeof body.state !== "object") {
      throw new Error("桌面状态格式不对");
    }
    const state = body.state;
    this.data = {
      windows: Array.isArray(state.windows) ? state.windows : [],
      iconPos: state.iconPos && typeof state.iconPos === "object" ? state.iconPos : {},
      rects: state.rects && typeof state.rects === "object" ? state.rects : {},
    };
  },

  get(key) {
    return this.data[key];
  },

  set(key, value) {
    this.data[key] = value;
    clearTimeout(this._timer);
    this._timer = setTimeout(() => {
      this.push().catch((err) => console.error("[desktop-state] 保存失败:", err));
    }, 200);
  },

  async push() {
    clearTimeout(this._timer);
    await DesktopLink.ready;
    if (DesktopLink.evicted) return;
    const res = await fetch("/api/desktop/state", {
      method: "PUT",
      cache: "no-store",
      keepalive: true,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ desktopId: DesktopLink.desktopId, state: this.data }),
    });
    if (!res.ok) throw new Error("保存桌面状态失败: HTTP " + res.status);
  },
};
