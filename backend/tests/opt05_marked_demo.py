"""OPT-05 分类演示用例（文件名不匹配 test_*.py，默认套件不收集）。

由 test_opt05_service_classification.py 在子进程中显式指定运行，用于验证两类去向：
- broker 不可达 → conftest 统一 skip，理由标注「环境缺失（非代码失败）」；
- broker 可达 → 用例被真正执行。
"""

import pytest


@pytest.mark.requires_redis
def test_marked_demo_runs_when_broker_reachable():
    print("OPT05_MARKED_DEMO_EXECUTED")
