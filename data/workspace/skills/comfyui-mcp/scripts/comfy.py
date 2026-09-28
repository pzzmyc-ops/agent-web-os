#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
comfy.py - 用 ComfyUI 的 REST API 驱动本机 ComfyUI（等价于 comfy-mcp 的底层行为）。

仅依赖标准库 urllib，Python 3.8+ 可跑。
默认连接 http://127.0.0.1:8188，可用 --base 覆盖。

子命令:
  server-info                确认 ComfyUI 是否在跑，看 GPU/device
  object-info [名称]         看节点(或全部)输入规格; 传 CheckpointLoaderSimple 等可列模型
  submit <workflow.json>     提交 API 格式工作流; --wait 阻塞到完成
  status <prompt_id>         轮询单个任务状态
  wait <prompt_id>           阻塞到任务完成
  fetch <prompt_id> --out D  把任务产物下载到目录, 打印本地路径
  upload <image>             上传输入图, 返回 {name,subfolder,type}
  queue                      看运行/排队任务数
  validate <workflow.json>   预检工作流是否可提交
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

DEFAULT_BASE = "http://127.0.0.1:8188"


def http_request(base, method, path, data=None, headers=None, timeout=60):
    url = base.rstrip("/") + path
    body = None
    hdr = {"Accept": "application/json"}
    if headers:
        hdr.update(headers)
    if data is not None:
        if isinstance(data, (dict, list)):
            body = json.dumps(data).encode("utf-8")
            hdr["Content-Type"] = "application/json"
        else:
            body = data
    req = urllib.request.Request(url, data=body, headers=hdr, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw.decode("utf-8"))
        except Exception:
            return e.code, raw.decode("utf-8", "replace")
    except urllib.error.URLError as e:
        raise SystemExit("连接失败(Connection refused?): %s\n请先 `comfy launch` 启动服务。" % e)
    try:
        return 200, json.loads(raw.decode("utf-8"))
    except Exception:
        return 200, raw


def get(base, path, timeout=60):
    return http_request(base, "GET", path, timeout=timeout)


def post(base, path, data=None, headers=None, timeout=120):
    return http_request(base, "POST", path, data=data, headers=headers, timeout=timeout)


def cmd_server_info(base):
    code, data = get(base, "/system_stats")
    if code != 200:
        print("ComfyUI 未响应(code=%s)。" % code)
        return 1
    print(json.dumps({k: data.get(k) for k in ("system", "devices") if k in data},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_object_info(base, name):
    if name:
        _, obj = get(base, "/object_info")
        data = {name: obj.get(name)} if name in obj else {}
    else:
        data = get(base, "/object_info")[1]
    if name and not data:
        print("没有节点: %s (试试不传名称看全部)" % name)
        return 1
    print(json.dumps(data, ensure_ascii=False, indent=2)[:6000])
    return 0


def load_workflow(path):
    with open(path, "r", encoding="utf-8") as f:
        wf = json.load(f)
    if "prompt" in wf:
        wf = wf["prompt"]
    return wf


def cmd_submit(base, path, wait, timeout):
    wf = load_workflow(path)
    client_id = str(uuid.uuid4())
    code, data = post(base, "/prompt", {"prompt": wf, "client_id": client_id})
    if code != 200:
        print("submit 失败(code=%s): %s" % (code, json.dumps(data, ensure_ascii=False)[:2000]))
        if isinstance(data, dict) and data.get("node_errors"):
            print("节点错误: ", json.dumps(data["node_errors"], ensure_ascii=False)[:2000])
        return 1
    pid = data.get("prompt_id")
    print("prompt_id = %s" % pid)
    if wait:
        return cmd_wait(base, pid, timeout)
    print("异步提交: 用 `comfy.py wait %s` 或 `comfy.py fetch %s --out <dir>`" % (pid, pid))
    return 0


def cmd_status(base, prompt_id):
    code, data = get(base, "/history/%s" % prompt_id)
    if code != 200 or not data:
        _, q = get(base, "/queue")
        running = [t for t in q.get("queue_running", []) if t and t[1] == prompt_id]
        if running:
            print("运行中: %s" % prompt_id)
        else:
            print("未找到/未开始: %s" % prompt_id)
        return 1
    rec = data.get(prompt_id, {})
    print(json.dumps(rec.get("status", {}), ensure_ascii=False, indent=2))
    return 0


def cmd_wait(base, prompt_id, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        code, data = get(base, "/history/%s" % prompt_id)
        if code == 200 and prompt_id in data:
            status = data[prompt_id].get("status", {})
            print("完成: %s" % json.dumps(status, ensure_ascii=False))
            if status.get("status_str") == "error":
                print("任务出错!")
                return 1
            return 0
        time.sleep(2)
    print("超时(timeout=%ss)，任务可能仍在跑。可用 `status` 查询。" % timeout)
    return 1


def cmd_fetch(base, prompt_id, out):
    code, data = get(base, "/history/%s" % prompt_id)
    if code != 200 or prompt_id not in data:
        print("尚未完成(或不存在): %s，先 `wait` 再取。" % prompt_id)
        return 1
    rec = data[prompt_id]
    if rec.get("status", {}).get("status_str") == "error":
        print("任务出错，无法取产物。")
        return 1
    os.makedirs(out, exist_ok=True)
    saved = []
    for node_id, node_out in rec.get("outputs", {}).items():
        for k in ("images", "gifs", "videos", "audio"):
            for item in node_out.get(k, []):
                fname = item.get("filename")
                sf = item.get("subfolder", "")
                typ = item.get("type", "output")
                if not fname:
                    continue
                path = "/view?%s" % urllib.parse.urlencode(
                    {"filename": fname, "subfolder": sf, "type": typ})
                c2, raw = http_request(base, "GET", path, timeout=300)
                if c2 != 200:
                    print("下载失败 %s (code=%s)" % (fname, c2))
                    continue
                target = os.path.join(out, os.path.basename(fname))
                with open(target, "wb") as f:
                    f.write(raw)
                saved.append(target)
                print("saved: %s" % target)
    if not saved:
        print("没有找到产物。")
        return 1
    print("\n用 render_media 展示这些路径(直接照抄, 勿手改):")
    for s in saved:
        print("  " + s)
    return 0


def cmd_upload(base, image):
    boundary = "----comfy" + uuid.uuid4().hex
    with open(image, "rb") as f:
        content = f.read()
    body = b""
    body += ("--%s\r\nContent-Disposition: form-data; name=\"image\"; filename=\"%s\"\r\n"
             "Content-Type: application/octet-stream\r\n\r\n" % (boundary, os.path.basename(image))).encode()
    body += content + b"\r\n"
    body += ("--%s--\r\n" % boundary).encode()
    code, data = post(base, "/upload/image", body,
                      headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    if code != 200:
        print("upload 失败(code=%s): %s" % (code, data))
        return 1
    print(json.dumps(data, ensure_ascii=False))
    print("把 name/subfolder/type 填进 workflow 的 LoadImage 节点.")
    return 0


def cmd_queue(base):
    code, data = get(base, "/queue")
    if code != 200:
        print("查询失败(code=%s)" % code)
        return 1
    print("queue_running=%d  queue_pending=%d" % (len(data.get("queue_running", [])),
                                                  len(data.get("queue_pending", []))))
    return 0


def cmd_validate(base, path):
    wf = load_workflow(path)
    _, obj = get(base, "/object_info")
    missing = []
    for node_id, node_def in wf.items():
        cls = node_def.get("class_type")
        if cls not in obj:
            missing.append((node_id, cls))
    if missing:
        print("以下节点类在当前 ComfyUI 中不存在(可能缺自定义节点):")
        for node_id, cls in missing:
            print("  id=%s class_type=%s" % (node_id, cls))
        return 1
    print("通过预检: 所有节点类都在 object_info 中。")
    return 0


def build_parser():
    p = argparse.ArgumentParser(description="ComfyUI REST API 客户端")
    p.add_argument("--base", default=DEFAULT_BASE, help="ComfyUI 地址, 默认 %s" % DEFAULT_BASE)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn):
        s = sub.add_parser(name, help=fn.__doc__)
        s.set_defaults(fn=fn)
        return s

    add("server-info", cmd_server_info)
    oi = add("object-info", cmd_object_info)
    oi.add_argument("name", nargs="?", default=None)

    subm = add("submit", cmd_submit)
    subm.add_argument("path")
    subm.add_argument("--wait", action="store_true")
    subm.add_argument("--timeout", type=int, default=900)

    st = add("status", cmd_status)
    st.add_argument("prompt_id")

    wt = add("wait", cmd_wait)
    wt.add_argument("prompt_id")
    wt.add_argument("--timeout", type=int, default=900)

    fc = add("fetch", cmd_fetch)
    fc.add_argument("prompt_id")
    fc.add_argument("--out", required=True)

    up = add("upload", cmd_upload)
    up.add_argument("image")

    add("queue", cmd_queue)

    va = add("validate", cmd_validate)
    va.add_argument("path")
    return p


def main():
    args = build_parser().parse_args()
    return args.fn(args.base, **{k: v for k, v in vars(args).items() if k not in ("cmd", "fn", "base")})


if __name__ == "__main__":
    sys.exit(main())
