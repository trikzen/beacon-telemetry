"""Admin API for managing device keys over HTTP.

Needed on hosts where you have no shell to run manage_keys.py (Render's free tier, for
example). It is OFF unless BEACON_ADMIN_TOKEN is set to a long secret (20+ characters),
and every request must send it in an X-Admin-Token header. These routes are left out of
the public /docs page on purpose.
"""

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

import auth
import db

ADMIN_TOKEN, TOKEN_TOO_SHORT = auth.admin_token_setting()

router = APIRouter(prefix="/admin", include_in_schema=False)


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    if ADMIN_TOKEN is None:
        raise HTTPException(status_code=404, detail="Not Found")     # as if it did not exist
    if not x_admin_token or not secrets.compare_digest(
        x_admin_token.encode("utf-8"), ADMIN_TOKEN.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Invalid or missing admin token")


class NewKey(BaseModel):
    device_id: str


@router.post("/keys", status_code=201, dependencies=[Depends(require_admin)])
def create_key(body: NewKey):
    try:
        issued = auth.issue_key(body.device_id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))
    issued["note"] = "Copy this key now. It cannot be shown again."
    return issued


@router.get("/keys", dependencies=[Depends(require_admin)])
def list_keys():
    return db.list_keys()


@router.delete("/keys/{key_id}", dependencies=[Depends(require_admin)])
def revoke_key(key_id: int):
    revoked = db.revoke_key(key_id)
    if revoked is None:
        raise HTTPException(status_code=404, detail=f"No key with id {key_id}")
    return revoked
