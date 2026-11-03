#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Unified entry: AX first, vision fallback.

    python act.py tree   --hwnd 12345 [--query 搜索]
    python act.py click  --hwnd 12345 --name 发送 [--route auto|ax|vision]
    python act.py type   --hwnd 12345 --name 搜索框 --text abc [--route auto|ax|vision]

Prints one JSON verdict line: {"route": "ax"|"vision", "effect": ..., ...}
Exit codes: 0 = action taken, 3 = target not found, 4 = AX refused (no fallback allowed).
"""
import argparse
import json
import os
import subprocess
import sys

import ax
import vision

HERE = os.path.dirname(os.path.abspath(__file__))


def ax_tree(hwnd, query=""):
    return ax.request({"op": "tree", "hwnd": hwnd, "max": 400, "query": query})


def pick(elements, name):
    for e in elements:
        if (e.get("name") or "") == name:
            return e
    for e in elements:
        if name and name in (e.get("name") or ""):
            return e
    return None


def ax_find(hwnd, name):
    """UIA-native FindAll by exact name — much faster than walking the tree."""
    return ax.request({"op": "find", "hwnd": hwnd, "name": name})


def ax_click(hwnd, name):
    # Fast path first: exact-name lookup through UIA itself.
    fast = ax_find(hwnd, name)
    els = (fast.get("elements") or []) if fast.get("ok") else []
    if not els:
        res = ax_tree(hwnd, name)
        if not res.get("ok") or not res.get("elements"):
            return None, "ax_tree_empty"
        els = res["elements"]
    el = pick(els, name)
    if not el:
        return None, "ax_no_match"
    pats = el.get("patterns") or ""
    for op, need in (("invoke", "Invoke"), ("select", "SelectionItem"), ("toggle", "Toggle")):
        if need in pats:
            r = ax.request({"op": op, "hwnd": hwnd, "index": el["index"]})
            if r.get("ok"):
                return {"route": "ax", "effect": r.get("effect"), "via": r.get("via"),
                        "element": el["name"], "role": el["role"]}, None
            return None, r.get("code") or "ax_action_failed"
    return None, "ax_no_actionable_pattern"


def ax_type(hwnd, name, text):
    fast = ax_find(hwnd, name)
    els = (fast.get("elements") or []) if fast.get("ok") else []
    if not els:
        res = ax_tree(hwnd, name)
        if not res.get("ok") or not res.get("elements"):
            return None, "ax_tree_empty"
        els = res["elements"]
    el = pick(els, name)
    if not el:
        return None, "ax_no_match"
    if "Value" not in (el.get("patterns") or ""):
        return None, "ax_no_value_pattern"
    r = ax.request({"op": "setvalue", "hwnd": hwnd, "index": el["index"], "text": text})
    if r.get("ok"):
        return {"route": "ax", "effect": r.get("effect"), "via": r.get("via"),
                "element": el["name"], "readback": r.get("readback")}, None
    return None, r.get("code") or "ax_action_failed"


def vision_find(hwnd, name):
    img, rect, source = vision.grab_window(hwnd)
    els = vision.ocr_elements(img, name)
    if not els:
        return None
    e = els[0]
    sx, sy = vision.to_screen(e, rect, img.size)
    return {"text": e["text"], "conf": e["conf"], "sx": sx, "sy": sy, "source": source}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["tree", "click", "type"])
    ap.add_argument("--hwnd", type=int, required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--text", default="")
    ap.add_argument("--query", default="")
    ap.add_argument("--route", default="auto", choices=["auto", "ax", "vision"])
    args = ap.parse_args()

    if args.op == "tree":
        res = ax_tree(args.hwnd, args.query)
        els = (res.get("elements") or []) if res.get("ok") else []
        actionable = [e for e in els if e.get("patterns")]
        # A window that only exposes itself (no patterns) is NOT usable via AX:
        # e.g. Chromium chat windows return a single Window row and nothing else.
        if actionable:
            print(f"[act] route=ax total={res.get('total')} actionable={len(actionable)} took={res.get('took_ms')}ms")
            for e in actionable:
                print(f"  #{e['index']:<3} {e['role']:<14} {e['name'][:48]!r:<50} [{e.get('patterns') or '-'}]")
            return 0
        img, rect, source = vision.grab_window(args.hwnd)
        vis = vision.ocr_elements(img, args.query)
        reason = res.get("code") or ("no_actionable_ax" if els else "ax_tree_empty")
        print(f"[act] route=vision source={source} (ax elements={len(els)}, reason={reason})")
        for e in vis[:40]:
            sx, sy = vision.to_screen(e, rect, img.size)
            print(f"  #{e['index']:<3} ocr  {e['text'][:44]!r:<46} conf={e['conf']:<5} screen=({sx},{sy})")
        return 0 if vis else 3
        return 0 if vis else 3

    if not args.name:
        print(json.dumps({"error": "--name is required"}, ensure_ascii=False))
        return 2

    if args.route in ("auto", "ax"):
        if args.op == "type":
            verdict, why = ax_type(args.hwnd, args.name, args.text)
        else:
            verdict, why = ax_click(args.hwnd, args.name)
        if verdict:
            print(json.dumps(verdict, ensure_ascii=False))
            return 0
        print(json.dumps({
            "route": "ax",
            "effect": "refused",
            "code": why,
            "hint": ("AX 不可用。不要用 OCR 猜坐标点击。请改用："
                     "screen.py --window <关键词> --grid 截图读出逻辑坐标，"
                     "再 mouse.py click <x> <y> --hwnd <hwnd>（会自动置前并校验命中）"),
        }, ensure_ascii=False))
        return 4

    print(json.dumps({
        "error": "--route vision 已停用：OCR 定位不足以稳定点击，请用 screen.py --grid + mouse.py --hwnd",
    }, ensure_ascii=False))
    return 4


if __name__ == "__main__":
    sys.exit(main())
