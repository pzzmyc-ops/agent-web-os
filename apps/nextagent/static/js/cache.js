import { clearCatalogForUser } from "./asset-catalog.js";

const PREFIX = "nextagent_";

export function saveConversationList(userId, list) {
  try {
    localStorage.setItem(PREFIX + "conv_list_" + userId, JSON.stringify(list));
  } catch (e) {}
}

export function loadConversationList(userId) {
  try {
    const raw = localStorage.getItem(PREFIX + "conv_list_" + userId);
    return raw ? JSON.parse(raw) : null;
  } catch (e) { return null; }
}

export function saveFolderList(userId, list) {
  try {
    localStorage.setItem(PREFIX + "folder_list_" + userId, JSON.stringify(list || []));
  } catch (e) {}
}

export function loadFolderList(userId) {
  try {
    const raw = localStorage.getItem(PREFIX + "folder_list_" + userId);
    return raw ? JSON.parse(raw) : [];
  } catch (e) { return []; }
}

export function saveCurrentConvId(userId, convId) {
  try {
    localStorage.setItem(PREFIX + "current_conv_" + userId, convId || "");
  } catch (e) {}
}

export function loadCurrentConvId(userId) {
  try {
    return localStorage.getItem(PREFIX + "current_conv_" + userId) || "";
  } catch (e) { return ""; }
}

export function clearUserCache(userId) {
  const prefix = PREFIX;
  const keysToRemove = [];
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (key && key.startsWith(prefix) && key.includes(userId)) {
      keysToRemove.push(key);
    }
  }
  keysToRemove.forEach(function (k) { localStorage.removeItem(k); });
  clearCatalogForUser(userId);
}
