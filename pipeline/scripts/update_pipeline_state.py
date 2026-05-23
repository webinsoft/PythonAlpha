import json
import sys
import argparse
from pathlib import Path
from datetime import datetime

def update_pipeline_state(run_dir_str: str, node_id: str, status: str, next_node: str = None) -> bool:
    """Update global pipeline state machine, subtract token/cost budgets, and log events."""
    run_dir = Path(run_dir_str).resolve()
    state_path = run_dir / "pipeline_state.json"
    
    if not state_path.is_file():
        print(f"❌ Error: pipeline_state.json not found: {state_path}")
        return False
        
    with open(state_path, "r", encoding="utf-8") as f:
        state = json.load(f)
        
    # Read node result to deduct budgets
    node_result_path = run_dir / "nodes" / node_id / "node_result.json"
    tokens_used = 0
    cost_used = 0.0
    
    if node_result_path.is_file():
        try:
            with open(node_result_path, "r", encoding="utf-8") as f:
                result = json.load(f)
            llm_usage = result.get("llm_usage", {})
            tokens_used = llm_usage.get("tokens", 0)
            cost_used = llm_usage.get("cost_usd", 0.0)
        except Exception as e:
            print(f"⚠️ Warning: Failed to parse node_result.json for budget updates: {e}")

    # Deduct budget
    state["budget_remaining"]["tokens"] = max(0, state["budget_remaining"]["tokens"] - tokens_used)
    state["budget_remaining"]["cost_usd"] = max(0.0, state["budget_remaining"]["cost_usd"] - cost_used)

    # Check budget constraints
    if state["budget_remaining"]["tokens"] <= 0 or state["budget_remaining"]["cost_usd"] <= 0.0:
        state["status"] = "exhausted"
        print("⚠️ Warning: Token or Cost budget exhausted!")

    # Update global status based on node execution status
    if status in ["blocked", "failed"]:
        state["status"] = status
    elif status == "success":
        if state.get("status") in ["blocked", "failed", "running"]:
            state["status"] = "running"

    # Update node states
    completed_time = datetime.now().isoformat()
    state["node_states"][node_id] = {
        "status": status,
        "completed_at": completed_time,
        "tokens_used": tokens_used,
        "cost_used": cost_used
    }
    
    if status == "success":
        if node_id not in state["completed_nodes"]:
            state["completed_nodes"].append(node_id)
            
    # Self-healing iteration tracking
    # If transitioning from Node 07 (diagnosis) or Node 05 (validate) back to Node 04 (codegen)
    if next_node == "04_codegen" and node_id in ["05_validate", "07_diagnosis"]:
        state["iteration_count"] += 1
        print(f"🔄 Entering self-healing iteration {state['iteration_count']}")

    # Transition current node
    if next_node:
        state["current_node"] = next_node
        
    # Write updated state
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        
    # Log to commander_log.md
    log_path = run_dir / "commander_log.md"
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"- **{completed_time}**: Node `{node_id}` finished with status `{status}`. "
                    f"Spent {tokens_used} tokens (${cost_used:.4f}). ")
            if next_node:
                f.write(f"Transitioning to `{next_node}`.\n")
            else:
                f.write("Pipeline terminated.\n")
    except Exception as e:
        print(f"⚠️ Warning: Failed to write to commander_log.md: {e}")
        
    print(f"✅ Pipeline state updated successfully: {node_id} -> {next_node} (status: {status})")
    return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Update global pipeline state.")
    parser.add_argument("--run-dir", required=True, help="Path to pipeline run directory")
    parser.add_argument("--node-id", required=True, help="ID of node completed")
    parser.add_argument("--status", required=True, choices=["success", "failed", "blocked", "degraded"], help="Node completion status")
    parser.add_argument("--next-node", help="ID of next node in state machine transition")
    args = parser.parse_args()
    
    if not update_pipeline_state(args.run_dir, args.node_id, args.status, args.next_node):
        sys.exit(1)
