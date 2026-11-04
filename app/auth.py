"""Generic OIDC authorization-code + PKCE adapter for the externally owned IAM.

Requires an interoperable OAuth/OIDC provider contract: authorization/token URLs,
public client ID, redirect URI, JWT audience and JWKS. Never accepts token URL
or redirect URI from request bodies.
"""
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from fastapi import APIRouter

from app.core import AUTH_MODE, fail

router = APIRouter(prefix="/auth", tags=["auth"])


def _config():
    return {
        "mode": AUTH_MODE,
        "authorization_url": os.getenv("OIDC_AUTHORIZATION_URL", ""),
        "token_url": os.getenv("OIDC_TOKEN_URL", ""),
        "client_id": os.getenv("OIDC_CLIENT_ID", ""),
        "redirect_uri": os.getenv("OIDC_REDIRECT_URI", ""),
        "scope": os.getenv("OIDC_SCOPE", "openid profile"),
        "logout_url": os.getenv("OIDC_LOGOUT_URL", ""),
    }


@router.get("/config")
def auth_config():
    cfg = _config()
    if AUTH_MODE != "oidc":
        return {"mode": "demo"}
    for key in ("authorization_url", "token_url", "client_id", "redirect_uri"):
        if not cfg[key]:
            fail(503, "OIDC_NOT_CONFIGURED", "Внешний сервис входа ещё не настроен.")
    return {k:v for k,v in cfg.items() if k != "token_url"}


@router.post("/token")
def exchange_auth_code(body: dict):
    if AUTH_MODE != "oidc":
        fail(403, "AUTH_MODE", "Обмен кодов доступен только для внешней авторизации.")
    cfg = _config()
    for key in ("token_url", "client_id", "redirect_uri"):
        if not cfg[key]:
            fail(503, "OIDC_NOT_CONFIGURED", "Нет контракта внешней авторизации.")
    code = body.get("code")
    verifier = body.get("code_verifier")
    if (not isinstance(code,str) or not 1<=len(code)<=4096 or
        not isinstance(verifier,str) or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}",verifier)):
        fail(422, "INVALID_OIDC_CODE", "Некорректный код входа или PKCE-проверка.")
    parsed = urlparse(cfg["token_url"])
    if parsed.scheme != "https" or not parsed.hostname:
        fail(503, "OIDC_CONFIG_ERROR", "Адрес сервера токенов должен использовать HTTPS.")
    payload=urlencode({
        "grant_type":"authorization_code", "code":code,
        "redirect_uri":cfg["redirect_uri"],
        "client_id":cfg["client_id"], "code_verifier":verifier,
    }).encode()
    request=Request(cfg["token_url"],data=payload,headers={
        "Content-Type":"application/x-www-form-urlencoded",
        "Accept":"application/json"},method="POST")
    try:
        with urlopen(request,timeout=12) as response:
            if response.status!=200:
                fail(502,"OIDC_EXCHANGE_FAILED","Сервис входа не выдал токен.")
            data=json.loads(response.read(262144).decode())
    except (HTTPError,URLError,ValueError,UnicodeDecodeError) as error:
        fail(502,"OIDC_EXCHANGE_FAILED",
             "Не удалось завершить вход через внешний сервис.")
    token=data.get("access_token")
    if not isinstance(token,str) or not 1<=len(token)<=16000:
        fail(502,"OIDC_EXCHANGE_FAILED","Провайдер не вернул подходящий токен доступа.")
    return {"access_token":token,"token_type":"Bearer",
            "expires_in":max(0,min(int(data.get("expires_in",3600)),86400))}
