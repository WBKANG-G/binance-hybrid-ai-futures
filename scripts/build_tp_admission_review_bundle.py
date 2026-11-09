"""Build a source-equivalent stdin review tool; never run or install the result."""

import argparse
import ast
import hashlib
from pathlib import Path


def build_bundle(source_root: Path) -> str:
    policy = source_root / "services/execution/demo_order_admission.py"
    probe = source_root / "scripts/review_demo_tp_admission.py"
    text = probe.read_text()
    lines = text.splitlines(keepends=True)
    imports = [
        node
        for node in ast.parse(text).body
        if isinstance(node, ast.ImportFrom)
        and node.module == "services.execution.demo_order_admission"
    ]
    if len(imports) != 1:
        raise ValueError("Expected one frozen policy import")
    node = imports[0]
    lines[node.lineno - 1 : node.end_lineno] = []
    banner = "# Generated read-only stdin bundle. No DB, installation or order capability.\n"
    banner += "# policy_sha256=" + hashlib.sha256(policy.read_bytes()).hexdigest() + "\n"
    banner += "# probe_sha256=" + hashlib.sha256(probe.read_bytes()).hexdigest() + "\n"
    bundle = banner + policy.read_text() + "\n\n" + "".join(lines)
    compile(bundle, "review_tp_entry_admission.py", "exec")
    return bundle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(build_bundle(Path(__file__).resolve().parents[1]))


if __name__ == "__main__":
    main()
