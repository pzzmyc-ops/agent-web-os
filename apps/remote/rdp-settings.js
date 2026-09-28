function boolField(value, name) {
  if (value === true || value === "true") return true;
  if (value === false || value === "false") return false;
  throw new Error(name + "无效");
}

function applyClientSettings(body, settings) {
  const quality = String(body.quality || "");
  if (quality !== "low" && quality !== "balanced" && quality !== "high") {
    throw new Error("连接质量无效");
  }
  const depth = Number(body.colorDepth);
  if (depth !== 8 && depth !== 16 && depth !== 24) {
    throw new Error("色深无效");
  }
  settings["color-depth"] = depth;
  settings["enable-wallpaper"] = boolField(body.wallpaper, "壁纸");
  settings["enable-theming"] = boolField(body.theming, "主题");
  settings["enable-font-smoothing"] = boolField(body.fontSmoothing, "字体平滑");
  settings["enable-desktop-composition"] = boolField(body.composition, "桌面合成");
  settings["disable-audio"] = !boolField(body.audio, "声音");
  settings["force-lossless"] = boolField(body.lossless, "无损");
}

module.exports = { applyClientSettings };
