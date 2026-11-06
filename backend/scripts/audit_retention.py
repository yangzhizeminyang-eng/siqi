"""OPT-14 审计保留运维 CLI：默认 dry-run，只有 --apply 才真正删除。

用法（cwd = backend）：
    python -m scripts.audit_retention --days 90                # 只读计划（默认 dry-run）
    python -m scripts.audit_retention --days 90 --json         # 机器可读输出
    python -m scripts.audit_retention --days 90 --apply        # 真正执行（受封顶/下限约束）
    python -m scripts.audit_retention --days 7 --apply --allow-below-min   # 显式放行低于 min_days 的窗口

退出码：0 = 正常（含 dry-run）；1 = 策略拒绝执行（refused）；2 = 参数错误
（argparse 或缺/非法 --days、`--max-delete <= 0`）。`--max-delete 0 --apply` 在任何
内存/DB 变更前拒绝，绝不解释为「删除 1 条」。
"""

from __future__ import annotations

import argparse
import json
import sys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audit_retention",
        description="审计事件保留清理（默认 dry-run；0 表示永久保留）",
    )
    parser.add_argument("--days", type=int, required=True, help="保留窗口（天）；早于此窗口的事件允许清理")
    parser.add_argument("--apply", action="store_true", help="真正执行删除（缺省为 dry-run，只输出计划）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果（最后一行）")
    parser.add_argument("--allow-below-min", action="store_true", help="显式放行低于 min_days 的窗口（防误删下限）")
    parser.add_argument("--max-delete", type=int, default=None, help="单次删除上限（只能收紧策略封顶）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    from app.tenancy.audit_retention import prune_audit_events, retention_policy

    result = prune_audit_events(
        args.days,
        dry_run=not args.apply,
        max_delete=args.max_delete,
        allow_below_min=args.allow_below_min,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, default=str))
    else:
        policy = retention_policy()
        mode = "APPLY" if args.apply else "DRY-RUN"
        print(f"[audit-retention] 模式={mode} days={result['days']} 策略 min_days={policy['min_days']} "
              f"max_delete={policy['max_delete']}")
        if result["refused"]:
            print(f"[audit-retention] 已拒绝：{result['reason']}")
        else:
            print(f"[audit-retention] 候选={result['candidates']}（共 {result['total']} 条）"
                  f" 删除={result['deleted']}（内存 {result['deleted_memory']} / DB {result['deleted_db']}）"
                  f" 截断={result['truncated']} 生效上限={result['effective_max_delete']} "
                  f"策略封顶={result['capped_by_policy']} cutoff={result['cutoff']}")

    if result.get("error_code") in {"invalid_max_delete", "invalid_days"}:
        return 2
    return 1 if result["refused"] else 0


if __name__ == "__main__":
    sys.exit(main())
