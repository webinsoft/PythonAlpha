import sys
import argparse
from pathlib import Path
from datetime import datetime
import json

def validate_run_scope(run_dir_str: str, node_id: str) -> bool:
    """Verify that the node did not perform out-of-scope writes during its execution."""
    run_dir = Path(run_dir_str).resolve()
    node_dir = run_dir / "nodes" / node_id
    
    if not node_dir.is_dir():
        print(f"❌ Error: Node directory does not exist: {node_dir}")
        return False
        
    val_report_path = node_dir / "validation_report.json"
    if not val_report_path.is_file():
        print(f"❌ Error: validation_report.json missing for node: {node_id}")
        return False
        
    try:
        with open(val_report_path, "r", encoding="utf-8") as f:
            val_report = json.load(f)
        started_at_str = val_report.get("started_at")
        started_at = datetime.fromisoformat(started_at_str).timestamp()
    except Exception as e:
        print(f"❌ Error: Failed to parse started_at from validation report: {e}")
        return False

    violation_files = []
    
    # Files/directories that are allowed to be modified at the run_dir level
    allowed_at_root = [
        "pipeline_state.json",
        "commander_log.md",
        "run_manifest.json"
    ]

    for p in run_dir.glob("**/*"):
        if not p.is_file():
            continue
            
        # Is it inside target node_dir? (Allowed)
        try:
            p.relative_to(node_dir)
            continue
        except ValueError:
            pass
            
        # Is it one of the allowed root files?
        if p.parent == run_dir and p.name in allowed_at_root:
            continue
            
        # Check mtime
        file_mtime = p.stat().st_mtime
        if file_mtime >= started_at:
            # Check difference is more than 0.1 seconds to avoid system time precision noise
            if file_mtime - started_at > 0.5:
                violation_files.append(str(p.relative_to(run_dir)))
                
    if violation_files:
        print(f"❌ Scope violation detected! Node '{node_id}' modified out-of-scope files:")
        for f in violation_files:
            print(f"  - {f}")
        return False
        
    print(f"✅ Scope validation passed for node: {node_id} (no out-of-scope writes).")
    return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate node write scope.")
    parser.add_argument("--run-dir", required=True, help="Path to pipeline run directory")
    parser.add_argument("--node-id", required=True, help="ID of node to check scope for")
    args = parser.parse_args()
    
    if not validate_run_scope(args.run_dir, args.node_id):
        sys.exit(1)
