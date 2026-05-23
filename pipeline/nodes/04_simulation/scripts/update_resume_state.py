import json
import argparse
import sys
from pathlib import Path
from datetime import datetime

def update_resume_state(
    node_dir_str: str,
    status: str,
    simulation_id: str = None,
    alpha_id: str = None,
    error: str = None,
    metrics: dict = None,
    iteration: int = None
):
    node_dir = Path(node_dir_str).resolve()
    outputs_dir = node_dir / "outputs"
    outputs_dir.mkdir(exist_ok=True)
    resume_path = outputs_dir / "resume_state.json"
    
    state = {}
    if resume_path.is_file():
        try:
            with open(resume_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            pass
            
    state.update({
        "status": status,
        "updated_at": datetime.now().isoformat()
    })
    
    if iteration is None:
        # Try to read iteration from node_input.json
        input_path = node_dir / "node_input.json"
        if input_path.is_file():
            try:
                with open(input_path, "r", encoding="utf-8") as f:
                    input_data = json.load(f)
                iteration = input_data.get("iteration")
            except Exception:
                pass

    if iteration is not None:
        state["iteration"] = iteration
    if simulation_id is not None:
        state["simulation_id"] = simulation_id
    if alpha_id is not None:
        state["alpha_id"] = alpha_id
    if error is not None:
        state["error"] = error
    if metrics is not None:
        state["metrics"] = metrics
        
    with open(resume_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        
    print(f"Updated resume state to: {status}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Update Node 06 simulation resume state.")
    parser.add_argument("--node-dir", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--simulation-id")
    parser.add_argument("--alpha-id")
    parser.add_argument("--error")
    parser.add_argument("--metrics")
    args = parser.parse_args()
    
    metrics_dict = None
    if args.metrics:
        try:
            metrics_dict = json.loads(args.metrics)
        except Exception:
            pass
            
    update_resume_state(
        node_dir_str=args.node_dir,
        status=args.status,
        simulation_id=args.simulation_id,
        alpha_id=args.alpha_id,
        error=args.error,
        metrics=metrics_dict
    )
