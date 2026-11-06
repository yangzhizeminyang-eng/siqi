"""企业版 E4-01 重试策略：每步骤独立 retry_count、配置上限、退避、失败分类。"""

from __future__ import annotations

import math

from app.enterprise_runtime.models import FailureKind


class RetryPolicy:
    def __init__(self, *, max_retries: int = 3, base_backoff_seconds: float = 1.0,
                 backoff_factor: float = 2.0, max_backoff_seconds: float = 120.0) -> None:
        self.max_retries = max(0, int(max_retries))
        self.base_backoff_seconds = max(0.0, float(base_backoff_seconds))
        self.backoff_factor = max(1.0, float(backoff_factor))
        self.max_backoff_seconds = max(0.0, float(max_backoff_seconds))

    def can_retry(self, retry_count: int, failure_kind: FailureKind | None) -> bool:
        """可重试且未超上限。不可重试/需要人工/需要审批的失败不自动重试。"""
        if failure_kind in (FailureKind.NON_RETRYABLE, FailureKind.NEEDS_HUMAN, FailureKind.NEEDS_APPROVAL):
            return False
        return retry_count < self.max_retries

    def next_backoff(self, retry_count: int) -> float:
        if retry_count < 0:
            retry_count = 0
        raw = self.base_backoff_seconds * (self.backoff_factor ** retry_count)
        return round(min(raw, self.max_backoff_seconds), 3)

    def classify(self, error_kind: str | None, *, needs_approval: bool = False,
                 needs_human: bool = False) -> FailureKind:
        if needs_human:
            return FailureKind.NEEDS_HUMAN
        if needs_approval:
            return FailureKind.NEEDS_APPROVAL
        if error_kind == "environment":
            return FailureKind.ENVIRONMENT
        if error_kind == "non_retryable":
            return FailureKind.NON_RETRYABLE
        return FailureKind.RETRYABLE

    def describe(self) -> dict:
        return {
            "max_retries": self.max_retries,
            "base_backoff_seconds": self.base_backoff_seconds,
            "backoff_factor": self.backoff_factor,
            "max_backoff_seconds": self.max_backoff_seconds,
        }


DEFAULT_POLICY = RetryPolicy()