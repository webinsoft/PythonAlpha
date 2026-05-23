import sys
import json
import argparse
import shutil
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from pipeline.shared.write_summary import init_node_bundle, finish_node_bundle, log_process, add_evidence


def run(run_dir_str: str):
    run_dir = Path(run_dir_str).resolve()
    node_dir = run_dir / "nodes" / "01_fastexpr_parser"

    node_input_path = node_dir / "node_input.json"
    spec_path = node_dir / "outputs" / "strategy_spec.json"

    init_node_bundle(node_dir, input_data={})
    log_process(node_dir, "FastExpr Parser node (Step 2 placeholder)...")

    if spec_path.is_file():
        try:
            with open(spec_path, "r", encoding="utf-8") as f:
                spec = json.load(f)
            log_process(node_dir, f"Found existing strategy_spec.json: {spec.get('strategy_name', 'unknown')}")
            add_evidence(node_dir, "outputs/strategy_spec.json", "read", reason="Existing strategy spec loaded")
            finish_node_bundle(node_dir, "success", "Loaded existing strategy_spec.json")
            sys.exit(0)
        except Exception as e:
            log_process(node_dir, f"Failed to read existing strategy_spec.json: {e}")

    log_process(node_dir, "No strategy_spec.json found. This node requires manual input in Step 2.")
    finish_node_bundle(node_dir, "blocked", "strategy_spec.json not found. FastExpr parser not yet implemented.")
    sys.exit(0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FastExpr Parser (placeholder)")
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    run(args.run_dir)
