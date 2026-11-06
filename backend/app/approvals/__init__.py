"""企业版 E2-01 审批模块：状态机、幂等、并发、租户隔离、审计。"""

from app.approvals.models import ApprovalApplicationRecord

__all__ = ["ApprovalApplicationRecord"]