import sys
import json
import argparse
from pathlib import Path

# Add project root and local node dir to sys.path
node_dir = Path(__file__).resolve().parent.parent
project_root = node_dir.parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(node_dir))

from pipeline.shared.wq_brain_client import (
    create_session,
    poll_alpha as wq_poll,
    fetch_alpha_details,
    BRAIN_API_URL,
)
from scripts.update_resume_state import update_resume_state


def poll_simulation(node_dir_str: str, simulation_id: str):
    node_dir = Path(node_dir_str).resolve()

    print(f"Logging in to poll simulation {simulation_id}...")
    sm = create_session()

    progress_url = f"{BRAIN_API_URL}/simulations/{simulation_id}"
    print(f"Polling progress from: {progress_url}...")

    # Delegate polling with Retry-After + terminal state detection
    data = wq_poll(sm, progress_url)

    # Process results
    alpha_id = data.get("alpha")
    results = {
        "simulation_id": simulation_id,
        "status": "success" if alpha_id else "error",
    }

    if alpha_id:
        print(f"Fetching metrics for alpha: {alpha_id}...")
        details = fetch_alpha_details(sm, alpha_id)
        is_data = details.get("is", {})

        metrics = {
            "sharpe": is_data.get("sharpe"),
            "turnover": is_data.get("turnover"),
            "fitness": is_data.get("fitness"),
            "returns": is_data.get("returns"),
            "drawdown": is_data.get("drawdown"),
            "margin": is_data.get("margin"),
        }
        results.update({"alpha_id": alpha_id, "metrics": metrics})

        print("Metrics retrieved successfully:")
        print(json.dumps(metrics, indent=2))

        # Write simulation_results.json
        with open(
            node_dir / "outputs" / "simulation_results.json", "w", encoding="utf-8"
        ) as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

        # Update resume state
        update_resume_state(
            node_dir_str=str(node_dir),
            status="success",
            simulation_id=simulation_id,
            alpha_id=alpha_id,
            metrics=metrics,
        )
    else:
        status = data.get("status", "UNKNOWN")
        msg = (
            data.get("message", "")
            or data.get("error", "Unknown simulation failure or Cancelled")
        )
        err_text = f"Simulation failed ({status}): {msg}"
        results.update({"error": err_text})
        print(f"❌ {err_text}")

        # Write simulation_results.json (with error status)
        with open(
            node_dir / "outputs" / "simulation_results.json", "w", encoding="utf-8"
        ) as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

        # Update resume state
        update_resume_state(
            node_dir_str=str(node_dir),
            status="failed",
            simulation_id=simulation_id,
            error=err_text,
        )
        raise RuntimeError(err_text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Poll simulation status from WorldQuant Brain."
    )
    parser.add_argument("--node-dir", required=True)
    parser.add_argument("--simulation-id", required=True)
    args = parser.parse_args()

    poll_simulation(args.node_dir, args.simulation_id)
