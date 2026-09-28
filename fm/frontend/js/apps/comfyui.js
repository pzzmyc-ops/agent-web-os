const ComfyUIApp = {
  name: "comfyui",
  title: "ComfyUI",
  sort: 5,
  singleton: true,
  ext: [],
  icon: "/images/comfyui.svg",

  open(item, id) {
    const frame = document.createElement("iframe");
    frame.className = "agent-frame";
    frame.src = "/comfyui/";
    frame.style.cssText = "width:100%;height:100%;border:0;display:block;";
    const cover = document.createElement("div");
    cover.style.cssText = "position:absolute;inset:0;display:none;z-index:6;";
    const win = WM.create({
      id,
      title: this.title,
      icon: this.icon,
      width: 1280,
      height: 800,
      content: frame,
      className: "win-agent",
    });
    win.body.style.position = "relative";
    win.body.appendChild(cover);

    const show = () => {
      if (window.FMDragState && FMDragState.paths.length) cover.style.display = "block";
    };
    const hide = () => {
      cover.style.display = "none";
    };
    document.addEventListener("dragstart", show);
    document.addEventListener("dragend", hide);
    win.onClose = () => {
      document.removeEventListener("dragstart", show);
      document.removeEventListener("dragend", hide);
    };

    cover.addEventListener("dragover", (e) => {
      if (!window.FMDragState || !FMDragState.paths.length) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    });
    cover.addEventListener("drop", (e) => {
      e.preventDefault();
      hide();
      const paths = fmDragPaths(e);
      window.FMDragState.paths = [];
      this.dropPaths(frame, paths).catch((err) => {
        toast(err.message || String(err), "error");
      });
    });
    return win;
  },

  async dropPaths(frame, paths) {
    const w = frame.contentWindow;
    if (!w || typeof w.app !== "object") {
      throw new Error("ComfyUI 还没准备好接收文件");
    }
    const list = [...new Set((paths || []).filter(Boolean))];
    if (!list.length) throw new Error("没有可放入的文件");
    for (const path of list) {
      const name = pathBaseName(path);
      const res = await fetch(API.downloadUrl(path), { cache: "no-store" });
      if (!res.ok) throw new Error("读不到文件: " + name);
      const blob = await res.blob();
      if (name.toLowerCase().endsWith(".json")) {
        await this.loadWorkflow(w, name, blob);
        continue;
      }
      if (typeof w.app.handleFile !== "function") {
        throw new Error("ComfyUI 还没准备好接收文件");
      }
      const file = new File([blob], name, { type: this.fileType(name) });
      await w.app.handleFile(file);
    }
  },

  async loadWorkflow(w, name, blob) {
    const data = JSON.parse(await blob.text());
    if (!data || typeof data !== "object" || Array.isArray(data)) {
      throw new Error("不是可识别的 ComfyUI 工作流: " + name);
    }
    const graph = data.workflow || data;
    if (graph.nodes && typeof w.app.loadGraphData === "function") {
      await w.app.loadGraphData(graph, true, true, null);
      return;
    }
    if (data.prompt && typeof w.app.loadApiJson === "function") {
      await w.app.loadApiJson(data.prompt);
      return;
    }
    throw new Error("不是可识别的 ComfyUI 工作流: " + name);
  },

  fileType(name) {
    const n = String(name || "").toLowerCase();
    if (n.endsWith(".json")) return "application/json";
    if (n.endsWith(".png")) return "image/png";
    if (n.endsWith(".jpg") || n.endsWith(".jpeg")) return "image/jpeg";
    if (n.endsWith(".webp")) return "image/webp";
    if (n.endsWith(".gif")) return "image/gif";
    if (n.endsWith(".mp4")) return "video/mp4";
    if (n.endsWith(".webm")) return "video/webm";
    if (n.endsWith(".wav")) return "audio/wav";
    if (n.endsWith(".mp3")) return "audio/mpeg";
    throw new Error("不支持的文件类型: " + name);
  },
};
