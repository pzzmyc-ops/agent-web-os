import os

from appconfig import load_config

_cfg = load_config()

ROOT = _cfg.fm_root_dir
if not os.path.isabs(ROOT):
    raise RuntimeError("fm_root must be an absolute path")
if not os.path.isdir(ROOT):
    raise RuntimeError(f"fm_root is not a directory: {ROOT}")
ROOT = os.path.realpath(ROOT)

# OnlyOffice Document Server 的**对外入口(nginx)**,不是内部 DocService。
# 这两个很容易搞混:Windows 社区版默认 nginx 在 :80、Node DocService 在 :8000,
# 而 :8000 上 /healthcheck、/web-apps/apps/api/documents/api.js、ConvertService
# 全都正常响应 —— 只有编辑器的版本化静态路径
#   /<version>/web-apps/apps/spreadsheeteditor/main/index.html
# 缺失(那条重写规则只在 nginx 里),表现为浏览器报 "Cannot GET /<version>/..."。
# 所以这里必须填 nginx 的地址。
ONLYOFFICE_URL = (_cfg.onlyoffice_url or "http://127.0.0.1").rstrip("/")
ONLYOFFICE_JWT = _cfg.onlyoffice_jwt
# Document Server 要用这个地址反向拉取文档、回调保存,所以它必须是「从 Document Server
# 看得到的我们」—— 不能是 127.0.0.1(那在容器/远程 DS 里指向 DS 自己)。同机部署时
# 127.0.0.1 可用,跨机要在 config.json 的 public_url 填本机在那张网上的 IP。
PUBLIC_URL = _cfg.public_url_effective
DESKTOP_NAME = "桌面"
DESKTOP_PATH = os.path.join(ROOT, DESKTOP_NAME)
if not os.path.isdir(DESKTOP_PATH):
    os.makedirs(DESKTOP_PATH)
