import { state, activeConv, getConvState } from "./state.js";
import { sendWsMsg } from "./ws.js";

var _detailOverlay = null;

function _panel() {
  return document.getElementById("queuePanel");
}

function _preview(text) {
  var oneLine = String(text || "").replace(/\s+/g, " ").trim();
  if (oneLine.length <= 80) return oneLine;
  return oneLine.slice(0, 80) + "...";
}

function _kindLabel(item) {
  if (item.kind === "report") return "终端完成通知";
  return "排队消息";
}

export function applyQueueState(threadId, items) {
  var cs = getConvState(threadId);
  cs.queueItems = Array.isArray(items) ? items.slice() : [];
  if (String(threadId) === String(state.currentConversationId)) {
    renderQueuePanel(cs);
  }
}

export function queueDepth(threadId) {
  var cs = threadId ? getConvState(threadId) : activeConv();
  if (!cs || !cs.queueItems) return 0;
  return cs.queueItems.length;
}

export function removeQueueItem(threadId, itemId) {
  sendWsMsg("queue_remove", { threadId: String(threadId || ""), itemId: String(itemId || "") });
}

export function renderQueuePanel(cs) {
  var panel = _panel();
  if (!panel) return;
  var items = cs && cs.queueItems ? cs.queueItems : [];
  if (!items.length) {
    panel.hidden = true;
    panel.innerHTML = "";
    return;
  }
  panel.hidden = false;
  panel.innerHTML = "";
  var head = document.createElement("div");
  head.className = "queue-panel-head";
  head.textContent = "排队中 " + items.length + " 条，当前回合结束后一并发出";
  panel.appendChild(head);
  var list = document.createElement("div");
  list.className = "queue-panel-list";
  for (var i = 0; i < items.length; i++) {
    list.appendChild(_renderItem(cs, items[i], i + 1));
  }
  panel.appendChild(list);
}

function _renderItem(cs, item, position) {
  var row = document.createElement("div");
  row.className = "queue-item" + (item.kind === "report" ? " queue-item-report" : "");
  row.dataset.itemId = item.id;

  var order = document.createElement("span");
  order.className = "queue-item-order";
  order.textContent = String(position);
  row.appendChild(order);

  var body = document.createElement("button");
  body.type = "button";
  body.className = "queue-item-body";
  body.title = "查看完整内容";
  var text = document.createElement("span");
  text.className = "queue-item-text";
  text.textContent = _preview(item.content) || "(无文字)";
  body.appendChild(text);
  if (item.attachments && item.attachments.length) {
    var att = document.createElement("span");
    att.className = "queue-item-att";
    att.textContent = "附件 " + item.attachments.length;
    body.appendChild(att);
  }
  if (item.kind === "report") {
    var tag = document.createElement("span");
    tag.className = "queue-item-tag";
    tag.textContent = _kindLabel(item);
    body.appendChild(tag);
  }
  body.onclick = function (e) {
    e.stopPropagation();
    openQueueItemDetail(cs, item);
  };
  row.appendChild(body);

  var del = document.createElement("button");
  del.type = "button";
  del.className = "queue-item-remove";
  del.title = "从队列中去除这条";
  del.innerHTML = "&times;";
  del.onclick = function (e) {
    e.stopPropagation();
    removeQueueItem(cs.id, item.id);
  };
  row.appendChild(del);
  return row;
}

export function closeQueueItemDetail() {
  if (_detailOverlay && _detailOverlay.parentNode) {
    _detailOverlay.parentNode.removeChild(_detailOverlay);
  }
  _detailOverlay = null;
}

export function openQueueItemDetail(cs, item) {
  closeQueueItemDetail();
  var overlay = document.createElement("div");
  overlay.className = "queue-detail-overlay";
  overlay.onclick = function (e) {
    if (e.target === overlay) closeQueueItemDetail();
  };
  var card = document.createElement("div");
  card.className = "queue-detail-card";

  var head = document.createElement("div");
  head.className = "queue-detail-head";
  var title = document.createElement("span");
  title.textContent = _kindLabel(item) + (item.model ? "  模型 " + item.model : "");
  head.appendChild(title);
  var close = document.createElement("button");
  close.type = "button";
  close.className = "queue-detail-close";
  close.innerHTML = "&times;";
  close.onclick = closeQueueItemDetail;
  head.appendChild(close);
  card.appendChild(head);

  var pre = document.createElement("pre");
  pre.className = "queue-detail-content";
  pre.textContent = item.content || "(无文字)";
  card.appendChild(pre);

  if (item.attachments && item.attachments.length) {
    var attList = document.createElement("ul");
    attList.className = "queue-detail-atts";
    for (var i = 0; i < item.attachments.length; i++) {
      var li = document.createElement("li");
      li.textContent = item.attachments[i];
      attList.appendChild(li);
    }
    card.appendChild(attList);
  }

  var foot = document.createElement("div");
  foot.className = "queue-detail-foot";
  var del = document.createElement("button");
  del.type = "button";
  del.className = "queue-detail-remove";
  del.textContent = "从队列中去除";
  del.onclick = function () {
    removeQueueItem(cs.id, item.id);
    closeQueueItemDetail();
  };
  foot.appendChild(del);
  card.appendChild(foot);

  overlay.appendChild(card);
  document.body.appendChild(overlay);
  _detailOverlay = overlay;
}
