var _registeredWindows = new Map();

export function registerWindowType(type, opener) {
  var key = String(type || "").trim();
  if (!key) throw new Error("window type is required");
  if (typeof opener !== "function") throw new Error("window opener must be a function");
  _registeredWindows.set(key, opener);
}

export function unregisterWindowType(type) {
  _registeredWindows.delete(String(type || "").trim());
}

export function hasWindowType(type) {
  return _registeredWindows.has(String(type || "").trim());
}

export function openRegisteredWindow(type, payload) {
  var key = String(type || "").trim();
  var opener = _registeredWindows.get(key);
  if (!opener) throw new Error("window type not registered: " + key);
  return opener(payload || {});
}
