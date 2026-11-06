"""企业版 E2-01：风险分级。"""

from __future__ import annotations

from typing import Literal

RiskLevel = Literal["L0", "L1", "L2", "L3", "L4"]

RISK_LABELS: dict[RiskLevel, str] = {
    "L0": "只读 / 低风险，默认允许",
    "L1": "可逆低风险变更，默认允许或记录治理决策",
    "L2": "影响项目 / 任务 / 知识库 / 代码资产，需项目负责人或指定审批人审批",
    "L3": "影响生产 / 发布 / 批量数据 / 重要组织资源，需多人审批 + 测试门禁",
    "L4": "租户安全 / 权限 / 密钥 / 管理员 / 高危数据操作，需安全管理员审批 + 二次确认",
}

RISK_ORDER: dict[RiskLevel, int] = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}


def risk_max(a: RiskLevel, b: RiskLevel) -> RiskLevel:
    return a if RISK_ORDER[a] >= RISK_ORDER[b] else b