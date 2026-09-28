(function () {
  const DP = "/assets/plugins/DPlayer/static/DPlayer";

  async function ensureDPlayer() {
    Loader.loadCss("/css/editor.css?v=14");
    Loader.loadCss(`${DP}/DPlayer.min.css`);
    await Loader.loadJs(`${DP}/lib/hls.min.js`);
    await Loader.loadJs(`${DP}/lib/flv.min.js`);
    await Loader.loadJs(`${DP}/DPlayer.min.js`);
    if (!window.DPlayer) throw new Error("DPlayer 加载失败");
  }

  function mediaType(ext) {
    const map = { f4v: "flv", f4a: "flv", m4a: "mp3", aac: "mp3", ogg: "oga" };
    return map[ext] || ext;
  }

  function frameFileName(name, time) {
    const dot = name.lastIndexOf(".");
    const base = dot > 0 ? name.slice(0, dot) : name;
    const ms = Math.floor(time * 1000) % 1000;
    const total = Math.floor(time);
    const hh = String(Math.floor(total / 3600)).padStart(2, "0");
    const mm = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
    const ss = String(total % 60).padStart(2, "0");
    return `${base}_${hh}-${mm}-${ss}.${String(ms).padStart(3, "0")}.png`;
  }

  function fitWindow(win, video) {
    if (!win || !video || win.maximized) return;
    const vWidth = video.videoWidth;
    const vHeight = video.videoHeight;
    if (!vWidth || !vHeight) return;
    const maxW = window.innerWidth * 0.9;
    const maxH = window.innerHeight * 0.9;
    let width = vWidth;
    let height = vHeight + 34;
    if (height > maxH) {
      const scale = (maxH - 34) / vHeight;
      width = Math.floor(vWidth * scale);
      height = Math.floor(maxH);
    }
    if (width > maxW) {
      const scale = maxW / width;
      width = Math.floor(maxW);
      height = Math.floor(height * scale);
    }
    width = Math.max(480, width);
    height = Math.max(320, height);
    win.el.style.width = `${width}px`;
    win.el.style.height = `${height}px`;
    win.el.style.left = `${Math.max(20, Math.floor((window.innerWidth - width) / 2))}px`;
    win.el.style.top = `${Math.max(20, Math.floor((window.innerHeight - height) / 2))}px`;
  }

  kodApp.add({
    name: "videoPlayer",
    title: "视频播放器",
    sort: 25,
    ext: ["mp4", "webm", "mov", "mkv", "avi", "m4v", "flv", "m3u8"],
    icon: "/assets/kod/images/file_icon/icon_file/movie/movie.png",
    async open(item, id) {
      const icon = "/assets/kod/images/file_icon/icon_file/movie/movie.png";
      if (!item) {
        const root = document.createElement("div");
        root.className = "app-empty";
        root.textContent = "在文件管理器中双击视频以播放";
        return WM.create({
          id,
          title: "视频播放器",
          icon,
          className: "dplayer-dialog",
          titleHtml: `<span class="path-ico" style="background-image:url('${icon}')"></span><span>视频播放器</span>`,
          width: Math.floor(window.innerWidth * 0.7),
          height: Math.floor(window.innerHeight * 0.6),
          content: root,
        });
      }
      await ensureDPlayer();
      const root = document.createElement("div");
      root.className = "dplayer-app";
      const box = document.createElement("div");
      root.appendChild(box);
      const fileIco = fileIcon(item);
      const win = WM.create({
        id,
        title: item.name,
        icon: fileIco,
        className: "dplayer-dialog",
        titleHtml: `<span class="path-ico" style="background-image:url('${fileIco}')"></span><span>${escapeHtml(item.name)}</span>`,
        width: Math.floor(window.innerWidth * 0.7),
        height: Math.floor(window.innerHeight * 0.6),
        content: root,
      });
      const ext = (item.ext || "").toLowerCase();
      let current = item;
      let player;
      async function saveVideoFrame() {
        const video = box.querySelector("video");
        if (!video || !video.videoWidth) throw new Error("当前无法截取画面");
        const canvas = document.createElement("canvas");
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(video, 0, 0);
        const blob = await new Promise((resolve) => {
          canvas.toBlob(resolve, "image/png");
        });
        if (!blob) throw new Error("截帧失败");
        const fileName = frameFileName(current.name, video.currentTime);
        const dest = parentPath(current.path);
        const file = new File([blob], fileName, { type: "image/png" });
        await API.upload(dest, file);
        await FMNotifyFsChanged([dest]);
        player.notice(`已保存 ${fileName}`);
      }
      player = new window.DPlayer({
        container: box,
        preload: "metadata",
        theme: "#f60",
        loop: false,
        autoplay: true,
        lang: "zh-cn",
        mutex: true,
        airplay: true,
        hotkey: true,
        video: {
          url: API.mediaUrl(item),
          type: mediaType(ext),
        },
        contextmenu: [
          {
            text: "下载视频",
            click: () => {
              const a = document.createElement("a");
              a.href = API.downloadUrl(current.path);
              a.download = current.name;
              a.click();
            },
          },
          {
            text: "保存视频帧",
            click: () => saveVideoFrame(),
          },
        ],
      });
      box.querySelectorAll(".dplayer-menu-item").forEach((el) => {
        const a = el.querySelector("a");
        if (!a) return;
        const href = a.getAttribute("href") || "";
        if (href.includes("diygod.me") || href.includes("MoePlayer/DPlayer")) el.remove();
      });
      const syncChrome = () => {
        const hide = box.classList.contains("dplayer-hide-controller");
        win.el.classList.toggle("hide-controller", hide);
      };
      player.on("loadeddata", () => {
        const video = box.querySelector("video");
        fitWindow(win, video);
        if (player.resize) player.resize();
      });
      box.addEventListener("click", () => setTimeout(syncChrome, 80));
      box.addEventListener("mousemove", () => setTimeout(syncChrome, 80));
      const mo = new MutationObserver(syncChrome);
      mo.observe(box, { attributes: true, attributeFilter: ["class"] });
      const mediaView = {
        hasPath: (path) => path === normalizeFsPath(current.path),
        update(fresh) {
          current = fresh;
          const video = box.querySelector("video");
          const wasPaused = video ? video.paused : true;
          player.switchVideo({ url: API.mediaUrl(fresh), type: mediaType(ext) });
          if (!wasPaused) player.play();
        },
      };
      window.FMMediaViews.add(mediaView);
      win.onClose = () => {
        window.FMMediaViews.delete(mediaView);
        mo.disconnect();
        player.pause();
        const video = box.querySelector("video");
        if (video) {
          video.pause();
          video.removeAttribute("src");
          video.load();
        }
        player.destroy();
      };
      const resize = () => {
        if (player.resize) player.resize();
      };
      setTimeout(resize, 50);
      const obs = new ResizeObserver(resize);
      obs.observe(root);
      const oldClose = win.onClose;
      win.onClose = () => {
        obs.disconnect();
        oldClose();
      };
      return win;
    },
  });
})();
