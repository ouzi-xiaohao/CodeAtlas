import hashlib
import hmac
import time

from fastapi import Header, HTTPException

from app.config import get_settings
from app.models import UserContext


async def current_user(
    x_user_id: str = Header(default="local-user"),
    x_teams: str = Header(default="default"),
    x_repositories: str = Header(default=""),
) -> UserContext:
    return UserContext(
        user_id=x_user_id,
        teams={value.strip() for value in x_teams.split(",") if value.strip()},
        repositories={value.strip() for value in x_repositories.split(",") if value.strip()},
    )


def create_confirmation(action: str, resource: str, expires_in: int = 300) -> str:
    expires = int(time.time()) + expires_in
    payload = f"{action}:{resource}:{expires}"
    signature = hmac.new(
        get_settings().write_confirmation_secret.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return f"{expires}.{signature}"


def verify_confirmation(token: str, action: str, resource: str) -> None:
    try:
        expires_text, signature = token.split(".", 1)
        expires = int(expires_text)
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid confirmation token") from exc
    if expires < int(time.time()):
        raise HTTPException(status_code=400, detail="Confirmation token expired")
    payload = f"{action}:{resource}:{expires}"
    expected = hmac.new(
        get_settings().write_confirmation_secret.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=403, detail="Confirmation token does not match operation")
