"""单账号身份(自动登录,无密码)。

第二步聚焦对话本身,鉴权走最简:恒为一个固定单用户。前端的登录门(/auth/me)
恒返回该用户,WS 不校验 token。后续要加单密码门很容易(参照 nextagent 做法)。
"""
from __future__ import annotations

SINGLE_USER_ID = "0" * 31 + "1"
SINGLE_USERNAME = "user"


def current_user() -> dict:
    return {"userId": SINGLE_USER_ID, "userName": SINGLE_USERNAME}
