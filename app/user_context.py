"""Per-request user identity, carried out-of-band from the model.

Credentials must never be tool parameters: the model fills those, so a token placed
there would be model-controlled. Tools read this ContextVar instead.
"""

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class UserContext:
    object_id: str | None = None
    tenant_id: str | None = None
    name: str | None = None
    # Token for downstream authorization (Search ACLs, OPA). None until SSO/OBO is wired.
    token: str | None = None


_current_user: ContextVar[UserContext | None] = ContextVar("current_user", default=None)


def set_user_context(context: UserContext):
    return _current_user.set(context)


def get_user_context() -> UserContext | None:
    return _current_user.get()


def reset_user_context(reset_token) -> None:
    _current_user.reset(reset_token)
