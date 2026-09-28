(function () {
  const AUDIO_EXT = ["mp3", "wav", "ogg", "flac", "m4a", "aac", "wma", "opus"];
  const GRADIENTS = [
    ["#85703c", "#7a2c5e"],
    ["#5fa716", "#2196F3"],
    ["#c23a5b", "#3a6bc2"],
    ["#2c7a7b", "#9c4221"],
    ["#553c9a", "#d53f8c"],
    ["#2b6cb0", "#38a169"],
  ];
  let playerUI = null;

  function esc(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmt(t) {
    if (!Number.isFinite(t)) return "00:00";
    const m = Math.floor(t / 60);
    const s = Math.floor(t % 60);
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }

  function gradientFor(name) {
    let h = 0;
    for (let i = 0; i < name.length; i += 1) h = (h * 31 + name.charCodeAt(i)) >>> 0;
    return GRADIENTS[h % GRADIENTS.length];
  }

  function createPlayer() {
    const root = document.createElement("div");
    root.className = "jPlayer jPlayer-music";
    root.innerHTML = `
      <div class="player-bg"></div>
      <div class="playerScreen">
        <div class="jPlayer-container"><audio preload="metadata"></audio></div>
        <div class="jPlayerMask"></div>
      </div>
      <div class="top-banner">
        <div class="item-title"></div>
        <div class="control-actions">
          <a href="javascript:;" class="play-backward" title="上一首"><i class="font-icon ri-rewind-fill"></i></a>
          <a href="javascript:;" class="play" title="播放"><i class="font-icon ri-play-fill"></i></a>
          <a href="javascript:;" class="pause" title="暂停"><i class="font-icon ri-stop-fill"></i></a>
          <a href="javascript:;" class="play-forward" title="下一首"><i class="font-icon ri-speed-fill"></i></a>
        </div>
        <div class="current-time-tips"><span>00:00</span></div>
        <div class="controlset right-volume">
          <a href="javascript:;" class="mute" title="静音"><i class="font-icon ri-volume-up-fill"></i></a>
          <a href="javascript:;" class="unmute" title="取消静音"><i class="font-icon ri-volume-mute-fill"></i></a>
          <div class="volumeblock">
            <div class="volume-control"><div class="volume-value" style="width:80%"></div></div>
          </div>
        </div>
        <div class="progress">
          <div class="fullBar"></div>
          <div class="seekBar"><div class="playBar"></div></div>
        </div>
      </div>
      <div class="play-tools">
        <span class="time">
          <span class="timer current">00:00</span> / <span class="timer duration">00:00</span>
        </span>
        <span class="right">
          <span class="change-loop" data-loop="0" title="列表循环"><i class="font-icon ri-repeat-line"></i></span>
          <span class="show-list" title="播放列表"><i class="font-icon ri-picture-in-picture-line"></i></span>
        </span>
      </div>
      <div class="play-list"><ul class="content"></ul></div>
    `;

    const state = {
      root,
      win: null,
      audio: root.querySelector("audio"),
      list: [],
      index: -1,
      loop: 0,
      volume: 0.8,
      muted: false,
      listVisible: true,
    };
    state.audio.volume = state.volume;
    bindPlayer(state);
    return state;
  }

  function bindPlayer(state) {
    const { root, audio } = state;
    const playBar = root.querySelector(".playBar");
    const currentEl = root.querySelector(".timer.current");
    const durationEl = root.querySelector(".timer.duration");
    const tipEl = root.querySelector(".current-time-tips span");
    const volValue = root.querySelector(".volume-value");
    const loopBtn = root.querySelector(".change-loop");

    const syncProgress = () => {
      const ratio = audio.duration ? (audio.currentTime / audio.duration) * 100 : 0;
      playBar.style.width = `${ratio}%`;
      currentEl.textContent = fmt(audio.currentTime);
      durationEl.textContent = fmt(audio.duration);
      tipEl.textContent = fmt(audio.currentTime);
    };

    const syncPlayUi = () => {
      root.classList.toggle("jp-state-playing", !audio.paused);
    };

    const syncMuteUi = () => {
      root.classList.toggle("jp-state-muted", state.muted || audio.volume === 0);
    };

    const syncLoopUi = () => {
      const icons = ["ri-repeat-line", "ri-repeat-one-line", "ri-order-play-line"];
      const titles = ["列表循环", "单曲循环", "顺序播放"];
      loopBtn.dataset.loop = String(state.loop);
      loopBtn.title = titles[state.loop];
      loopBtn.querySelector("i").className = `font-icon ${icons[state.loop]}`;
    };

    audio.addEventListener("timeupdate", syncProgress);
    audio.addEventListener("loadedmetadata", syncProgress);
    audio.addEventListener("play", syncPlayUi);
    audio.addEventListener("pause", syncPlayUi);
    audio.addEventListener("ended", () => {
      if (state.loop === 1) {
        audio.currentTime = 0;
        audio.play();
        return;
      }
      const next = state.index + 1;
      if (next < state.list.length) {
        playAt(state, next);
        return;
      }
      if (state.loop === 0 && state.list.length) {
        playAt(state, 0);
        return;
      }
      syncPlayUi();
    });

    root.querySelector(".play").onclick = () => {
      if (!state.list.length || state.index < 0) throw new Error("播放列表为空");
      audio.play();
    };
    root.querySelector(".pause").onclick = () => audio.pause();
    root.querySelector(".play-backward").onclick = () => {
      if (!state.list.length) throw new Error("播放列表为空");
      if (audio.currentTime > 3) {
        audio.currentTime = 0;
        return;
      }
      playAt(state, state.index <= 0 ? state.list.length - 1 : state.index - 1);
    };
    root.querySelector(".play-forward").onclick = () => {
      if (!state.list.length) throw new Error("播放列表为空");
      playAt(state, (state.index + 1) % state.list.length);
    };

    root.querySelector(".seekBar").onclick = (e) => {
      const rect = e.currentTarget.getBoundingClientRect();
      const p = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
      if (audio.duration) audio.currentTime = p * audio.duration;
    };
    root.querySelector(".seekBar").onmousemove = (e) => {
      const rect = e.currentTarget.getBoundingClientRect();
      const p = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
      tipEl.textContent = fmt((audio.duration || 0) * p);
      tipEl.parentElement.style.left = `${p * 100}%`;
      tipEl.parentElement.classList.add("show");
    };
    root.querySelector(".seekBar").onmouseleave = () => {
      tipEl.parentElement.classList.remove("show");
      tipEl.textContent = fmt(audio.currentTime);
    };

    root.querySelector(".mute").onclick = () => {
      state.muted = true;
      audio.muted = true;
      syncMuteUi();
    };
    root.querySelector(".unmute").onclick = () => {
      state.muted = false;
      audio.muted = false;
      syncMuteUi();
    };
    root.querySelector(".volume-control").onclick = (e) => {
      const rect = e.currentTarget.getBoundingClientRect();
      const p = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
      state.volume = p;
      audio.volume = p;
      volValue.style.width = `${p * 100}%`;
      if (p > 0) {
        state.muted = false;
        audio.muted = false;
      }
      syncMuteUi();
    };

    loopBtn.onclick = () => {
      state.loop = (state.loop + 1) % 3;
      syncLoopUi();
    };
    root.querySelector(".show-list").onclick = () => {
      state.listVisible = !state.listVisible;
      root.classList.toggle("list-hidden", !state.listVisible);
    };

    root.querySelector(".play-list .content").onclick = (e) => {
      const dl = e.target.closest(".download");
      const rm = e.target.closest(".remove");
      const li = e.target.closest(".item");
      if (!li) return;
      const idx = Number(li.dataset.index);
      if (dl) {
        const it = state.list[idx];
        const a = document.createElement("a");
        a.href = API.downloadUrl(it.path);
        a.download = it.name;
        a.click();
        return;
      }
      if (rm) {
        removeAt(state, idx);
        return;
      }
      playAt(state, idx);
    };

    syncLoopUi();
    syncMuteUi();
    volValue.style.width = `${state.volume * 100}%`;
    state.syncProgress = syncProgress;
  }

  function renderList(state) {
    const ul = state.root.querySelector(".play-list .content");
    ul.innerHTML = state.list.map((it, i) => `
      <li class="item${i === state.index ? " this" : ""}" data-index="${i}">
        <span class="name">${esc(it.name)}</span>
        <div class="action-right">
          <span class="download" title="下载"><i class="font-icon ri-download-fill-2"></i></span>
          <span class="remove" title="移除"><i class="font-icon ri-close-line"></i></span>
        </div>
      </li>
    `).join("");
  }

  function setTheme(state, name) {
    const [a, b] = gradientFor(name);
    state.root.querySelector(".player-bg").style.backgroundImage =
      `linear-gradient(160deg, ${a}, ${b})`;
  }

  function playAt(state, index) {
    if (index < 0 || index >= state.list.length) throw new Error("播放列表索引无效");
    state.index = index;
    const item = state.list[index];
    const { audio, root, win } = state;
    audio.src = API.mediaUrl(item);
    audio.load();
    audio.play();
    root.querySelector(".item-title").textContent = item.name;
    setTheme(state, item.name);
    renderList(state);
    if (win) {
      win.title = item.name;
      win.icon = fileIcon(item);
      win.el.querySelector(".win-title").innerHTML =
        `<span class="path-ico" style="background-image:url('${fileIcon(item)}')"></span><span>${esc(item.name)}</span>`;
      WM.renderTaskbar();
    }
  }

  function clearPlayback(state) {
    const { audio, root, win } = state;
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
    state.index = -1;
    root.querySelector(".item-title").textContent = "音频播放器";
    root.querySelector(".playBar").style.width = "0%";
    root.querySelector(".timer.current").textContent = "00:00";
    root.querySelector(".timer.duration").textContent = "00:00";
    root.classList.remove("jp-state-playing");
    const icon = "/assets/kod/images/file_icon/icon_file/music.png";
    if (win) {
      win.title = "音频播放器";
      win.icon = icon;
      win.el.querySelector(".win-title").innerHTML =
        `<span class="path-ico" style="background-image:url('${icon}')"></span><span>音频播放器</span>`;
      WM.renderTaskbar();
    }
    renderList(state);
  }

  function removeAt(state, index) {
    state.list.splice(index, 1);
    if (!state.list.length) {
      clearPlayback(state);
      return;
    }
    if (index < state.index) state.index -= 1;
    else if (index === state.index) {
      const next = Math.min(state.index, state.list.length - 1);
      playAt(state, next);
      return;
    }
    renderList(state);
  }

  async function collectPlaylist(item) {
    const parent = parentPath(item.path);
    const res = await API.list(parent);
    const audios = res.data.fileList.filter((f) => AUDIO_EXT.includes(String(f.ext || "").toLowerCase()));
    if (!audios.length) return [item];
    return audios;
  }

  kodApp.add({
    name: "audioPlayer",
    title: "音频播放器",
    sort: 15,
    singleton: true,
    ext: AUDIO_EXT,
    icon: "/assets/kod/images/file_icon/icon_file/music.png",
    async open(item, id) {
      Loader.loadCss("/css/editor.css?v=12");
      Loader.loadCss("/assets/kod/style/lib/font-icon/style.css");
      const icon = "/assets/kod/images/file_icon/icon_file/music.png";

      if (playerUI && playerUI.win && WM.windows.get(id)) {
        WM.focus(id);
        if (playerUI.win.minimized) WM.restore(id);
        if (!item) return playerUI.win;
        const exist = playerUI.list.findIndex((x) => x.path === item.path);
        if (exist >= 0) playAt(playerUI, exist);
        else {
          playerUI.list.push(item);
          playAt(playerUI, playerUI.list.length - 1);
        }
        return playerUI.win;
      }

      const state = createPlayer();
      const win = WM.create({
        id,
        title: item ? item.name : "音频播放器",
        icon: item ? fileIcon(item) : icon,
        width: 360,
        height: 500,
        className: "music-player-dialog dialog-simple",
        titleHtml: item
          ? `<span class="path-ico" style="background-image:url('${fileIcon(item)}')"></span><span>${esc(item.name)}</span>`
          : `<span class="path-ico" style="background-image:url('${icon}')"></span><span>音频播放器</span>`,
        content: state.root,
      });
      state.win = win;
      playerUI = state;
      const mediaView = {
        hasPath: (path) => state.list.some((x) => normalizeFsPath(x.path) === path),
        update(fresh) {
          const target = normalizeFsPath(fresh.path);
          state.list = state.list.map((x) => (normalizeFsPath(x.path) === target ? fresh : x));
          const cur = state.list[state.index];
          if (!cur || normalizeFsPath(cur.path) !== target) return;
          const { audio } = state;
          const wasPaused = audio.paused;
          audio.src = API.mediaUrl(fresh);
          audio.load();
          if (!wasPaused) audio.play();
        },
      };
      window.FMMediaViews.add(mediaView);
      if (item) {
        state.list = await collectPlaylist(item);
        const idx = state.list.findIndex((x) => x.path === item.path);
        playAt(state, idx >= 0 ? idx : 0);
      } else {
        clearPlayback(state);
      }
      win.onClose = () => {
        window.FMMediaViews.delete(mediaView);
        state.audio.pause();
        state.audio.removeAttribute("src");
        state.audio.load();
        playerUI = null;
      };
      return win;
    },
  });
})();
