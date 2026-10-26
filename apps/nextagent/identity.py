"""单账号身份(自动登录,无密码)。

身份恒为一个固定单用户,/auth/me 恒返回该用户。登录门在整站层(项目根 webauth.py):
密码换 Cookie,所有自有页面、接口和 WebSocket 握手都要带它,这里不再重复校验。
"""
from __future__ import annotations

SINGLE_USER_ID = "0" * 31 + "1"
SINGLE_USERNAME = "user"


def current_user() -> dict:
    return {"userId": SINGLE_USER_ID, "userName": SINGLE_USERNAME}
