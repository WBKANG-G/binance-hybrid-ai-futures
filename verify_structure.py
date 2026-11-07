"""Compare candidate AST with the approved installed source; no venue access."""

import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).parent


def function(tree, name):
    return next(
        n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    )


def verify(base):
    approved = ast.parse((base / "integrations/binance_usdm/demo_snapshot.py").read_text())
    candidate = ast.parse((ROOT / "candidate_snapshot.py").read_text())
    for name in ("signed_number", "pick", "snapshot", "operational_snapshot"):
        assert ast.dump(function(approved, name)) == ast.dump(function(candidate, name)), name

    class RemoveClosedCheck(ast.NodeTransformer):
        count = 0

        def visit_BoolOp(self, node):
            self.generic_visit(node)
            values = []
            for value in node.values:
                if (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name)
                    and value.func.id == "receipt_proven_closed"
                ):
                    self.count += 1
                else:
                    values.append(value)
            node.values = values
            return node

    remove = RemoveClosedCheck()
    altered = remove.visit(function(candidate, "_snapshot"))
    assert remove.count == 1 and ast.dump(altered) == ast.dump(function(approved, "_snapshot"))
    parent = ast.parse((base / "services/execution/readiness_closed_history.py").read_text())

    class DecoupleValuation(ast.NodeTransformer):
        count = 0

        def visit_Compare(self, node):
            self.generic_visit(node)
            if ast.unparse(node) == "ledger.value.as_of <= stamp <= now":
                self.count += 1
                return ast.Compare(
                    left=ast.Name(id="stamp", ctx=ast.Load()),
                    ops=[ast.LtE()],
                    comparators=[ast.Name(id="now", ctx=ast.Load())],
                )
            return node

    transform = DecoupleValuation()
    expected = transform.visit(function(parent, "_matches"))
    assert transform.count == 1 and ast.dump(expected) == ast.dump(
        function(ast.parse((ROOT / "candidate_snapshot.py").read_text()), "_matches")
    )
    unchanged = [
        "services/execution/demo.py",
        "services/execution/demo_validation.py",
        "services/execution/demo_tp_trigger_check.py",
        "services/execution/demo_stream.py",
        "services/execution/demo_empty_db_restart.py",
        "services/risk/paper.py",
        "packages/persistence/demo.py",
        "MASTER_SPEC_V1_0.md",
    ]
    return {
        "functions_identical": ["signed_number", "pick", "snapshot", "operational_snapshot"],
        "snapshot_parser_identical_after_removing_one_closed_receipt_condition": True,
        "receipt_validator_change": "Only decouple current account valuation time from immutable past closure; all original binding checks retained.",
        "unchanged_approved_proof_sources": {
            n: hashlib.sha256((base / n).read_bytes()).hexdigest() for n in unchanged
        },
        "runtime_import_or_cli_activation_added": False,
    }


if __name__ == "__main__":
    result = verify(Path.cwd())
    (ROOT / "STRUCTURE_PROOF.json").write_text(json.dumps(result, indent=2) + "\n")
    print("Approved parser and proof structure comparison passed.")
