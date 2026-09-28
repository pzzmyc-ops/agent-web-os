---
name: feishu-send-group
display_name: 飞书消息（群 / 单人）
description: 用户要求通过飞书发出、发到飞书群、或私聊通知某个人时使用。底层调用飞书 CLI (lark-cli)。
executor: handler
entry_kind: standard
params: "group?, content?, to?"
tags: [feishu, lark, chat, notify]
user_selectable: true
metadata: { timeout: 60 }
version: "1.0.0"
---

# 飞书消息（群发 + 单发）

用户在对话里说任务已经完成、通过飞书发出、发到飞书群、或想通知某个人时，把指定内容发到飞书。

底层默认是飞书 CLI：`lark-cli`。飞书 webhook 对话不要走 CLI。

- **群发**：`lark-cli im +messages-send --as bot --chat-id <群ID>`
- **按姓名单发（私聊）**：先用 `lark-cli contact +search-user --query <姓名>` 解析出 open_id，再用 `lark-cli im +messages-send --as user --user-id <open_id>`
- **webhook 马上发**：`feishu_send_text(text="要说的话")`，不要填 group，不要发文件，不要查群消息
- **webhook 纯程序/固定程序定时发群**：`schedule` 用 `mode=exec`，对开头提示词里的 webhook 地址发 HTTP。不要用 `mode=program`（只写本对话卡片，群里收不到），不要用 `mode=agent`（会叫醒对话）。不要说发不到外部群。

普通飞书对话不要用 terminal 拼 HTTP。webhook 马上发由 `feishu_send_text` 发。

## 判断发到哪

### 发给群
- 当前是飞书 webhook 对话：发到这个对话绑定的地址，不必再问群名。
- 当前对话已绑定飞书群：发到这个群，不必再问群名。
- 用户说了群名：发到那个群。
- 既没有绑定也没有群名：先问用户发到哪个群，不要自己挑。

### 发给单个用户（私聊）
- 用户点名通知某个人（例如「通知张三」）时，用单发。
- 底层按姓名在通讯录唯一解析出用户。姓名匹配到多人时会报错，不要自己挑。
- 单发不需要群名，也不需要绑定群。

## 怎么发

### 群发
- `feishu_send_text(text="要说的话")`（已绑定群或飞书 webhook 对话）
- `feishu_send_text(text="要说的话", group="群名")`（指定群）
- `feishu_send_file(path="文件真实绝对路径，如 D:/mafagent/data/workspace/a.pdf")`（指定群时加 group；webhook 对话不能发文件）

### webhook 纯程序定时发群
用户说纯程序、固定程序、不要叫醒、每隔 N 分钟发到群：
- `schedule action=create mode=exec repeat=interval every_minutes=N command=...`
- command 用开头提示词里的完整 webhook 地址，把正文换掉：

```
python -c "import json,urllib.request; urllib.request.urlopen(urllib.request.Request('WEBHOOK地址', data=json.dumps({'msg_type':'text','content':{'text':'ok'}}).encode('utf-8'), headers={'Content-Type':'application/json; charset=utf-8'}, method='POST'), timeout=30)"
```

### 单发（私聊）
- 调用 handler 的 `send_to_user(姓名, 内容)`
- 或者：`lark-cli contact +search-user --query <姓名> --format json` 拿到唯一 open_id 后，再 `lark-cli im +messages-send --as user --user-id <open_id> --markdown <内容> --format json`

## 发完汇报

发出去之后，用一句话告诉用户发到哪个群 / 发给了谁。
