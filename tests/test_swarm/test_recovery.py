"""Tests for agent recovery policy."""
from __future__ import annotations

import pytest

from llm_code.swarm.recovery import RecoveryPolicy, RecoveryAction


class TestRecoveryPolicy:
    def test_defaults(self) -> None:
        policy = RecoveryPolicy()
        assert policy.max_retries == 2
        assert policy.retry_delay_sec == 5
        assert policy.on_all_failed == "abort"

    def test_frozen(self) -> None:
        policy = RecoveryPolicy()
        with pytest.raises(AttributeError):
            policy.max_retries = 5


class TestRecoveryAction:
    def test_should_retry_under_limit(self) -> None:
        policy = RecoveryPolicy(max_retries=3)
        assert RecoveryAction.should_retry(policy, attempt=1) is True
        assert RecoveryAction.should_retry(policy, attempt=3) is True

    def test_should_not_retry_over_limit(self) -> None:
        policy = RecoveryPolicy(max_retries=2)
        assert RecoveryAction.should_retry(policy, attempt=3) is False

    def test_on_all_failed_abort(self) -> None:
        policy = RecoveryPolicy(on_all_failed="abort")
        assert RecoveryAction.resolve_all_failed(policy) == "abort"

    def test_on_all_failed_checkpoint(self) -> None:
        policy = RecoveryPolicy(on_all_failed="checkpoint_and_stop")
        assert RecoveryAction.resolve_all_failed(policy) == "checkpoint_and_stop"
