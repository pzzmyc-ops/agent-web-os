import gzip
import html
import re
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlparse

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, Response

router = APIRouter(prefix="/api/browser")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_LIMIT = 20_000_000
_HOOK = """
var ORIGIN = "__ORIGIN__";
function via(url) {
  var abs = new URL(url, ORIGIN);
  if (abs.origin === location.origin && abs.pathname.indexOf("/api/browser/") !== 0) {
    abs = new URL(ORIGIN + abs.pathname + abs.search + abs.hash);
  }
  if (abs.origin !== ORIGIN) return abs.href;
  return location.origin + "/api/browser/page?url=" + encodeURIComponent(abs.href);
}
var _fetch = window.fetch;
window.fetch = function (input, init) {
  if (typeof input === "string") input = via(input);
  return _fetch.call(this, input, init);
};
var _open = XMLHttpRequest.prototype.open;
XMLHttpRequest.prototype.open = function (method, url) {
  var args = Array.prototype.slice.call(arguments);
  if (typeof url === "string") args[1] = via(url);
  return _open.apply(this, args);
};
document.addEventListener("click", function (event) {
  var node = event.target;
  while (node && node.tagName !== "A") node = node.parentElement;
  if (!node || !node.href) return;
  event.preventDefault();
  event.stopPropagation();
  event.stopImmediatePropagation();
  var kind = (node.target === "_blank" || event.ctrlKey || event.metaKey) ? "browser-tab" : "browser-go";
  parent.postMessage({ type: kind, url: node.href }, "*");
}, true);
document.addEventListener("auxclick", function (event) {
  if (event.button !== 1) return;
  var node = event.target;
  while (node && node.tagName !== "A") node = node.parentElement;
  if (!node || !node.href) return;
  event.preventDefault();
  event.stopPropagation();
  event.stopImmediatePropagation();
  parent.postMessage({ type: "browser-tab", url: node.href }, "*");
}, true);
document.addEventListener("submit", function (event) {
  var form = event.target;
  if (!form || form.tagName !== "FORM") return;
  var method = (form.getAttribute("method") || "get").toLowerCase();
  if (method !== "get") throw new Error("只支持用地址打开的表单");
  event.preventDefault();
  event.stopPropagation();
  var action = new URL(form.getAttribute("action") || ".", document.baseURI);
  new FormData(form).forEach(function (value, key) {
    action.searchParams.append(key, String(value));
  });
  parent.postMessage({ type: "browser-go", url: action.href }, "*");
}, true);
window.open = function (url) {
  if (!url) return null;
  parent.postMessage({ type: "browser-tab", url: new URL(String(url), document.baseURI).href }, "*");
  return null;
};
"""


def check_http_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(status_code=400, detail=f"只打开 http 或 https: {url}")
    return url


def _charset(header: str) -> str:
    for part in header.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset" and value.strip():
            return value.strip().strip('"')
    return "utf-8"


_SKIP = ("#", "javascript:", "data:", "mailto:", "blob:", "about:")
_ATTR = re.compile(
    r"""(?P<attr>\b(?:src|href|action)\s*=\s*)(?P<q>["'])(?P<val>.*?)(?P=q)""",
    re.I | re.S,
)
_CSP = re.compile(
    r"""<meta\b[^>]*http-equiv\s*=\s*["']Content-Security-Policy["'][^>]*>""",
    re.I,
)


def _absolute(value: str, base_url: str) -> str:
    raw = value.strip()
    if not raw or raw.lower().startswith(_SKIP):
        return value
    return urljoin(base_url, raw)


def _rewrite(page: str, base_url: str) -> str:
    page = _CSP.sub("", page)

    def repl(match):
        return match.group("attr") + match.group("q") + _absolute(match.group("val"), base_url) + match.group("q")

    return _ATTR.sub(repl, page)


def _inject(page: str, base_url: str) -> str:
    origin = "{0}://{1}".format(urlparse(base_url).scheme, urlparse(base_url).netloc)
    page = _rewrite(page, base_url)
    tag = '<base href="' + html.escape(base_url, quote=True) + '"><script>' + _HOOK.replace("__ORIGIN__", origin) + "</script>"
    head = page.lower().find("<head>")
    if head >= 0:
        at = head + len("<head>")
        return page[:at] + tag + page[at:]
    return tag + page


def _read(resp) -> tuple[bytes, str, str]:
    raw = resp.read(_LIMIT + 1)
    if len(raw) > _LIMIT:
        raise HTTPException(status_code=400, detail="页面太大")
    encoding = (resp.headers.get("Content-Encoding") or "").lower()
    if encoding == "gzip":
        raw = gzip.decompress(raw)
    final = check_http_url(resp.geturl())
    return raw, resp.headers.get("Content-Type", ""), final


@router.get("/page")
def browser_page(url: str = Query()):
    target = check_http_url(url)
    request = urllib.request.Request(target, headers={"User-Agent": _UA, "Accept": "text/html,application/xhtml+xml,*/*"})
    try:
        try:
            resp_cm = urllib.request.urlopen(request, timeout=20)
        except urllib.error.HTTPError as exc:
            resp_cm = exc
        with resp_cm as resp:
            raw, content_type, final = _read(resp)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc
    sample = raw.lstrip()[:32].lower()
    html_page = "html" in content_type.lower() or sample.startswith(b"<!doctype html") or sample.startswith(b"<html")
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    }
    if not html_page:
        return Response(content=raw, media_type=content_type or "application/octet-stream", headers=headers)
    text = raw.decode(_charset(content_type))
    return HTMLResponse(_inject(text, final), headers=headers)
