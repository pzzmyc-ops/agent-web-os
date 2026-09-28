function clipboardDataHasFiles(dt) {
  if (!dt) return false;
  if (dt.files && dt.files.length) return true;
  const items = dt.items ? [...dt.items] : [];
  return items.some((it) => it.kind === "file");
}

async function pasteIntoPath(destPath, dataTransfer) {
  const dest = destPath || window.FMEnv.workspace;
  if (clipboardDataHasFiles(dataTransfer)) {
    await Uploader.addFromDataTransfer(dataTransfer, dest);
    return;
  }
  if (AppClipboard.paths.length && AppClipboard.mode) {
    const srcParents = AppClipboard.paths.map((p) => parentPath(p));
    if (AppClipboard.mode === "copy") await API.copy(AppClipboard.paths, dest);
    else {
      await API.move(AppClipboard.paths, dest);
      AppClipboard.paths = [];
      AppClipboard.mode = null;
    }
    await FMNotifyFsChanged([...srcParents, dest]);
    return;
  }
  if (!dataTransfer) {
    throw new Error("右键菜单读不到系统复制的文件，请先点一下要粘贴的位置再按 Ctrl+V");
  }
  throw new Error("剪贴板里没有文件");
}

const Uploader = {
  get onComplete() {
    return TransferManager.onComplete;
  },
  set onComplete(fn) {
    TransferManager.onComplete = fn;
  },
  open(destPath, files) {
    TransferManager.open(destPath || window.FMEnv.workspace);
    if (files && files.length) {
      return TransferManager.enqueueUploads(files, destPath);
    }
    return null;
  },
  addFiles(fileList, destPath) {
    return TransferManager.enqueueUploads(fileList, destPath);
  },
  addFromDataTransfer(dataTransfer, destPath) {
    return TransferManager.addFromDataTransfer(dataTransfer, destPath);
  },
};

kodApp.add({
  name: "uploader",
  title: "文件上传",
  sort: 0,
  menu: false,
  ext: [],
  icon: "/assets/kod/images/common/drop_upload.png",
  open() {
    TransferManager.open(window.FMEnv.workspace);
  },
});
