# 验收专用启动脚本：仅在本次验收进程内把 Gitee API 调用替换为本地桩，
# 产品代码不做任何修改。真实 Gitee 依赖（有效 Token）不可用时用于跑通任务全链路。
import uvicorn

from app import main as m

REAL_GITEE_API_GET = m.gitee_api_get
REAL_FETCH_GITEE_BRANCH_HEAD = m.fetch_gitee_branch_head
REAL_SYNC_REPOSITORY_SNAPSHOT = m.sync_repository_snapshot

REAL_ISSUE = {
    "id": 34190395,
    "number": "IKCTH7",
    "state": "open",
    "title": "身份证号码校验不正确",
    "body": "IdcardUtil.isValidCard 用于校验身份证号码，身份证参照片的身份证号校验结果不正确，存在兼容性问题，期望修复。",
    "labels": [{"name": "bug"}, {"name": "修复"}],
    "user": {"name": "验收用户"},
}
REAL_COMMENTS = [
    {"id": 1, "user": {"name": "评论人A"}, "body": "复现步骤已补充。"},
    {"id": 2, "user": {"name": "评论人B"}, "body": "回归测试需要覆盖 18 位号码。"},
]
HIGH_RISK_ISSUE = {
    "id": 9990001,
    "number": "IACC1",
    "state": "open",
    "title": "支付回调权限校验缺失导致资金风险",
    "body": "生产环境支付回调缺少鉴权，存在删除资金记录的安全漏洞，需要评估权限与 token 校验。",
    "labels": [{"name": "security"}],
}
HIGH_RISK_COMMENTS = [{"id": 3, "user": {"name": "评论人C"}, "body": "涉及生产资金链路，请尽快处理。"}]
TINY_ISSUE = {
    "id": 9990002,
    "number": "ISMALL",
    "state": "open",
    "title": "占位",
    "body": "",
    "labels": [],
    "user": {"name": "验收用户"},
}
LOW_ISSUE = {
    "id": 9990003,
    "number": "ILOW1",
    "state": "open",
    "title": "优化首页文案展示",
    "body": "调整首页说明文字与排版顺序。",
    "labels": [{"name": "docs"}],
}
PAY_CALLBACK_ISSUE = {
    "id": 9990004,
    "number": "IPAY1",
    "state": "open",
    "title": "支付回调重复记账导致订单重复入账",
    "body": "支付渠道超时重试时，同一回调事件会被重复投递，网关幂等键未生效，订单被重复入账。请检查回调处理的幂等校验与去重逻辑。",
    "labels": [{"name": "bug"}, {"name": "支付"}],
    "user": {"name": "验收用户"},
}
PAY_CALLBACK_COMMENTS = [
    {"id": 4, "user": {"name": "支付负责人"}, "body": "期望在回调入口按幂等键去重，重复回调只记账一次。"},
]

# DEV-008 验收：首个索引完成前分支在 COMMIT_A，之后模拟一次新提交切到 COMMIT_B，
# 之后 head 保持 COMMIT_B —— 同一进程内可连续演示「自动建索引→过期重建→同 Commit 复用」
HEAD_COMMITS = [
    "1f0a9b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a",
    "9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a2f1e0d",
]


def stub_fetch_gitee_branch_head(repository, base_branch=None):
    if getattr(repository, "url", "") == "https://gitee.com/yang-zhizemin/dreamweaver-studio-pro":
        return REAL_FETCH_GITEE_BRANCH_HEAD(repository, base_branch)
    completed = [record for record in m.REPOSITORY_INDEXES.values() if record.repository_id == repository.id and record.status == "COMPLETED"]
    return HEAD_COMMITS[1] if completed else HEAD_COMMITS[0]


def stub_sync_repository_snapshot(repository, commit_sha):
    if getattr(repository, "url", "") == "https://gitee.com/yang-zhizemin/dreamweaver-studio-pro":
        return REAL_SYNC_REPOSITORY_SNAPSHOT(repository, commit_sha)
    version_line = f"# 快照 Commit：{commit_sha[:8]}（验收桩模拟文件快照）\n"
    return [
        m.CodeFile(
            path="services/payment/callback.py",
            language="python",
            content=(
                "from __future__ import annotations\n"
                "\n"
                '"""支付回调入口：负责渠道回调的验签、幂等去重与入账通知。\n'
                "\n"
                "历史问题：渠道超时重试会重复投递同一回调事件，幂等键未生效时\n"
                "订单会被重复入账（线上 P0 事故，2026-08 复盘）。\n"
                '"""\n'
                "\n"
                "import hashlib\n"
                "\n"
                "from app.services.payment import notify, order_repo\n"
                "\n"
                "IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60\n"
                "\n"
                "\n"
                "class CallbackService:\n"
                "    \"\"\"渠道回调处理服务。\"\"\"\n"
                "\n"
                "    def __init__(self, order_repository=order_repo):\n"
                "        self.orders = order_repository\n"
                "\n"
                "    def _idempotency_key(self, event):\n"
                "        raw = f\"{event.channel}:{event.event_id}\"\n"
                "        return hashlib.sha256(raw.encode(\"utf-8\")).hexdigest()\n"
                "\n"
                "    def handle_callback(self, event):\n"
                "        key = self._idempotency_key(event)\n"
                "        if self.orders.is_processed(key):\n"
                "            # 幂等命中：回调已记账，直接返回成功，避免重复入账\n"
                "            return notify.callback_ack(event, deduped=True)\n"
                "        order = order_repo.lock_by_no(event.order_no)\n"
                "        if order is None:\n"
                "            notify.callback_failed(event, reason=\"order_not_found\")\n"
                "            return\n"
                "        order.mark_paid(channel=event.channel, paid_at=event.paid_at)\n"
                "        order_repo.save(order)\n"
                "        self.orders.record_processed(key, ttl=IDEMPOTENCY_TTL_SECONDS)\n"
                "        notify.order_paid(order, source=\"callback\")\n"
                "\n"
                "\n"
                "def handle_channel_callback(event):\n"
                "    service = CallbackService()\n"
                "    service.handle_callback(event)\n"
            ),
        ),
        m.CodeFile(
            path="services/payment/validator.py",
            language="python",
            content=(
                "from __future__ import annotations\n"
                "\n"
                '"""身份信息校验：身份证号码（IdcardUtil 兼容层）与证件参数校验。"""\n'
                "\n"
                "import re\n"
                "\n"
                "ID_CARD_PATTERN = re.compile(r\"^\\d{17}[\\dXx]$\")\n"
                "WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]\n"
                "CHECK_CODES = \"10X98765432\"\n"
                "\n"
                "\n"
                "class IdcardUtil:\n"
                "    \"\"\"兼容层：身份证号码校验工具。\"\"\"\n"
                "\n"
                "    @staticmethod\n"
                "    def isValidCard(card_no):\n"
                "        return validate_id_card(card_no)\n"
                "\n"
                "\n"
                "def validate_id_card(card_no: str) -> bool:\n"
                "    if not card_no or not ID_CARD_PATTERN.match(card_no):\n"
                "        return False\n"
                "    digits = [int(ch) for ch in card_no[:17]]\n"
                "    total = sum(d * w for d, w in zip(digits, WEIGHTS))\n"
                "    expect = CHECK_CODES[total % 11]\n"
                "    actual = card_no[17].upper()\n"
                "    return expect == actual\n"
                "\n"
                "\n"
                "def validate_bank_card(card_no: str) -> bool:\n"
                "    return bool(re.fullmatch(r\"\\d{16,19}\", card_no))\n"
            ),
        ),
        m.CodeFile(
            path="services/payment/notify.py",
            language="python",
            content=(
                "from __future__ import annotations\n"
                "\n"
                '"""记账结果通知：订单支付成功后发送渠道回执与内部事件。"""\n'
                "\n"
                "import json\n"
                "\n"
                "from app.services.payment import event_bus\n"
                "\n"
                "\n"
                "def callback_ack(event, deduped=False):\n"
                "    payload = {\"event_id\": event.event_id, \"deduped\": deduped}\n"
                "    event_bus.publish(\"callback.ack\", json.dumps(payload))\n"
                "    return payload\n"
                "\n"
                "\n"
                "def callback_failed(event, reason):\n"
                "    event_bus.publish(\"callback.failed\", json.dumps({\"event_id\": event.event_id, \"reason\": reason}))\n"
                "\n"
                "\n"
                "def order_paid(order, source=\"callback\"):\n"
                "    payload = {\"order_no\": order.order_no, \"amount\": order.amount, \"source\": source}\n"
                "    event_bus.publish(\"order.paid\", json.dumps(payload))\n"
                "    return payload\n"
            ),
        ),
        m.CodeFile(
            path="tests/test_callback.py",
            language="python",
            content=(
                "import pytest\n"
                "\n"
                "from app.services.payment.callback import CallbackService\n"
                "\n"
                "\n"
                "def test_repeated_callback_only_records_once():\n"
                "    service = CallbackService()\n"
                "    event = make_callback_event(\"PAY-20260904-001\", event_id=\"evt_repeat_1\")\n"
                "    service.handle_callback(event)\n"
                "    service.handle_callback(event)  # 渠道超时重试投递同一事件\n"
                "    assert order_repo.recorded_times(\"PAY-20260904-001\") == 1\n"
                "\n"
                "\n"
                "def test_callback_unknown_order_fails_fast():\n"
                "    event = make_callback_event(\"PAY-NOT-EXIST\")\n"
                "    service = CallbackService()\n"
                "    assert service.handle_callback(event) is None\n"
            ),
        ),
        m.CodeFile(
            path="README.md",
            language="markdown",
            content=(
                "# payment-service\n"
                + version_line
                + "\n"
                "支付回调、对账与交易链路服务。主要模块：\n"
                "\n"
                "- `services/payment/callback.py`：渠道回调入口（验签、幂等去重、记账）\n"
                "- `services/payment/validator.py`：身份证 / 银行卡参数校验\n"
                "- `services/payment/notify.py`：记账结果回执与内部事件\n"
                "- `tests/test_callback.py`：回调幂等与异常路径测试\n"
                "\n"
                "测试命令：`pytest tests/ -q`\n"
            ),
        ),
    ]


def stub_gitee_api_get(path: str, token: str):
    if "/repos/yang-zhizemin/dreamweaver-studio-pro/" in path:
        return REAL_GITEE_API_GET(path, token)
    if "/issues/IPAY1/comments" in path:
        return PAY_CALLBACK_COMMENTS
    if "/issues/IPAY1" in path:
        return PAY_CALLBACK_ISSUE
    if "/issues/IKCTH7/comments" in path:
        return REAL_COMMENTS
    if "/issues/IKCTH7" in path:
        return REAL_ISSUE
    if "/issues/IACC1/comments" in path:
        return HIGH_RISK_COMMENTS
    if "/issues/IACC1" in path:
        return HIGH_RISK_ISSUE
    if "/issues/ISMALL/comments" in path:
        return []
    if "/issues/ISMALL" in path:
        return TINY_ISSUE
    if "/issues/ILOW1/comments" in path:
        return []
    if "/issues/ILOW1" in path:
        return LOW_ISSUE
    raise m.HTTPException(status_code=502, detail={"code": 502, "message": f"验收桩未覆盖该路径: {path}", "data": None})


m.gitee_api_get = stub_gitee_api_get
m.fetch_gitee_branch_head = stub_fetch_gitee_branch_head
m.sync_repository_snapshot = stub_sync_repository_snapshot
app = m.app

if __name__ == "__main__":
    uvicorn.run("acceptance_stub:app", host="127.0.0.1", port=8000, log_level="info")
