"""
Create node task input and dispatch prompt for a pipeline node.

Prepares node_input.json (with upstream artifacts and config) and generates
a structured dispatch prompt from subagent_prompt_template.md for use by
the PipelineAgent when dispatching NodeAgents.
"""

import json
import sys
import argparse
from pathlib import Path
from typing import Dict, List, Optional

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))

from pipeline.shared.write_summary import init_node_bundle


# Task-specific instructions per node (human-readable summary for dispatch prompt)
_NODE_TASK_INSTRUCTIONS: Dict[str, str] = {
    "00_auth": (
        "Authenticate with the WorldQuant Brain platform. "
        "Read credentials from .env, attempt login, and write outputs/auth_status.json "
        "with authentication result and session metadata."
    ),
    "01_input": (
        "Load config.json, fetch dataset metadata and available fields from the "
        "WorldQuant Brain API. Write outputs/fields.json (field whitelist) and "
        "outputs/dataset_metadata.json."
    ),
    "02_search": (
        "Search arXiv and SSRN for academic papers related to the target dataset fields. "
        "Download and parse the top-scoring paper into structured sections. "
        "Write outputs/search_results.json and outputs/papers_content.json. "
        "If no papers are found, degrade gracefully with a finance-intuition fallback."
    ),
    "03_extractor": (
        "Extract a structured strategy specification (StrategySpec) from the paper content. "
        "Map paper indicators to the available WQ Brain field whitelist. "
        "Write outputs/strategy_spec.json following the StrategySpec JSON schema. "
        "If the file does not exist, generate outputs/agent_prompt.txt and exit with blocked status."
    ),
    "04_codegen": (
        "Generate a compliant WorldQuant Brain Python Alpha script from the strategy spec. "
        "Follow the WQ Brain Python Alpha Cookbook rules (decorator, imports, pasteurize/neutralize/scale). "
        "Write attempt_N/outputs/alphas.py. If the file does not exist, generate agent_prompt.txt and block."
    ),
    "05_validate": (
        "Perform deterministic AST static analysis on the generated Python Alpha code. "
        "Verify syntax, imports, decorator signature, and return type compliance. "
        "Write outputs/validation_results.json with pass/fail status and error details."
    ),
    "06_simulation": (
        "Submit the validated Python Alpha to WorldQuant Brain for backtest simulation. "
        "Poll for results with rate-limit handling. Write outputs/simulation_results.json "
        "and outputs/resume_state.json for checkpoint recovery."
    ),
    "07_diagnosis": (
        "Evaluate backtest metrics against configured thresholds (Sharpe, Fitness, Turnover). "
        "If thresholds are met, auto-generate APPROVED diagnosis. "
        "If not met, generate outputs/agent_prompt.txt with simulation results and "
        "current alpha source code, then block for Agent diagnosis."
    ),
}


def build_dispatch_prompt(node_id: str, node_run_dir: Path, node_input: dict) -> str:
    """Generate a structured dispatch prompt for a NodeAgent.

    Uses subagent_prompt_template.md as the template and fills in
    node-specific context including upstream artifacts and task instructions.
    """
    template_path = project_root / "pipeline" / "subagent_prompt_template.md"

    if template_path.is_file():
        template = template_path.read_text(encoding="utf-8")
    else:
        # Fallback minimal template
        template = (
            "You are the Node Agent for the node: {node_id} ({node_label}).\n\n"
            "## Active Execution Directory\n{node_run_dir}\n\n"
            "## Inputs\n{node_input_json}\n\n"
            "## Task\n{task_instructions}\n"
        )

    node_config = _get_node_config(node_id)
    label = node_config.get("label", node_id) if node_config else node_id

    # Build upstream artifacts summary
    upstream = node_input.get("upstream_artifacts", {})
    artifacts_text = json.dumps(upstream, indent=2, ensure_ascii=False) if upstream else "(none)"

    task = _NODE_TASK_INSTRUCTIONS.get(node_id, f"Execute the business logic for node {node_id}.")

    prompt = template.format(
        node_id=node_id,
        node_label=label,
        node_run_dir=str(node_run_dir),
        upstream_artifacts_list=artifacts_text,
        task_instructions=task,
    )

    # Append node_input.json as structured context
    prompt += "\n\n## Full Node Input (node_input.json)\n```json\n"
    prompt += json.dumps(node_input, indent=2, ensure_ascii=False)
    prompt += "\n```\n"

    return prompt


def _get_node_config(node_id: str) -> Optional[dict]:
    """Load node config from registry."""
    registry_path = project_root / "pipeline" / "node_registry.json"
    if not registry_path.is_file():
        return None
    with open(registry_path, "r", encoding="utf-8") as f:
        registry = json.load(f)
    for node in registry.get("nodes", []):
        if node["id"] == node_id:
            return node
    return None


def create_node_task(run_dir_str: str, node_id: str) -> bool:
    """Create input configuration, dispatch prompt, and folder for a node."""
    run_dir = Path(run_dir_str).resolve()

    # Load registry and state
    registry_path = project_root / "pipeline" / "node_registry.json"
    with open(registry_path, "r", encoding="utf-8") as f:
        registry = json.load(f)

    state_path = run_dir / "pipeline_state.json"
    with open(state_path, "r", encoding="utf-8") as f:
        state = json.load(f)

    # Find target node config
    node_config = next((n for n in registry["nodes"] if n["id"] == node_id), None)
    if not node_config:
        print(f"Error: Node {node_id} not found in node registry.")
        return False

    node_run_dir = run_dir / "nodes" / node_id
    node_run_dir.mkdir(parents=True, exist_ok=True)

    # Gather upstream artifacts
    upstream_artifacts = {}
    for upstream_id in node_config.get("required_upstream", []):
        upstream_dir = run_dir / "nodes" / upstream_id
        if not upstream_dir.is_dir():
            print(f"Error: Required upstream node directory missing: {upstream_id}")
            return False

        upstream_outputs_dir = upstream_dir / "outputs"
        if not upstream_outputs_dir.is_dir():
            print(f"Error: Required upstream outputs missing: {upstream_id}")
            return False

        upstream_files = [
            str(p.resolve()) for p in upstream_outputs_dir.glob("**/*") if p.is_file()
        ]
        upstream_artifacts[upstream_id] = upstream_files

    # Read global config snapshot from manifest
    manifest_path = run_dir / "run_manifest.json"
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    config = manifest.get("config_snapshot", {})

    # Create node_input.json
    node_input = {
        "run_id": run_dir.name,
        "node_id": node_id,
        "iteration": state.get("iteration_count", 0),
        "config": config,
        "upstream_artifacts": upstream_artifacts,
    }

    # Initialize the 7-piece bundle
    init_node_bundle(node_run_dir, node_input)

    # Generate and write the dispatch prompt (for agent dispatch mode)
    dispatch_prompt = build_dispatch_prompt(node_id, node_run_dir, node_input)
    dispatch_prompt_path = node_run_dir / "dispatch_prompt.md"
    with open(dispatch_prompt_path, "w", encoding="utf-8") as f:
        f.write(dispatch_prompt)

    print(f"Initialized bundle for node: {node_id} at {node_run_dir}")
    print(f"Dispatch prompt written to: {dispatch_prompt_path}")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create node task files.")
    parser.add_argument("--run-dir", required=True, help="Path to active pipeline run directory")
    parser.add_argument("--node-id", required=True, help="ID of node to set up")
    args = parser.parse_args()

    if not create_node_task(args.run_dir, args.node_id):
        sys.exit(1)
