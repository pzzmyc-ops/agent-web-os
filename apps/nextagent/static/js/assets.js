import { loadTree, reset as resetAssetStore, handleAssetEvent, initAssetWindows, ASSET_ROOT } from "./modules/asset-system/index.js";
import { getEffectiveThreadId } from "./modules/asset-system/asset-store.js";

var _assetThreadId = "";

export function initAssets() {
  initAssetWindows();
}

export function connectAssetWs(threadId) {
  if (!threadId) return;
  _assetThreadId = threadId;
  loadTree(threadId, ASSET_ROOT());
}

export function syncAssetsThread(threadId) {
  var tid = String(threadId || _assetThreadId || getEffectiveThreadId() || "").trim();
  if (!tid) return;
  if (_assetThreadId !== tid) {
    connectAssetWs(tid);
  }
}

export function clearAssets() {
  resetAssetStore();
  _assetThreadId = "";
}

export { handleAssetEvent };
