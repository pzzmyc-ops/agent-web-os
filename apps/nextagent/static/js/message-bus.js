const _listeners = {};
const _wildcard = [];

export function on(type, fn) {
  if (type === "*") {
    _wildcard.push(fn);
    return function off() {
      const i = _wildcard.indexOf(fn);
      if (i >= 0) _wildcard.splice(i, 1);
    };
  }
  if (!_listeners[type]) _listeners[type] = [];
  _listeners[type].push(fn);
  return function off() {
    const arr = _listeners[type];
    if (!arr) return;
    const i = arr.indexOf(fn);
    if (i >= 0) arr.splice(i, 1);
  };
}

export function once(type, fn) {
  const off = on(type, function (msg) {
    off();
    fn(msg);
  });
  return off;
}

export function off(type, fn) {
  const arr = _listeners[type];
  if (!arr) return;
  const i = arr.indexOf(fn);
  if (i >= 0) arr.splice(i, 1);
}

export function hasListeners(type) {
  return !!(_listeners[type] && _listeners[type].length) || _wildcard.length > 0;
}

export function emit(type, msg) {
  const arr = _listeners[type];
  if (arr) {
    const snapshot = arr.slice();
    for (let i = 0; i < snapshot.length; i++) {
      try {
        snapshot[i](msg);
      } catch (e) {
        console.error("[message-bus] listener error for type=" + type, e);
      }
    }
  }
  if (_wildcard.length) {
    const wsnap = _wildcard.slice();
    for (let i = 0; i < wsnap.length; i++) {
      try {
        wsnap[i](msg);
      } catch (e) {
        console.error("[message-bus] wildcard listener error", e);
      }
    }
  }
}

export const messageBus = { on, once, off, emit, hasListeners };
