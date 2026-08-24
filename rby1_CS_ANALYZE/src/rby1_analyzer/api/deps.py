from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status


def bearer_token(request: Request, authorization: Annotated[str | None, Header()] = None) -> str:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    token = authorization[7:]
    if not token or not request.app.state.runtime.authority.accepts(token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid bearer token")
    return token


Bearer = Annotated[str, Depends(bearer_token)]
