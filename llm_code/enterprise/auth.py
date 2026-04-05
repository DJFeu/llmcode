"""Authentication provider abstraction and identity model."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AuthIdentity:
    """Represents an authenticated user."""
    user_id: str
    email: str
    display_name: str
    groups: tuple[str, ...] = ()
    raw_claims: dict = field(default_factory=dict)


class AuthProvider(ABC):
    @abstractmethod
    async def authenticate(self) -> AuthIdentity: ...

    @abstractmethod
    async def refresh(self) -> AuthIdentity | None: ...

    @abstractmethod
    async def revoke(self) -> None: ...
