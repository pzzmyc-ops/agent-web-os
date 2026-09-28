"""role_manage 工具 —— agent 创建/维护角色卡,并把角色拉进当前会话。

角色卡 = name + avatar(1-2 字)+ system_prompt,存在 sessions/roles.json(由 store 管)。
角色被加入某个会话后,那个会话的下一轮走多 agent 群聊(stream_adapter.run_handoff_turn):
每张角色卡变成一个 handoff 参与者,instructions 就是它的 system_prompt,可用工具与主
agent 相同,「做完交回协调者」那句协议由框架侧统一注入,不用写进角色卡。

两个语义要点在工具描述、工具结果、技能正文里各讲一遍 —— 模型会跳读工具描述,但一定
会读工具结果:

  1. system_prompt 的前 200 字会被截去当「交接到这个成员」的工具说明
     (agent_assembly.build_role_agent 的 description),所以第一句必须说清这个角色
     负责什么,否则协调者不知道什么时候该找他。
  2. join 生效于**下一轮**:本轮的参与者名单在收到用户消息时就定了(ws_chat 取
     store.get_active_roles),角色不会当场开口。
"""
from __future__ import annotations

import json

from ._context import current_turn
from ._registry import registry

#: 前端角色卡的头像框是 maxlength=2(static/js/roles.js 的 #efAvatar),超了会显示不全。
_AVATAR_MAX = 2

#: 在场角色数超过这个值就在结果里提醒一句 —— 群聊每轮要按发言顺序串行跑,人多既慢又容易跑偏。
_CROWDED = 4

DESCRIPTION = "管理角色库"

ROLE_MANAGE_DOC = """管理角色库:新建/编辑/删除角色卡,把角色拉进当前对话组成多角色群聊。

角色 = 一张卡(name + avatar 1-2字 + system_prompt)。加入对话后它成为一个独立参与者,
用自己的 system_prompt 当设定、和你一样能调工具,由你(总协调者)按需要交接给它。

**system_prompt 的第一句要说清「这个角色负责什么」** —— 前 200 字会成为其他成员
「交接给它」时看到的说明。写法、模板、反例见 load_skill name=role-create。

## role_manage(action="create", name=..., system_prompt=..., avatar=..., join=true)
建一张角色卡。join=true 顺带把它加入当前对话(从下一轮起发言)。
## role_manage(action="list") —— 角色库里有哪些角色、当前对话里在场哪些
## role_manage(action="join" / "leave", role="角色名或id") —— 加入/移出当前对话
## role_manage(action="update", role=..., name=/avatar=/system_prompt=) —— 只改传了的字段
## role_manage(action="delete", role=...) —— 删卡(同时从所有对话里移除,不可撤销)"""


def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg}, ensure_ascii=False)


def _store():
    """全进程唯一的 Store。

    延迟导入 web_server:sessions 目录的路径表达式只在它那里写了一次,在这里另建一个
    Store 就得把那段路径抄第二份(config.py 的注释吐槽过这种抄两份各错一处的事)。
    """
    from ..web_server import store

    return store


def _brief(r: dict) -> dict:
    return {
        "id": r.get("id", ""),
        "name": r.get("name", ""),
        "avatar": r.get("avatar", ""),
        "system_prompt": r.get("system_prompt", ""),
    }


def _find(st, ref: str) -> tuple[dict | None, str]:
    """按 id 或角色名找一张卡。返回 (角色, 错误说明) —— 找不到时错误说明里带现有角色名。"""
    ref = str(ref or "").strip()
    if not ref:
        return None, 'role 不能为空 —— 填角色名或角色 id,先 action="list" 看有哪些'
    roles = st.list_roles()
    for r in roles:
        if r.get("id") == ref:
            return r, ""
    hits = [r for r in roles if (r.get("name") or "").strip().lower() == ref.lower()]
    if len(hits) == 1:
        return hits[0], ""
    if len(hits) > 1:
        ids = ", ".join(f"{r.get('name')}(id={r.get('id')})" for r in hits)
        return None, f"有多个角色都叫「{ref}」,改用 id 指定:{ids}"
    names = ", ".join((r.get("name") or "?") for r in roles) or "(角色库是空的)"
    return None, f"没有叫「{ref}」的角色。现有角色:{names}"


def _join(st, role_id: str) -> tuple[bool, str]:
    """把角色加进当前会话。返回 (是否成功, 给模型看的说明)。"""
    ctx = current_turn()
    if ctx is None:
        return False, "当前不在一轮对话里,没法确定加入哪个会话 —— 角色卡已入库,让用户从角色库拖进对话即可"
    st.add_active_role(ctx.thread_id, role_id)
    note = "已加入当前对话,从**下一轮**开始发言(本轮的参与者名单在收到用户这条消息时就定了)。"
    n = len(st.get_active_roles(ctx.thread_id))
    if n >= _CROWDED:
        note += f" 当前在场 {n} 个角色 —— 群聊按发言顺序串行跑,人多每轮更慢也更容易跑偏,建议只留必要的几个。"
    return True, note


async def _notify() -> None:
    """让正在看这个页面的浏览器立刻刷新角色库侧栏。没有在线连接就静默跳过。"""
    try:
        from ..live_conns import broadcast

        await broadcast({"type": "roles_changed", "data": {}})
    except Exception:  # noqa: BLE001 — 推送只是补实时性,失败不能影响这次操作的结果
        pass


@registry.register(
    toolset="roles",
    name="role_manage",
    summary="管理角色库:新建/编辑/删除角色卡,把角色拉进当前对话",
    description=DESCRIPTION,
    doc=ROLE_MANAGE_DOC,
)
async def role_manage(
    action: str,
    role: str = "",
    name: str = "",
    avatar: str = "",
    system_prompt: str = "",
    join: bool = False,
) -> str:
    """管理角色库。角色加入对话后成为独立参与者,用自己的 system_prompt 当设定。

    Args:
        action: create / list / join / leave / update / delete
        role: 要操作的角色,填角色名或 id。join/leave/update/delete 必填
        name: 角色名(卡片上显示的名字)。create 必填
        avatar: 头像,1-2 个字(如 "产品")。留空则前端用角色名首字
        system_prompt: 角色设定。create 必填,第一句先说清它负责什么
        join: create 时顺带把新角色加入当前对话(从下一轮起发言)
    """
    action = str(action or "").strip().lower()
    st = _store()

    if action == "list":
        ctx = current_turn()
        active_ids = (
            [r.get("id") for r in st.get_active_roles(ctx.thread_id)] if ctx is not None else []
        )
        return json.dumps(
            {
                "roles": [{**_brief(r), "in_current_chat": r.get("id") in active_ids} for r in st.list_roles()],
                "in_current_chat_count": len(active_ids),
            },
            ensure_ascii=False,
        )

    if action == "create":
        nm = str(name or "").strip()
        if not nm:
            return _err('name 不能为空 —— 卡片上显示的角色名,例 name="产品经理"')
        sp = str(system_prompt or "").strip()
        if not sp:
            return _err(
                "system_prompt 不能为空 —— 它是这个角色的全部设定(它是谁、负责什么、怎么说话)。"
                "第一句先说清负责什么:前 200 字就是其他成员「交接给它」的依据。"
                "写法见 load_skill name=role-create"
            )
        dup = [r for r in st.list_roles() if (r.get("name") or "").strip() == nm]
        if dup:
            return _err(
                f'已经有角色叫「{nm}」(id={dup[0].get("id")})—— 用 action="update" 改它,或换个名字'
            )
        rid = st.create_role(nm, str(avatar or "").strip()[:_AVATAR_MAX], sp)
        payload: dict = {"ok": True, "action": "created", "role": _brief(st.get_role(rid) or {})}
        if join:
            ok, note = _join(st, rid)
            payload["joined"] = ok
            payload["note"] = note
        else:
            payload["note"] = '角色卡已入库,但还没进任何对话 —— 要它参与就再调 action="join"'
        await _notify()
        return json.dumps(payload, ensure_ascii=False)

    if action == "join":
        r, err = _find(st, role)
        if err:
            return _err(err)
        assert r is not None
        ctx = current_turn()
        if ctx is not None and any(x.get("id") == r["id"] for x in st.get_active_roles(ctx.thread_id)):
            return json.dumps(
                {"ok": True, "action": "join", "role": _brief(r), "note": "它本来就在这个对话里了"},
                ensure_ascii=False,
            )
        ok, note = _join(st, r["id"])
        if not ok:
            return _err(note)
        await _notify()
        return json.dumps({"ok": True, "action": "joined", "role": _brief(r), "note": note}, ensure_ascii=False)

    if action == "leave":
        r, err = _find(st, role)
        if err:
            return _err(err)
        assert r is not None
        ctx = current_turn()
        if ctx is None:
            return _err("当前不在一轮对话里,没法确定从哪个会话移出")
        st.remove_active_role(ctx.thread_id, r["id"])
        await _notify()
        return json.dumps(
            {
                "ok": True,
                "action": "left",
                "role": _brief(r),
                "note": "已从当前对话移出(角色卡还在角色库里,随时可以再 join)。下一轮起它不再发言。",
            },
            ensure_ascii=False,
        )

    if action == "update":
        r, err = _find(st, role)
        if err:
            return _err(err)
        assert r is not None
        nm = str(name or "").strip() or r.get("name", "")
        av = str(avatar or "").strip()[:_AVATAR_MAX] or r.get("avatar", "")
        sp = str(system_prompt or "").strip() or r.get("system_prompt", "")
        if not st.update_role(r["id"], nm, av, sp):
            return _err(f'角色 {r.get("name")} 不见了(可能刚被删)—— 重新 action="list" 看看')
        await _notify()
        return json.dumps(
            {
                "ok": True,
                "action": "updated",
                "role": _brief(st.get_role(r["id"]) or {}),
                "note": "没传的字段保持原样(所以清空不了字段,只能改成新内容)。改动对下一轮生效。",
            },
            ensure_ascii=False,
        )

    if action == "delete":
        r, err = _find(st, role)
        if err:
            return _err(err)
        assert r is not None
        st.delete_role(r["id"])
        await _notify()
        return json.dumps(
            {
                "ok": True,
                "action": "deleted",
                "role": _brief(r),
                "note": "角色卡已删除,同时从所有对话的在场名单里移除了。不可撤销。",
            },
            ensure_ascii=False,
        )

    return _err(f"未知 action: {action!r}。可用: create, list, join, leave, update, delete")
