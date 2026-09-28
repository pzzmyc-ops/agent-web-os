export function logAssetUrl(stage, detail) {
  var d = detail || {};
  if (d.mediaId !== undefined || d.mime !== undefined || d.filename !== undefined || d.srcKind !== undefined) {
    console.log(
      "[asset_diag] " +
        stage +
        " t=" +
        performance.now().toFixed(0) +
        " media_id=" +
        (d.mediaId || "") +
        " mime=" +
        (d.mime || "") +
        " filename=" +
        (d.filename || "") +
        " src_kind=" +
        (d.srcKind || "") +
        " src_len=" +
        (d.srcLen || 0) +
        " has_data_url=" +
        !!d.hasDataUrl +
        (d.extra ? " " + d.extra : "")
    );
    return;
  }
  console.log(
    "[asset_diag] " +
      stage +
      " t=" +
      performance.now().toFixed(0) +
      " attr_amp=" +
      !!d.attrHasAmp +
      " prop_amp=" +
      !!d.propHasAmp +
      " attr_na=" +
      !!d.attrHasNaToken +
      " prop_na=" +
      !!d.propHasNaToken +
      " uri=" +
      (d.uri || "") +
      " attr_len=" +
      (d.attrLen || 0) +
      " prop_len=" +
      (d.propLen || 0) +
      (d.extra ? " " + d.extra : "")
  );
}

export function snapshotImgSrc(img, stage) {
  if (!img) return;
  var attr = img.getAttribute("src") || "";
  var prop = img.src || "";
  var uri = "";
  var m = prop.match(/[?&]path=([^&]+)/) || attr.match(/[?&]path=([^&]+)/);
  if (m) {
    try {
      uri = decodeURIComponent(m[1]);
    } catch (e) {
      uri = m[1];
    }
  }
  logAssetUrl(stage, {
    attrHasAmp: attr.indexOf("&amp;") >= 0,
    propHasAmp: prop.indexOf("&amp;") >= 0,
    uri: uri,
    attrLen: attr.length,
    propLen: prop.length,
  });
}

export function probeAssetImg(img, stage) {
  if (!globalThis.__NA_DEV__) return;
  if (!img) return;
  var url = img.src || img.getAttribute("src") || "";
  if (!url) {
    logAssetUrl(stage + "_probe_skip", { extra: "empty_url" });
    return;
  }
  fetch(url, { method: "GET", credentials: "same-origin" })
    .then(function (resp) {
      var ct = resp.headers.get("content-type") || "";
      logAssetUrl(stage + "_probe", {
        uri: assetUriFromUrl(url),
        extra: "status=" + resp.status + " ct=" + ct + " ok=" + resp.ok,
      });
    })
    .catch(function (err) {
      logAssetUrl(stage + "_probe_err", {
        uri: assetUriFromUrl(url),
        extra: String(err && err.message ? err.message : err),
      });
    });
}

function assetUriFromUrl(url) {
  var m = String(url || "").match(/[?&]path=([^&]+)/);
  if (!m) return "";
  try {
    return decodeURIComponent(m[1]);
  } catch (e) {
    return m[1];
  }
}

export function logAssetHtml(stage, html) {
  var s = String(html || "");
  console.log(
    "[asset_diag] " +
      stage +
      " t=" +
      performance.now().toFixed(0) +
      " len=" +
      s.length +
      " has_amp=" +
      (s.indexOf("&amp;") >= 0) +
      " snip=" +
      s.slice(0, 200)
  );
}

