const Downloader = {
  download(items) {
    return TransferManager.download(items);
  },
};

kodApp.add({
  name: "downloader",
  title: "文件下载",
  sort: 0,
  menu: false,
  ext: [],
  icon: "/assets/kod/images/file_icon/icon_file/zip.png",
  open() {
    TransferManager.open("/");
  },
});
