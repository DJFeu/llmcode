"""Recovery policy for agent teams — retry logic and failure handling."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RecoveryPolicy:
    max_retries: int = 2
    retry_delay_sec: int = 5
    on_all_failed: str = "abort"


class RecoveryAction:
    @staticmethod
    def should_retry(policy: RecoveryPolicy, attempt: int) -> bool:
        return attempt <= policy.max_retries

    @staticmethod
    def resolve_all_failed(policy: RecoveryPolicy) -> str:
        return policy.on_all_failed
