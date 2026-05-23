"""
Execute a single pipeline node — task creation, dispatch, validation, routing.

Supports two dispatch modes:
  - "subprocess" (default): subprocess.run() on node/run.py
  - "agent" (future): agent_open() dispatch — requires Tool Proxy support
    in the TUI runtime for sub-agents to access local file I/O.

When Tool Proxy is available, swap mode to "agent" and NodeAgents will
be dispatched as independent sub-sessions that read node_input.json and
write business outputs directly.
"""

import json
import os
import sys
import subprocess
import argparse
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))

from pipeline.scripts.validate_config import validate as validate_config
from pipeline.scripts.create_node_task import create_node_task
from pipeline.scripts.validate_node_bundle import validate_node_bundle
from pipeline.scripts.validate_run_scope import validate_run_scope
from pipeline.scripts.update_pipeline_state import update_pipeline_state


def dispatch_via_agent(run_dir: Path, node_id: str) -> bool:
    """Dispatch a NodeAgent via agent_open (requires Tool Proxy).

    This function is the future dispatch path.  When the TUI runtime
    supports Tool Proxy for agent_open sub-agents, this replaces
    subprocess.run() with agent_open() + agent_eval().

    For now, it falls back to subprocess mode with a warning.

    Parameters
    ----------
    run_dir : Path
        Pipeline run directory.
    node_id : str
        Node ID to dispatch.

    Returns
    -------
    bool
        True if the sub-agent completed successfully.
    """
    node_run_dir = run_dir / "nodes" / node_id
    dispatch_prompt_path = node_run_dir / "dispatch_prompt.md"

    if not dispatch_prompt_path.is_file():
        print(f"Warning: dispatch_prompt.md not found for {node_id}, "
              f"falling back to subprocess mode.")
        return _run_via_subprocess(run_dir, node_id)

    print(f"Agent dispatch mode: would dispatch NodeAgent for {node_id}")
    print(f"  Dispatch prompt: {dispatch_prompt_path}")

    # --- Future: replace with agent_open ---
    # prompt = dispatch_prompt_path.read_text()
    # agent_open(
    #     name=f"NodeAgent-{node_id}",
    #     prompt=prompt,
    #     cwd=str(project_root),
    #     allowed_tools=["read_file", "write_file", "list_dir", "grep_files"],
    #     fork_context=False,
    # )
    # result = agent_eval(name=f"NodeAgent-{node_id}", block=True, timeout_ms=300000)
    # return result.get("status") == "completed"
    # ----------------------------------------

    # Fallback to subprocess for now
    print("  (Tool Proxy not available — falling back to subprocess mode)")
    return _run_via_subprocess(run_dir, node_id)


def _run_via_subprocess(run_dir: Path, node_id: str) -> bool:
    """Execute node script via subprocess (current implementation)."""
    node_script_path = project_root / "pipeline" / "nodes" / node_id / "run.py"

    if not node_script_path.is_file():
        print(f"Node run script missing: {node_script_path}")
        return False

    cmd = [sys.executable, str(node_script_path), "--run-dir", str(run_dir)]
    print(f"Executing: {' '.join(cmd)}")

    try:
        res = subprocess.run(cmd, cwd=str(project_root), capture_output=True, text=True)
        if res.stdout:
            print("--- Standard Output ---")
            print(res.stdout)
        if res.stderr:
            print("--- Standard Error ---", file=sys.stderr)
            print(res.stderr, file=sys.stderr)
        return res.returncode == 0
    except Exception as e:
        print(f"Subprocess execution failed: {e}")
        return False


def _resolve_routing(node_id: str, node_status: str, node_run_dir: Path,
                     run_dir: Path, registry: dict, state: dict) -> str:
    """Determine the next node based on current node status and registry rules.

    Returns
    -------
    str or None
        The next node ID, or None if routing is paused (blocked/failed).
    """
    # Read validate/simulation results for branching nodes
    if node_id == "05_validate":
        result_path = node_run_dir / "outputs" / "validation_results.json"
        is_valid = False
        try:
            with open(result_path, "r", encoding="utf-8") as f:
                val_result = json.load(f)
            is_valid = val_result.get("valid", False)
        except Exception:
            pass

        if is_valid:
            print("AST validation passed. Routing to Node 06 (simulation).")
            return "06_simulation"

        current_attempts = state.get("iteration_count", 0)
        if current_attempts < 2:
            print(f"AST validation failed. Routing back to Node 04 (codegen) for attempt {current_attempts + 1}.")
            return "04_codegen"
        else:
            print("AST validation failed 3 times. Routing to BEST_K_BRANCH.")
            return "BEST_K_BRANCH"

    if node_id == "07_diagnosis":
        diag_path = node_run_dir / "outputs" / "diagnosis.json"
        decision = "BEST_K_BRANCH"
        try:
            with open(diag_path, "r", encoding="utf-8") as f:
                diag_result = json.load(f)
            decision = diag_result.get("decision", "BEST_K_BRANCH")
        except Exception:
            pass

        if decision == "APPROVED":
            print("Sharpe threshold reached! Routing to APPROVED.")
            return "APPROVED"

        if decision == "04_codegen":
            current_iterations = state.get("iteration_count", 0)
            if current_iterations < state.get("max_iterations", 5):
                print(f"Performance goals not met. Self-healing iteration {current_iterations + 1}.")
                return "04_codegen"
            else:
                print("Self-healing iteration limit reached. Routing to BEST_K_BRANCH.")
                return "BEST_K_BRANCH"

        print("Routing to BEST_K_BRANCH.")
        return "BEST_K_BRANCH"

    # Standard sequential routing
    node_config = next((n for n in registry["nodes"] if n["id"] == node_id), None)
    if node_config:
        allowed = node_config.get("allowed_next", [])
        if allowed:
            return allowed[0]

    return None


def run_node(run_dir_str: str, node_id: str, dispatch_mode: str = "subprocess") -> bool:
    """Run a single node's execution lifecycle.

    Parameters
    ----------
    run_dir_str : str
        Path to pipeline run directory.
    node_id : str
        Node ID to execute.
    dispatch_mode : str
        "subprocess" (default) or "agent" (requires Tool Proxy).

    Returns
    -------
    bool
        True if the node completed or is blocked (valid terminal state).
        False if the node failed fatally.
    """
    run_dir = Path(run_dir_str).resolve()

    print(f"\n{'='*50}")
    print(f"Running Node: {node_id} inside {run_dir.name} (mode={dispatch_mode})")
    print(f"{'='*50}")

    # 1. Validate config
    if not validate_config():
        print("Pipeline execution stopped: Configuration invalid.")
        return False

    # 2. Create node task (sets up node_input.json, dispatch_prompt.md, bundle)
    if not create_node_task(str(run_dir), node_id):
        print(f"Failed to initialize task for node: {node_id}")
        return False

    # 3. Dispatch
    if dispatch_mode == "agent":
        script_success = dispatch_via_agent(run_dir, node_id)
    else:
        script_success = _run_via_subprocess(run_dir, node_id)

    if not script_success:
        print(f"Node {node_id} dispatch/execution failed.")
        update_pipeline_state(str(run_dir), node_id, "failed", next_node=None)
        return False

    # 4. Validate bundle (blocked nodes get reduced validation)
    if not validate_node_bundle(str(run_dir), node_id):
        print(f"Node {node_id} did not produce a valid bundle.")
        update_pipeline_state(str(run_dir), node_id, "blocked", next_node=None)
        return False

    # 5. Validate scope
    if not validate_run_scope(str(run_dir), node_id):
        print(f"Node {node_id} violated file write scope boundaries.")
        update_pipeline_state(str(run_dir), node_id, "blocked", next_node=None)
        return False

    # 6. Read node status
    node_run_dir = run_dir / "nodes" / node_id
    node_status = "success"
    node_result_path = node_run_dir / "node_result.json"
    if node_result_path.is_file():
        try:
            with open(node_result_path, "r", encoding="utf-8") as f:
                node_res = json.load(f)
            node_status = node_res.get("status", "success")
        except Exception as e:
            print(f"Warning: Could not read node status: {e}")

    # 7. Determine routing
    registry = json.loads((project_root / "pipeline" / "node_registry.json").read_text())
    state_path = run_dir / "pipeline_state.json"
    state = json.loads(state_path.read_text())

    next_node = None
    if node_status in ("success", "degraded"):
        next_node = _resolve_routing(node_id, node_status, node_run_dir, run_dir, registry, state)
    else:
        print(f"Node {node_id} completed with status '{node_status}'. Routing paused.")

    # 8. Update state machine
    update_pipeline_state(str(run_dir), node_id, node_status, next_node=next_node)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a specific node in the pipeline.")
    parser.add_argument("--run-dir", required=True, help="Path to pipeline run directory")
    parser.add_argument("--node-id", required=True, help="Node ID to execute")
    parser.add_argument("--mode", default="subprocess", choices=["subprocess", "agent"],
                        help="Dispatch mode: subprocess (default) or agent")
    args = parser.parse_args()

    if not run_node(args.run_dir, args.node_id, dispatch_mode=args.mode):
        sys.exit(1)
