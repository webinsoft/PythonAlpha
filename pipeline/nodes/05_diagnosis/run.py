import os
import sys
import json
import argparse
from pathlib import Path

# Add project root and node dir to sys.path
node_dir = Path(__file__).resolve().parent
project_root = node_dir.parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(node_dir))

from pipeline.shared.write_summary import init_node_bundle, finish_node_bundle, log_process, add_evidence
from scripts.compare_metrics import compare_metrics
from scripts.archive_best_k import archive_best_k

def run(run_dir_str: str):
    run_dir = Path(run_dir_str).resolve()
    node_dir = run_dir / "nodes" / "05_diagnosis"
    
    # 1. Load node_input.json
    input_path = node_dir / "node_input.json"
    if not input_path.is_file():
        print(f"❌ Error: node_input.json not found at {input_path}")
        sys.exit(1)
        
    with open(input_path, "r", encoding="utf-8") as f:
        input_data = json.load(f)
        
    config = input_data.get("config", {})
    thresholds = config.get("thresholds", {
        "min_sharpe": 1.0,
        "min_fitness": 0.5,
        "max_turnover": 0.5
    })
    
    budgets = config.get("budgets", {})
    max_iterations = budgets.get("max_self_heal_iterations", 5)
    best_k_top = config.get("pipeline", {}).get("best_k_top", 3)
    
    iteration = input_data.get("iteration", 0)
    
    # Locate inputs
    spec_path = run_dir / "nodes" / "01_fastexpr_parser" / "outputs" / "strategy_spec.json"
    code_path = run_dir / "nodes" / "02_codegen" / "outputs" / "alphas.py"
    sim_res_path = run_dir / "nodes" / "04_simulation" / "outputs" / "simulation_results.json"
    
    log_process(node_dir, f"Starting diagnosis and self-healing analysis (iteration {iteration})...")
    
    # Block checks
    if not spec_path.is_file():
        err_msg = f"strategy_spec.json not found at: {spec_path}"
        log_process(node_dir, err_msg)
        finish_node_bundle(node_dir, "failed", err_msg, errors=[err_msg])
        sys.exit(1)
        
    if not code_path.is_file():
        err_msg = f"alphas.py not found at: {code_path}"
        log_process(node_dir, err_msg)
        finish_node_bundle(node_dir, "failed", err_msg, errors=[err_msg])
        sys.exit(1)
        
    # Read spec and code
    with open(spec_path, "r", encoding="utf-8") as f:
        strategy_spec = json.load(f)
        
    with open(code_path, "r", encoding="utf-8") as f:
        code_content = f.read()
        
    # Read simulation results (with fallback if missing)
    sim_results = {}
    if sim_res_path.is_file():
        try:
            with open(sim_res_path, "r", encoding="utf-8") as f:
                sim_results = json.load(f)
        except Exception as e:
            log_process(node_dir, f"Warning: Failed to load simulation results: {e}")
            
    if not sim_results:
        sim_results = {
            "status": "error",
            "error": "Simulation results file was missing or unreadable."
        }
        
    # Compare metrics or handle error
    is_success = sim_results.get("status") == "success"
    metrics = sim_results.get("metrics") or {}
    
    passed = False
    failed_reasons = []
    
    if is_success:
        passed, failed_reasons = compare_metrics(metrics, thresholds)
        if passed:
            log_process(node_dir, f"Alpha simulation met all risk and performance thresholds! Sharpe: {metrics.get('sharpe')}")
        else:
            log_process(node_dir, f"Alpha simulation failed thresholds. Reasons: {failed_reasons}")
    else:
        log_process(node_dir, f"Alpha simulation failed to execute. Error: {sim_results.get('error')}")
        
    # Determine decision routing
    diag_file = node_dir / "outputs" / "diagnosis.json"
    
    # 1. Check if we already have diagnosis.json provided by Agent
    is_valid_diag = False
    if diag_file.is_file():
        try:
            with open(diag_file, "r", encoding="utf-8") as f:
                diag_output = json.load(f)
            if diag_output.get("iteration") == iteration:
                is_valid_diag = True
            else:
                log_process(node_dir, f"Stale diagnosis.json from iteration {diag_output.get('iteration')} ignored (current iteration is {iteration}).")
                diag_file.unlink()
        except Exception:
            pass
            
    if not is_valid_diag:
        if passed:
            # If passed, we can auto-generate the diagnosis.json successfully
            diag_output = {
                "decision": "APPROVED",
                "feedback": "Approved.",
                "diagnostic": "Alpha met all thresholds successfully.",
                "modification_guide": "None. Alpha approved.",
                "metrics": metrics,
                "passed": True,
                "failed_reasons": [],
                "iteration": iteration
            }
            out_dir = node_dir / "outputs"
            out_dir.mkdir(exist_ok=True)
            with open(diag_file, "w", encoding="utf-8") as f:
                json.dump(diag_output, f, indent=2, ensure_ascii=False)
            add_evidence(node_dir, "outputs/diagnosis.json", "write", reason="Auto-generated diagnosis for successful run")
        else:
            # Simulation failed. Write agent_prompt.txt and block the node
            log_process(node_dir, "Simulation thresholds failed and diagnosis.json not found. Writing agent_prompt.txt and blocking node...")
            
            prompt_file = node_dir / "outputs" / "agent_prompt.txt"
            proposed_decision = "04_codegen" if iteration < max_iterations - 1 else "BEST_K_BRANCH"
            
            prompt_content = f"""【Agent Task: Simulation Diagnosis & Self-Healing Feedback】
You are running in a fully Agentic mode (no LLM API calls). The pipeline has reached the Diagnosis stage (Iteration {iteration}).
The backtest simulation did not pass the required thresholds or failed with errors. 

Your task is to analyze the simulation results, diagnose the root cause, write code modification feedback, and save it to:
File Path: {diag_file}

=== CRITERIA & METRICS ===
- Required Thresholds:
  - min_sharpe: {thresholds.get('min_sharpe')}
  - min_fitness: {thresholds.get('min_fitness')}
  - max_turnover: {thresholds.get('max_turnover')}
- Actual Simulation Output:
{json.dumps(sim_results, indent=2, ensure_ascii=False)}
- Failed Reasons: {failed_reasons}

=== ALPHA SOURCE CODE ===
```python
{code_content}
```

=== TASK INSTRUCTIONS ===
1. Analyze why the Alpha failed (e.g. was it a compile error, runtime error, low Sharpe, or high Turnover?).
2. Formulate specific code correction advice for the next iteration (this will be fed into the next codegen attempt).
3. Determine the routing 'decision' (either '04_codegen' to retry or 'BEST_K_BRANCH' if you want to stop).
4. Save the results as JSON matching this format:
{{
  "decision": "{proposed_decision}",
  "feedback": "Write specific correction instructions for codegen here.",
  "diagnostic": "Describe the root cause of the simulation failure here.",
  "modification_guide": "Describe step-by-step how to modify the alpha code here.",
  "metrics": {json.dumps(metrics, indent=2, ensure_ascii=False) if metrics else "null"},
  "passed": false,
  "failed_reasons": {json.dumps(failed_reasons, ensure_ascii=False)},
  "iteration": {iteration}
}}

Once you have written this file, please rerun the pipeline (e.g. using resume_run).
"""
            out_dir = node_dir / "outputs"
            out_dir.mkdir(exist_ok=True)
            with open(prompt_file, "w", encoding="utf-8") as f:
                f.write(prompt_content)
                
            add_evidence(node_dir, "outputs/agent_prompt.txt", "write", reason="Prompt for manual Agent simulation diagnosis")
            
            finish_node_bundle(
                node_dir=node_dir,
                status="blocked",
                message=f"Simulation failed thresholds on iteration {iteration}. Agent prompt generated.",
                handoff_content=f"# Handoff: 07_diagnosis (Blocked)\n\n- Simulation diagnosis is blocked. Please inspect `outputs/agent_prompt.txt`.\n"
            )
            print(f"🛑 Node 07_diagnosis blocked on iteration {iteration}. Agent prompt written.")
            sys.exit(0)
            
    # 2. If diagnosis.json exists, load it
    log_process(node_dir, "Found diagnosis.json. Loading evaluation results...")
    try:
        with open(diag_file, "r", encoding="utf-8") as f:
            diag_output = json.load(f)
        decision = diag_output.get("decision", "BEST_K_BRANCH")
        passed = diag_output.get("passed", False)
        failed_reasons = diag_output.get("failed_reasons", [])
        diag_data = {
            "feedback": diag_output.get("feedback", ""),
            "diagnostic": diag_output.get("diagnostic", ""),
            "modification_guide": diag_output.get("modification_guide", "")
        }
    except Exception as e:
        err_msg = f"Failed to load diagnosis.json: {e}"
        log_process(node_dir, err_msg)
        finish_node_bundle(node_dir, "failed", err_msg, errors=[err_msg])
        sys.exit(1)
    
    # 3. Archive best candidates in survivors.json
    survivors = archive_best_k(
        node_dir=node_dir,
        iteration=iteration,
        code=code_content,
        sim_results=sim_results,
        best_k_top=best_k_top
    )
    add_evidence(node_dir, "outputs/survivors.json", "write", reason="Top survivors alpha list")
    add_evidence(node_dir, "outputs/history.json", "write", reason="Full run attempts history log")
    
    # 4. Generate Handoff and Finish Node
    handoff_lines = [
        f"# Handoff: 07_diagnosis",
        f"- **Iteration**: {iteration}",
        f"- **Decision**: {decision}",
        f"- **Passed**: {passed}",
    ]
    if not passed:
        handoff_lines.append(f"- **Failed Reasons**: {failed_reasons or sim_results.get('error')}")
        handoff_lines.append(f"- **Diagnostic**: {diag_data.get('diagnostic')}")
        handoff_lines.append(f"- **Feedback**: {diag_data.get('feedback')}")
    else:
        handoff_lines.append(f"- **Sharpe**: {metrics.get('sharpe')}")
        handoff_lines.append(f"- **Fitness**: {metrics.get('fitness')}")
        handoff_lines.append(f"- **Turnover**: {metrics.get('turnover')}")
        
    handoff_content = "\n".join(handoff_lines)
    
    msg = f"Diagnosis complete. Decision: {decision}. Passed thresholds: {passed}."
    finish_node_bundle(
        node_dir=node_dir,
        status="success",
        message=msg,
        handoff_content=handoff_content
    )
    sys.exit(0)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run diagnosis and self-healing node.")
    parser.add_argument("--run-dir", required=True, help="Path to pipeline run directory")
    args = parser.parse_args()
    
    run(args.run_dir)
