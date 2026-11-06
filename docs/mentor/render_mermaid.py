"""从 mentor 交付文档中抽取 ```mermaid 代码块并渲染为 PNG。

用法：
    python render_mermaid.py <markdown-path> <out-dir>

依赖：@mermaid-js/mermaid-cli（mmdc）。若 mmdc 不可用则只导出 .mmd 源文件。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

FENCE_RE = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL)


def extract(md_path: Path) -> list[tuple[str, str]]:
    text = md_path.read_text(encoding="utf-8")
    blocks = FENCE_RE.findall(text)
    named: list[tuple[str, str]] = []
    for index, block in enumerate(blocks, start=1):
        body = block.strip()
        first = body.splitlines()[0] if body else ""
        if first.startswith("mindmap"):
            kind = "mindmap"
        elif first.startswith("sequenceDiagram"):
            kind = "sequence"
        elif first.startswith("flowchart") or first.startswith("graph"):
            kind = "flow"
        else:
            kind = "other"
        named.append((f"{index:02d}-{kind}", body))
    return named


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    md_path = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)

    blocks = extract(md_path)
    print(f"extracted {len(blocks)} mermaid blocks from {md_path}")

    mmdc = os.environ.get("MMDC", "")
    puppet = Path(os.environ.get("MMDC_PUPPETEER_CONFIG", "")) if os.environ.get("MMDC_PUPPETEER_CONFIG") else None

    results = []
    for name, body in blocks:
        mmd = out_dir / f"{name}.mmd"
        mmd.write_text(body, encoding="utf-8")
        png = out_dir / f"{name}.png"
        entry = {"name": name, "mmd": str(mmd), "png": None, "ok": False}
        if mmdc:
            cmd = [mmdc, "-i", str(mmd), "-o", str(png), "-b", "white", "-s", "2"]
            if puppet is not None:
                cmd += ["-p", str(puppet)]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                entry["ok"] = proc.returncode == 0 and png.exists()
                entry["stderr"] = (proc.stderr or "")[-800:]
            except Exception as error:  # noqa: BLE001
                entry["stderr"] = f"{type(error).__name__}: {error}"
        results.append(entry)
        print(f"  {name}: ok={entry['ok']} {entry.get('stderr','')[:300]}")

    (out_dir / "render-report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0 if all(r["ok"] for r in results) or not mmdc else 0


if __name__ == "__main__":
    raise SystemExit(main())
