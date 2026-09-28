---
name: dingtalk-send-group
display_name: 钉钉消息（群 / 单人）
description: 用户要求通过钉钉发出、发到群里、或私聊通知某个人（如「通知钟雯艳」）时使用。底层调用钉钉 CLI (dws)。
executor: handler
entry_kind: standard
params: "group?, content?, to?"
tags: [dingtalk, chat, notify]
user_selectable: true
metadata: { timeout: 60 }
version: "1.3.0"
---

# 钉钉消息（群发 + 单发）

用户在对话里说任务已经完成、通过钉钉发出、发到群里、或想通知某个人时，把指定内容发到钉钉。

底层是钉钉 CLI：`dws`。支持两种方式：

- **群发**：`dws chat +send-to-group`
- **按姓名单发（私聊）**：`dws chat +dm`

直接调用 dws CLI，不要用 terminal 拼 HTTP，不要走钉钉开放接口。

## 判断发到哪

### 发给群
- 当前对话已绑定钉钉群：发到这个群，不必再问群名。
- 用户说了群名：发到那个群。
- 既没有绑定也没有群名：先问用户发到哪个群，不要自己挑。

### 发给单个用户（私聊）
- 用户点名通知某个人（例如「通知钟雯艳」，这种是**通知某个具体的人**）时，用单发：
  `dws chat +dm --to <姓名> --content <内容> --yes`
- 底层会按姓名在通讯录唯一解析出用户（openDingTalkId），姓名匹配到多人时会列候选让选。
- 单发不需要群名，也不需要绑定群。这是和群发最大的不同。

> 之前版本只写了群发、误判“无法发单人”；实际 dws CLI 支持 +dm 单发。遇到「通知某个人」优先用单发，不要因为没绑定群就卡住。

## 怎么发

### 群发
- `dingtalk_send_text(text="要说的话")`（已绑定群）
- `dingtalk_send_text(text="要说的话", group="群名")`（指定群）
- `dingtalk_send_file(path="文件真实绝对路径，如 D:/mafagent/data/workspace/a.pdf")`（指定群时加 group）

> **⚠️ @ 某人（要让 @ 变蓝可点击）：不要用 `dingtalk_send_text` / `+send-to-group`**，它只会把 `@姓名` 当纯文本发出去，@ 不会变蓝。
> 必须走 `dws chat +messages-send`：正文用 `<@openDingTalkId>` 占位符，并用 `--at-open-dingtalk-ids` 传该成员的 openDingTalkId。
> ```bash
> dws chat +messages-send --as user --group <openConversationId> >   --text "内容 <@openDingTalkId>" >   --at-open-dingtalk-ids <openDingTalkId> --yes
> ```
> - 群 openConversationId：`dws chat +chat-list-all --format json` 查，或取发送回执里的 `conversation_id`；也可用 `--chat-query "群名"` 让 CLI 唯一解析群。
> - @ 所有人用 `--at-all`；@ 多人：`--text "a <@id1> b <@id2>" --at-open-dingtalk-ids id1,id2`。

### 单发（私聊）
- `dws chat +dm --to <姓名> --content <内容> --yes`（`--format json` 可看结果）
- 或者调用 handler 的 `send_to_user(姓名, 内容)`

## 发完汇报

发出去之后，用一句话告诉用户发到哪个群 / 发给了谁。

## 排障：发送报 AUTH_PERMISSION_DENIED / FORBIDDEN（组织切换）

发送文件或消息时如果返回：
```
[AUTH_PERMISSION_DENIED] Permission denied   (server_error_code: FORBIDDEN, server_key: im)
```
而 `dws doctor` 显示「已登录、网络可达」都正常，**最常见原因是 dws CLI 当前激活的组织不是企业的那个组织**（比如切到了个人/其他组织）。此时即使账号是同一个，也没有目标群/文件上传资源的权限。

处理办法——切回企业组织：
```bash
dws profile list                      # 查看当前可用的组织
dws profile switch "重庆灏瀚网络科技有限公司:何昊阳"   # 切到企业组织（组织名:用户名）
dws doctor                            # 再确认登录态
```
切换后再重试发送即可。`dws profile switch` 的组织名/用户名要按 `dws profile list` 的实际结果来填，示例为「重庆灏瀚网络科技有限公司:何昊阳」。

> 判断依据：文字消息能发、唯独 `dingtalk_send_file`（或某些群/资源）持续报 FORBIDDEN，且 `dws doctor` 全绿 → 优先怀疑「组织没切回企业」。这属于 CLI 级现状，不是代码 bug。
