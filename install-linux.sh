#!/usr/bin/env bash
set -euo pipefail

NO_COMFYUI=0
NO_DSH=0
NO_HERMES=0
NO_OFFICE=0
NO_RDP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --no-comfyui) NO_COMFYUI=1 ;;
    --no-dsh) NO_DSH=1 ;;
    --no-hermes) NO_HERMES=1 ;;
    --no-office) NO_OFFICE=1 ;;
    --no-rdp) NO_RDP=1 ;;
    *)
      echo "未知参数: $1" >&2
      exit 1
      ;;
  esac
  shift
done

COMFYUI_PATH=""
PY_VER="3.13.9"
MODEL="deepseek-v4-flash"
EMBEDDING_MODEL="bge-m3"
WEB_PORT=19080
ONLYOFFICE_PORT=18180
OLLAMA_PORT_PRESET=19134
COMFYUI_PORT=18188
HERMES_PORT=19787
HERMES_EMBED_PORT=19788
DEEPSEEK_PORT=19180
DEEPSEEK_EMBED_PORT=19181
REMOTE_PORT=19482
GUACD_PORT_PRESET=19822

wsl_path() {
  local raw="$1"
  raw="${raw//\\//}"
  if [[ "$raw" =~ ^([A-Za-z]):/(.*)$ ]]; then
    local drive="${BASH_REMATCH[1]}"
    drive="${drive,,}"
    printf '%s\n' "/mnt/${drive}/${BASH_REMATCH[2]}"
    return
  fi
  printf '%s\n' "$raw"
}

COMFYUI_DIR=""
if [ -n "$COMFYUI_PATH" ]; then
  COMFYUI_DIR="$(wsl_path "$COMFYUI_PATH")"
  echo "使用已有 ComfyUI: $COMFYUI_PATH -> $COMFYUI_DIR"
  if [ ! -f "$COMFYUI_DIR/main.py" ]; then
    echo "找不到 ComfyUI: $COMFYUI_DIR/main.py" >&2
    exit 1
  fi
fi

if [ "$(uname -s)" != "Linux" ]; then
  echo "只支持 Linux / WSL" >&2
  exit 1
fi
if [ "$(id -u)" -eq 0 ]; then
  echo "不要用 root 运行,需要提权时脚本会自己 sudo" >&2
  exit 1
fi
if [ ! -d /run/systemd/system ]; then
  echo "未启用 systemd。WSL 在 /etc/wsl.conf 写入 [boot] systemd=true 后执行 wsl --terminate $WSL_DISTRO_NAME 再跑,不要 wsl --shutdown" >&2
  exit 1
fi
for cmd in sudo python3 curl git nvidia-smi; do
  if ! command -v "$cmd" >/dev/null; then
    echo "找不到命令: $cmd" >&2
    exit 1
  fi
  echo "命令 $cmd: $(command -v "$cmd")"
done
echo "Python $(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
echo "检查 NVIDIA 驱动"
nvidia-smi

BOOT="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/mafagent"
echo "脚本目录 $BOOT"
echo "安装目录 $DEST"

ensure_clone() {
  url="$1"
  dest="$2"
  marker="$3"
  echo "克隆 $url -> $dest"
  if [ -d "$dest/.git" ]; then
    if ! got="$(git -C "$dest" remote get-url origin)"; then
      echo "未完成: 无法读取 $dest 的远程地址，删除后继续"
      rm -rf "$dest"
    else
      got="${got%$'\r'}"
      if [ "$got" != "$url" ]; then
        echo "仓库地址不对: $dest origin=$got 期望 $url" >&2
        exit 1
      fi
      if [ -f "$dest/$marker" ]; then
        echo "已完成: $dest"
        echo "可用: $dest/$marker"
        return
      fi
      echo "未完成: $dest 远程匹配但缺少 $marker，删除后继续"
      rm -rf "$dest"
    fi
  elif [ -e "$dest" ]; then
    echo "未完成: $dest 存在但不是 git 仓库，删除后继续"
    rm -rf "$dest"
  fi
  mkdir -p "$(dirname "$dest")"
  echo "git clone $url -> $dest"
  git clone --depth 1 "$url" "$dest"
  echo "克隆完成: $dest"
  if [ ! -f "$dest/$marker" ]; then
    echo "不可用: 缺少 $dest/$marker" >&2
    exit 1
  fi
  echo "可用: $dest/$marker"
}

ensure_clone "https://github.com/pzzmyc-ops/mafagent.git" "$DEST" "server.py"
if [ "$NO_HERMES" -eq 0 ]; then
  ensure_clone "https://github.com/NousResearch/hermes-agent.git" "$DEST/apps/hermes-agent" "run_agent.py"
  ensure_clone "https://github.com/nesquena/hermes-webui.git" "$DEST/apps/hermes-webui" "server.py"
else
  echo "跳过 Hermes"
fi
if [ "$NO_COMFYUI" -eq 1 ]; then
  echo "跳过 ComfyUI"
elif [ -n "$COMFYUI_DIR" ]; then
  link="$DEST/apps/comfyui"
  if [ -L "$link" ] && [ "$(readlink -f "$link")" = "$(readlink -f "$COMFYUI_DIR")" ] && [ -f "$link/main.py" ]; then
    echo "已完成: $link -> $COMFYUI_DIR"
  else
    if [ -e "$link" ] || [ -L "$link" ]; then
      echo "未完成: $link 不是指向 $COMFYUI_DIR 的链接，删除后继续"
      rm -rf "$link"
    fi
    echo "链接 $link -> $COMFYUI_DIR"
    ln -sv "$COMFYUI_DIR" "$link"
  fi
  if [ ! -f "$link/main.py" ]; then
    echo "不可用: 缺少 $link/main.py" >&2
    exit 1
  fi
  echo "可用: $link/main.py"
else
  ensure_clone "https://github.com/comfyanonymous/ComfyUI.git" "$DEST/apps/comfyui" "main.py"
fi
if [ "$NO_DSH" -eq 0 ]; then
  ensure_clone "https://github.com/deepseek-ai/deepseek-harness.git" "$DEST/apps/deepseek" "apps/cli/package.json"
else
  echo "跳过 DeepSeek"
fi

ROOT="$DEST"
cd "$ROOT"
config_ok() {
  python3 - "$ROOT/config.json" <<'PY'
import json, sys
path = sys.argv[1]
raw = json.loads(open(path, encoding="utf-8").read())
need = ("api_key", "model", "onlyoffice_jwt", "embedding_model", "web_port", "onlyoffice_url", "ollama_url", "public_url", "comfyui_port", "hermes_port", "hermes_embed_port", "deepseek_port", "deepseek_embed_port", "remote_port", "guacd_port")
if not isinstance(raw, dict):
    raise SystemExit(1)
for name in need:
    if raw.get(name) in (None, ""):
        raise SystemExit(1)
PY
}
if [ -f "$ROOT/config.json" ] && config_ok; then
  echo "已完成: $ROOT/config.json"
else
  echo "按脚本预设写入 $ROOT/config.json"
  python3 - "$ROOT/config.json" "$MODEL" "$EMBEDDING_MODEL" "$WEB_PORT" "$ONLYOFFICE_PORT" "$OLLAMA_PORT_PRESET" "$COMFYUI_PORT" "$HERMES_PORT" "$HERMES_EMBED_PORT" "$DEEPSEEK_PORT" "$DEEPSEEK_EMBED_PORT" "$REMOTE_PORT" "$GUACD_PORT_PRESET" <<'PY'
import json, secrets, sys
dest, model, embed, web, oo, ollama, comfy, hermes, hermes_embed, deepseek, deepseek_embed, remote, guacd = sys.argv[1:]
out = {
    "base_url": "",
    "api_key": secrets.token_hex(16),
    "model": model,
    "max_context_window_tokens": 1048576,
    "max_output_tokens": 16384,
    "web_port": int(web),
    "data_dir": "data",
    "fm_root": "data/workspace",
    "onlyoffice_url": "http://127.0.0.1:%s" % oo,
    "onlyoffice_jwt": secrets.token_hex(16),
    "public_url": "http://127.0.0.1:%s" % web,
    "ollama_url": "http://127.0.0.1:%s" % ollama,
    "embedding_model": embed,
    "comfyui_port": int(comfy),
    "hermes_port": int(hermes),
    "hermes_embed_port": int(hermes_embed),
    "deepseek_port": int(deepseek),
    "deepseek_embed_port": int(deepseek_embed),
    "remote_port": int(remote),
    "guacd_port": int(guacd),
}
open(dest, "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
print("已生成 api_key 与 onlyoffice_jwt")
PY
fi
if ! config_ok; then
  echo "不可用: $ROOT/config.json" >&2
  exit 1
fi
echo "可用: $ROOT/config.json"

echo "检查克隆结果"
need_files=("$ROOT/server.py" "$ROOT/apps/remote/gateway.js" "$ROOT/vendor/guacamole/guacamole-common-js/all.min.js" "$ROOT/data/workspace/skills")
if [ "$NO_HERMES" -eq 0 ]; then
  need_files+=("$ROOT/apps/hermes-agent/run_agent.py" "$ROOT/apps/hermes-webui/server.py")
fi
if [ "$NO_COMFYUI" -eq 0 ]; then
  need_files+=("$ROOT/apps/comfyui/main.py")
fi
if [ "$NO_DSH" -eq 0 ]; then
  need_files+=("$ROOT/apps/deepseek/apps/cli/package.json")
fi
for need in "${need_files[@]}"
do
  if [ ! -e "$need" ]; then
    echo "不可用: 缺少 $need" >&2
    exit 1
  fi
  echo "可用: $need"
done

eval "$(python3 - "$ROOT/config.json" <<'PY'
import json, sys
from urllib.parse import urlparse
path = sys.argv[1]
raw = json.loads(open(path, encoding="utf-8").read())
if not isinstance(raw, dict):
    raise SystemExit("config.json 不是对象")
jwt = str(raw.get("onlyoffice_jwt") or "").strip()
model = str(raw.get("embedding_model") or "").strip()
oo = str(raw.get("onlyoffice_url") or "").strip()
ollama = str(raw.get("ollama_url") or "").strip()
web = raw.get("web_port")
need = ("comfyui_port", "hermes_port", "hermes_embed_port", "deepseek_port", "deepseek_embed_port", "remote_port", "guacd_port")
if not jwt:
    raise SystemExit("config.json 未配置 onlyoffice_jwt")
if not model:
    raise SystemExit("config.json 未配置 embedding_model")
if not oo:
    raise SystemExit("config.json 未配置 onlyoffice_url")
if not ollama:
    raise SystemExit("config.json 未配置 ollama_url")
if web is None:
    raise SystemExit("config.json 未配置 web_port")
for name in need:
    if raw.get(name) is None:
        raise SystemExit("config.json 未配置 %s" % name)
parsed = urlparse(oo)
if parsed.scheme != "http" or not parsed.hostname:
    raise SystemExit("onlyoffice_url 必须是 http 地址")
oo_port = parsed.port if parsed.port is not None else 80
op = urlparse(ollama)
if op.scheme != "http" or not op.hostname:
    raise SystemExit("ollama_url 必须是 http 地址")
ollama_port = op.port if op.port is not None else 80
print("OO_PORT=%s" % oo_port)
print("OO_JWT=%s" % json.dumps(jwt))
print("EMBED_MODEL=%s" % json.dumps(model))
print("OLLAMA_PORT=%s" % ollama_port)
print("WEB_PORT=%s" % int(web))
print("COMFY_PORT=%s" % int(raw["comfyui_port"]))
print("HERMES_PORT=%s" % int(raw["hermes_port"]))
print("DEEPSEEK_PORT=%s" % int(raw["deepseek_port"]))
print("REMOTE_PORT=%s" % int(raw["remote_port"]))
print("GUACD_PORT=%s" % int(raw["guacd_port"]))
PY
)"

port_open() {
  python3 - "$1" <<'PY'
import socket, sys
port = int(sys.argv[1])
s = socket.socket()
s.settimeout(3)
r = s.connect_ex(("127.0.0.1", port))
s.close()
raise SystemExit(0 if r == 0 else 1)
PY
}

listen() {
  echo "检查 127.0.0.1:$1 是否在监听"
  if ! port_open "$1"; then
    echo "不可用: 127.0.0.1:$1 没有在监听" >&2
    return 1
  fi
  echo "可用: 127.0.0.1:$1"
}

pkg_installed() {
  dpkg -s "$1" >/dev/null 2>&1
}

ensure_pkgs() {
  local missing=()
  local pkg
  for pkg in "$@"; do
    if pkg_installed "$pkg"; then
      echo "已安装: $pkg"
    else
      echo "未安装: $pkg"
      missing+=("$pkg")
    fi
  done
  if [ "${#missing[@]}" -gt 0 ]; then
    echo "安装软件包: ${missing[*]}"
    sudo apt-get update
    sudo apt-get install -y "${missing[@]}"
  fi
  for pkg in "$@"; do
    if ! pkg_installed "$pkg"; then
      echo "不可用: $pkg 安装后仍不存在" >&2
      exit 1
    fi
  done
  echo "软件包可用: $*"
}

export DEBIAN_FRONTEND=noninteractive
ensure_pkgs gnupg ca-certificates
CONDA="$HOME/miniconda3"
PY="$CONDA/envs/mafagent/bin/python"
py_exact() {
  [ -x "$1" ] && "$1" -c 'import sys; raise SystemExit(0 if sys.version.split()[0]==sys.argv[1] else 1)' "$2"
}
if py_exact "$PY" "$PY_VER"; then
  echo "已完成: $($PY -V) $PY"
else
  if [ ! -x "$CONDA/bin/conda" ]; then
    case "$(uname -m)" in
      x86_64) conda_arch="x86_64" ;;
      aarch64) conda_arch="aarch64" ;;
      *)
        echo "不可用: 不支持的架构 $(uname -m)" >&2
        exit 1
        ;;
    esac
    installer="$(mktemp --suffix=.sh)"
    echo "下载 Miniconda3 https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-${conda_arch}.sh"
    curl -fsSL -o "$installer" "https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-${conda_arch}.sh"
    echo "安装 Miniconda3 到 $CONDA"
    bash "$installer" -b -p "$CONDA"
    rm -f "$installer"
  fi
  if [ ! -x "$CONDA/bin/conda" ]; then
    echo "不可用: 找不到 $CONDA/bin/conda" >&2
    exit 1
  fi
  echo "可用: $CONDA/bin/conda"
  "$CONDA/bin/conda" tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main
  "$CONDA/bin/conda" tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r
  if [ -e "$CONDA/envs/mafagent" ]; then
    echo "未完成: $CONDA/envs/mafagent 不是 Python $PY_VER，删除后继续"
    "$CONDA/bin/conda" env remove -y -n mafagent
  fi
  echo "创建 conda 环境 mafagent，Python $PY_VER"
  "$CONDA/bin/conda" create -y -n mafagent "python=$PY_VER"
fi
if ! py_exact "$PY" "$PY_VER"; then
  echo "不可用: $PY 不是 Python $PY_VER" >&2
  exit 1
fi
echo "可用: $($PY -V) $PY"
if [ "$NO_OFFICE" -eq 1 ]; then
  echo "跳过 OnlyOffice"
else
echo "安装字体"
echo ttf-mscorefonts-installer msttcorefonts/accepted-mscorefonts-eula select true | sudo debconf-set-selections
ensure_pkgs ttf-mscorefonts-installer

if pkg_installed onlyoffice-documentserver && listen "$OO_PORT"; then
  echo "已完成: OnlyOffice $OO_PORT"
else
  echo "安装 OnlyOffice,端口 $OO_PORT"
  printf 'onlyoffice-documentserver onlyoffice/ds-port select %s\n' "$OO_PORT" | sudo debconf-set-selections
  printf 'onlyoffice-documentserver onlyoffice/jwt-enabled boolean true\n' | sudo debconf-set-selections
  printf 'onlyoffice-documentserver onlyoffice/jwt-secret password %s\n' "$OO_JWT" | sudo debconf-set-selections
  if [ ! -f /usr/share/keyrings/onlyoffice.gpg ]; then
    mkdir -p -m 700 "$HOME/.gnupg"
    curl -fsSL https://download.onlyoffice.com/GPG-KEY-ONLYOFFICE | gpg --no-default-keyring --keyring gnupg-ring:/tmp/onlyoffice.gpg --import
    chmod 644 /tmp/onlyoffice.gpg
    sudo chown root:root /tmp/onlyoffice.gpg
    sudo mv /tmp/onlyoffice.gpg /usr/share/keyrings/onlyoffice.gpg
  fi
  echo 'deb [signed-by=/usr/share/keyrings/onlyoffice.gpg] https://download.onlyoffice.com/repo/debian squeeze main' | sudo tee /etc/apt/sources.list.d/onlyoffice.list >/dev/null
  sudo apt-get update
  echo "安装 OnlyOffice。它的 sudo 配置含有已废弃的 requiretty，安装时会删掉这一行"
  sudo bash -c 'set -e
restore_sudo() {
  if [ -f /etc/sudoers.d/documentserver ]; then
    sed -i "/requiretty/d" /etc/sudoers.d/documentserver
  fi
  if [ -x /usr/bin/sudo.real ]; then
    mv /usr/bin/sudo.real /usr/bin/sudo
  fi
}
trap restore_sudo EXIT
if [ ! -e /usr/bin/sudo.real ]; then
  mv /usr/bin/sudo /usr/bin/sudo.real
fi
cat > /usr/bin/sudo << "EOF"
#!/bin/sh
if [ -f /etc/sudoers.d/documentserver ] && grep -q requiretty /etc/sudoers.d/documentserver; then
  sed -i "/requiretty/d" /etc/sudoers.d/documentserver
fi
exec /usr/bin/sudo.real "$@"
EOF
chmod 755 /usr/bin/sudo
apt-get install -y onlyoffice-documentserver
dpkg --configure -a
'
  sudo dpkg-reconfigure -f noninteractive onlyoffice-documentserver
  echo "放宽 OnlyOffice 下载限制"
  sudo python3 - <<'PY'
import json
from pathlib import Path
p = Path("/etc/onlyoffice/documentserver/local.json")
d = json.loads(p.read_text(encoding="utf-8"))
d.setdefault("FileConverter", {}).setdefault("converter", {})["maxDownloadBytes"] = 1073741824
p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
  sudo sed -i 's/client_max_body_size 100m;/client_max_body_size 1g;/' /etc/onlyoffice/documentserver/nginx/includes/ds-common.conf
  echo "重启 OnlyOffice"
  sudo systemctl restart ds-docservice ds-converter nginx
  echo "等待 2 秒后检查 OnlyOffice 端口 $OO_PORT"
  sleep 2
fi
listen "$OO_PORT"
fi

if [ "$NO_RDP" -eq 1 ]; then
  echo "跳过远程桌面"
elif pkg_installed guacd && pkg_installed libguac-client-rdp0t64 && listen "$GUACD_PORT"; then
  echo "已完成: guacd $GUACD_PORT"
else
  echo "安装 guacd,端口 $GUACD_PORT"
  printf '#!/bin/sh\nexit 101\n' | sudo tee /usr/sbin/policy-rc.d >/dev/null
  sudo chmod 755 /usr/sbin/policy-rc.d
  sudo apt-get install -y guacd libguac-client-rdp0t64
  sudo rm -f /usr/sbin/policy-rc.d
  printf 'LISTEN_ADDRESS=127.0.0.1\nLISTEN_PORT=%s\nDAEMON_ARGS=\n' "$GUACD_PORT" | sudo tee /etc/default/guacd >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl enable --now guacd
  echo "重启 guacd"
  sudo systemctl restart guacd
  echo "等待 1 秒后检查 guacd 端口 $GUACD_PORT"
  sleep 1
  listen "$GUACD_PORT"
fi

if ! command -v ollama >/dev/null; then
  echo "安装 Ollama"
  curl -fsSL https://ollama.com/install.sh | sudo sh
fi
if ! command -v ollama >/dev/null; then
  echo "不可用: 找不到 ollama" >&2
  exit 1
fi
echo "可用: $(command -v ollama)"
sudo mkdir -p /etc/systemd/system/ollama.service.d
printf '[Service]\nEnvironment="OLLAMA_HOST=127.0.0.1:%s"\n' "$OLLAMA_PORT" | sudo tee /etc/systemd/system/ollama.service.d/override.conf >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now ollama
if ! listen "$OLLAMA_PORT"; then
  echo "重启 Ollama,端口 $OLLAMA_PORT"
  sudo systemctl restart ollama
  echo "等待 2 秒后检查 Ollama 端口 $OLLAMA_PORT"
  sleep 2
fi
listen "$OLLAMA_PORT"
export OLLAMA_HOST="127.0.0.1:${OLLAMA_PORT}"
if ollama show "$EMBED_MODEL" >/dev/null; then
  echo "已完成: 嵌入模型 $EMBED_MODEL"
else
  echo "拉取嵌入模型 $EMBED_MODEL"
  ollama pull "$EMBED_MODEL"
fi
if ! ollama show "$EMBED_MODEL" >/dev/null; then
  echo "不可用: 嵌入模型 $EMBED_MODEL" >&2
  exit 1
fi
echo "可用: 嵌入模型 $EMBED_MODEL"

if [ "$NO_DSH" -eq 0 ] || [ "$NO_RDP" -eq 0 ]; then
node_ok() {
  command -v node >/dev/null && node -e 'const p=process.versions.node.split(".").map(Number); process.exit((p[0]===22&&p[1]>=19)||p[0]>=24?0:1)'
}
if [ ! -s "$HOME/.nvm/nvm.sh" ]; then
  echo "安装 nvm"
  curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
fi
if [ ! -s "$HOME/.nvm/nvm.sh" ]; then
  echo "不可用: 找不到 $HOME/.nvm/nvm.sh" >&2
  exit 1
fi
echo "可用: $HOME/.nvm/nvm.sh"
export NVM_DIR="$HOME/.nvm"
. "$NVM_DIR/nvm.sh"
if node_ok; then
  echo "已完成: Node $(node -v)"
else
  echo "安装 Node.js 22"
  nvm install 22
  nvm alias default 22
fi
if ! node_ok; then
  echo "不可用: Node.js 需要 ^22.19.0 或 >=24,当前 $(node -v 2>/dev/null || echo 未安装)" >&2
  exit 1
fi
echo "可用: Node $(node -v)"
if ! corepack pnpm -v 2>/dev/null | grep -qx '11.7.0'; then
  echo "启用 corepack 并安装 pnpm 11.7.0"
  corepack enable
  corepack prepare pnpm@11.7.0 --activate
fi
if ! corepack pnpm -v 2>/dev/null | grep -qx '11.7.0'; then
  echo "不可用: pnpm 不是 11.7.0,当前 $(corepack pnpm -v 2>/dev/null || echo 未安装)" >&2
  exit 1
fi
echo "可用: pnpm $(corepack pnpm -v)"
fi
if [ "$NO_DSH" -eq 1 ]; then
  echo "跳过 DeepSeek 构建"
else
  if [ -f "$ROOT/apps/deepseek/apps/cli/lib/bin.js" ] && [ -f "$ROOT/apps/deepseek/apps/web/dist/index.html" ]; then
    echo "已完成: DeepSeek Harness 构建"
  else
    echo "安装并构建 DeepSeek Harness: $ROOT/apps/deepseek"
    (
      cd "$ROOT/apps/deepseek"
      corepack pnpm install
      corepack pnpm run build
    )
  fi
  if [ ! -f "$ROOT/apps/deepseek/apps/cli/lib/bin.js" ]; then
    echo "不可用: 缺少 $ROOT/apps/deepseek/apps/cli/lib/bin.js" >&2
    exit 1
  fi
  if [ ! -f "$ROOT/apps/deepseek/apps/web/dist/index.html" ]; then
    echo "不可用: 缺少 $ROOT/apps/deepseek/apps/web/dist/index.html" >&2
    exit 1
  fi
  echo "可用: $ROOT/apps/deepseek/apps/cli/lib/bin.js"
  echo "可用: $ROOT/apps/deepseek/apps/web/dist/index.html"
fi
if [ "$NO_RDP" -eq 1 ]; then
  echo "跳过 guacamole-lite 依赖"
else
  if [ -d "$ROOT/vendor/guacamole/guacamole-lite/node_modules/ws" ]; then
    echo "已完成: guacamole-lite 依赖"
  else
    echo "安装 guacamole-lite 依赖"
    (cd "$ROOT/vendor/guacamole/guacamole-lite" && npm install --omit=dev)
  fi
  if [ ! -d "$ROOT/vendor/guacamole/guacamole-lite/node_modules/ws" ]; then
    echo "不可用: 缺少 guacamole-lite 的 ws" >&2
    exit 1
  fi
  echo "可用: $ROOT/vendor/guacamole/guacamole-lite/node_modules/ws"
fi

py_ok() {
  [ -x "$ROOT/.venv/bin/python" ] && "$ROOT/.venv/bin/python" -c 'import fastapi,uvicorn,httpx,jwt,yaml,openai,pydantic,dotenv,websockets,numpy,multipart,cryptography,opentelemetry'
}
if py_ok; then
  echo "已完成: Python 依赖"
else
  echo "创建虚拟环境并安装 Python 依赖: $ROOT/.venv"
  "$PY" -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/pip" install --upgrade pip
  "$ROOT/.venv/bin/pip" install \
    "fastapi>=0.104.0,<1" \
    "uvicorn[standard]>=0.24.0,<1" \
    "httpx[socks]==0.28.1" \
    "PyJWT[crypto]==2.13.0" \
    "pyyaml==6.0.3" \
    "openai==2.24.0" \
    "pydantic==2.13.4" \
    "python-dotenv==1.2.2" \
    "websockets==15.0.1" \
    numpy \
    "python-multipart>=0.0.9,<1" \
    "cryptography==48.0.1" \
    "typing-extensions>=4.15" \
    "opentelemetry-api>=1.39"
fi
if ! py_ok; then
  echo "不可用: Python 依赖导入失败" >&2
  exit 1
fi
echo "可用: Python 依赖"
if [ "$NO_HERMES" -eq 1 ]; then
  echo "跳过 Hermes"
else
  if "$ROOT/.venv/bin/python" -c 'import hermes_cli'; then
    echo "已完成: Hermes"
  else
    echo "安装 Hermes: $ROOT/apps/hermes-agent"
    "$ROOT/.venv/bin/pip" install -e "$ROOT/apps/hermes-agent"
  fi
  if ! "$ROOT/.venv/bin/python" -c 'import hermes_cli'; then
    echo "不可用: 无法导入 hermes_cli" >&2
    exit 1
  fi
  echo "可用: hermes_cli"
fi
if "$ROOT/.venv/bin/python" -c 'import openai; raise SystemExit(0 if openai.__version__=="2.46.0" else 1)'; then
  echo "已完成: openai 2.46.0"
else
  echo "安装 openai 2.46.0"
  "$ROOT/.venv/bin/pip" install "openai==2.46.0"
fi
if ! "$ROOT/.venv/bin/python" -c 'import openai; raise SystemExit(0 if openai.__version__=="2.46.0" else 1)'; then
  echo "不可用: openai 不是 2.46.0" >&2
  exit 1
fi
echo "可用: openai 2.46.0"

HERMES_HOME="$HOME/.hermes"
mkdir -p "$HERMES_HOME"
WORK="$ROOT/data/workspace"
mkdir -p "$WORK"
if [ -f "$HERMES_HOME/config.yaml" ] && grep -F "cwd: $WORK" "$HERMES_HOME/config.yaml" >/dev/null && [ -f "$HERMES_HOME/env" ] && grep -F "HERMES_HOME=$HERMES_HOME" "$HERMES_HOME/env" >/dev/null && grep -F "HERMES_WEBUI_PYTHON=$ROOT/.venv/bin/python" "$HERMES_HOME/env" >/dev/null; then
  echo "已完成: Hermes 配置"
else
  echo "写入 Hermes 配置: $HERMES_HOME/config.yaml"
  python3 - "$HERMES_HOME/config.yaml" "$WORK" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
work = sys.argv[2]
text = "terminal:\n  backend: local\n  cwd: %s\n" % work
path.write_text(text, encoding="utf-8")
PY
  {
    printf 'export HERMES_HOME=%s\nexport HERMES_WEBUI_PYTHON=%s\n' "$HERMES_HOME" "$ROOT/.venv/bin/python"
    if [ -s "$HOME/.nvm/nvm.sh" ]; then
      printf 'export NVM_DIR=%s\n. %s/nvm.sh\n' "$HOME/.nvm" "$HOME/.nvm"
    fi
  } > "$HERMES_HOME/env"
fi
if ! grep -F "cwd: $WORK" "$HERMES_HOME/config.yaml" >/dev/null || ! grep -F "HERMES_WEBUI_PYTHON=$ROOT/.venv/bin/python" "$HERMES_HOME/env" >/dev/null; then
  echo "不可用: Hermes 配置" >&2
  exit 1
fi
echo "可用: $HERMES_HOME/config.yaml"

if [ "$NO_COMFYUI" -eq 1 ]; then
  echo "跳过 ComfyUI 依赖"
elif [ -n "$COMFYUI_DIR" ]; then
  if [ ! -f "$ROOT/apps/comfyui/main.py" ]; then
    echo "不可用: 缺少 $ROOT/apps/comfyui/main.py" >&2
    exit 1
  fi
  echo "已完成: 使用已有 ComfyUI $COMFYUI_DIR"
  echo "可用: $ROOT/apps/comfyui/main.py"
else
  COMFY="$ROOT/apps/comfyui"
  comfy_ok() {
    [ -x "$COMFY/.venv/bin/python" ] && "$COMFY/.venv/bin/python" -c 'import torch; raise SystemExit(0 if torch.cuda.is_available() else 1)'
  }
  if comfy_ok; then
    echo "已完成: ComfyUI CUDA"
  else
    echo "安装 ComfyUI 依赖: $COMFY"
    "$PY" -m venv "$COMFY/.venv"
    "$COMFY/.venv/bin/pip" install --upgrade pip
    "$COMFY/.venv/bin/pip" install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
    "$COMFY/.venv/bin/pip" install -r "$COMFY/requirements.txt"
  fi
  if ! comfy_ok; then
    echo "不可用: ComfyUI 不能使用 CUDA" >&2
    exit 1
  fi
  echo "可用: ComfyUI CUDA"
fi

echo "安装完成。代码目录 $ROOT ，Linux 端口 web=$WEB_PORT onlyoffice=$OO_PORT ollama=$OLLAMA_PORT comfyui=$COMFY_PORT hermes=$HERMES_PORT deepseek=$DEEPSEEK_PORT remote=$REMOTE_PORT guacd=$GUACD_PORT"
echo "启动 $ROOT/server.py"
cd "$ROOT"
set -a
. "$HERMES_HOME/env"
set +a
exec "$ROOT/.venv/bin/python" "$ROOT/server.py"
