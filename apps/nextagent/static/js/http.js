export function naFetch(input, init) {
  var next = {};
  if (init !== undefined && init !== null) {
    Object.keys(init).forEach(function (k) {
      next[k] = init[k];
    });
  }
  if (next.credentials === undefined) {
    next.credentials = "same-origin";
  }
  return fetch(input, next);
}
