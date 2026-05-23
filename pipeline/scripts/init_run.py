import json
import os
import sys
from pathlib import Path
from datetime import datetime
import argparse
import dotenv

def init_run(dry_run=False):
    """Initialise a new pipeline execution directory and metadata."""
    project_root = Path(__file__).resolve().parent.parent.parent
    
    # Load env/config
    dotenv.load_dotenv(project_root / ".env")
    config_path = project_root / "config.json"
    
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
        
    run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir = project_root / "pipeline_runs" / run_id
    
    manifest = {
        "run_id": run_id,
        "started_at": datetime.now().isoformat(),
        "config_snapshot": config,
        "llm_model": os.getenv("LLM_MODEL", "openai/kimi-k2.6"),
        "paper2spec_model": os.getenv("PAPER2SPEC_MODEL", "openai/kimi-k2.6"),
        "status": "active"
    }
    
    initial_state = {
        "current_node": "00_auth",
        "completed_nodes": [],
        "node_states": {},
        "status": "running",
        "iteration_count": 0,
        "max_iterations": config.get("budgets", {}).get("max_self_heal_iterations", 5),
        "budget_remaining": {
            "tokens": config.get("budgets", {}).get("max_tokens", 500000),
            "cost_usd": config.get("budgets", {}).get("max_cost_usd", 5.00),
            "simulations": config.get("budgets", {}).get("max_simulation_count", 10)
        }
    }
    
    if dry_run:
        print(f"[DRY-RUN] Would create directory: {run_dir}")
        print(f"[DRY-RUN] Would write manifest: {json.dumps(manifest, indent=2)}")
        print(f"[DRY-RUN] Would write initial state: {json.dumps(initial_state, indent=2)}")
        return str(run_dir)
        
    # Write to disk
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "nodes").mkdir(exist_ok=True)
    
    with open(run_dir / "run_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        
    with open(run_dir / "pipeline_state.json", "w", encoding="utf-8") as f:
        json.dump(initial_state, f, indent=2, ensure_ascii=False)
        
    # Create commander_log.md
    with open(run_dir / "commander_log.md", "w", encoding="utf-8") as f:
        f.write(f"# Pipeline Run Log: {run_id}\n\n- **Started**: {manifest['started_at']}\n- **Model**: {manifest['llm_model']}\n\n## Timeline\n")
        
    print(f"✅ Run initialized successfully at: {run_dir}")
    return str(run_dir)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Initialize pipeline run.")
    parser.add_argument("--dry-run", action="store_true", help="Dry run print outputs without writing to disk.")
    args = parser.parse_args()
    
    init_run(dry_run=args.dry_run)
