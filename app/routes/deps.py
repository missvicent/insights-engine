"""Shared FastAPI dependencies for the routes layer.

`get_current_user` is a stub until real authentication lands. It reads the
`x-user-id` header and returns it verbatim. Routes call this via `Depends(...)`
so swapping in a real auth implementation is a one-file change.
"""

from fastapi import Header, HTTPException


def get_current_user(x_user_id: str | None = Header(default=None)) -> str:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="missing x-user-id header")
    return x_user_id
