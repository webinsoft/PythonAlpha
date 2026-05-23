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
from scripts.submit_alpha import submit_alpha
from scripts.poll_simulation import poll_simulation
from scripts.update_resume_state import update_resume_state

def run(run_dir_str: str):
    run_dir = Path(run_dir_str).resolve()
    node_dir = run_dir / "nodes" / "04_simulation"
    
    # 1. Load node_input.json to retrieve config
    input_path = node_dir / "node_input.json"
    if not input_path.is_file():
        print(f"❌ Error: node_input.json not found at {input_path}")
        sys.exit(1)
        
    with open(input_path, "r", encoding="utf-8") as f:
        input_data = json.load(f)
        
    config = input_data.get("config", {})
    pipe_config = config.get("pipeline", {})
    
    region = pipe_config.get("region", "GLB")
    universe = pipe_config.get("universe", "MINVOL1M")
    delay = int(pipe_config.get("delay", 1))
    
    # Locate inputs
    code_path = run_dir / "nodes" / "02_codegen" / "outputs" / "alphas.py"
    
    log_process(node_dir, "Starting platform simulation node...")
    
    if not code_path.is_file():
        err_msg = f"alphas.py not found at: {code_path}"
        log_process(node_dir, err_msg)
        finish_node_bundle(node_dir, "failed", err_msg, errors=[err_msg])
        sys.exit(1)
        
    # Check resume state
    resume_path = node_dir / "outputs" / "resume_state.json"
    resume_state = {}
    iteration = input_data.get("iteration", 0)
    if resume_path.is_file():
        try:
            with open(resume_path, "r", encoding="utf-8") as f:
                temp_state = json.load(f)
            if temp_state.get("iteration") == iteration:
                resume_state = temp_state
            else:
                log_process(node_dir, f"Stale resume state from iteration {temp_state.get('iteration')} ignored (current iteration is {iteration}).")
                # Clear the stale files
                for fname in ["resume_state.json", "simulation_results.json", "submitted_batch.json"]:
                    fpath = node_dir / "outputs" / fname
                    if fpath.is_file():
                        fpath.unlink()
        except Exception as e:
            log_process(node_dir, f"Warning: Failed to load resume state: {e}")
            
    status = resume_state.get("status")
    sim_id = resume_state.get("simulation_id")
    
    completed = False
    
    if status == "success" and sim_id:
        log_process(node_dir, f"Resume state indicates success for simulation {sim_id}. Loading existing results.")
        results_path = node_dir / "outputs" / "simulation_results.json"
        if results_path.is_file():
            completed = True
        else:
            log_process(node_dir, f"Simulation results file missing despite success status. Will retry polling/simulation.")
            
    if completed:
        with open(node_dir / "outputs" / "simulation_results.json", "r", encoding="utf-8") as f:
            res_data = json.load(f)
        metrics = res_data.get("metrics", {})
        msg = f"Simulation loaded from resume state. Alpha ID: {res_data.get('alpha_id')}. Sharpe: {metrics.get('sharpe')}"
        handoff_content = f"""# Handoff: 06_simulation
- **Simulation ID**: {sim_id}
- **Alpha ID**: {res_data.get('alpha_id')}
- **Status**: success (resumed)
- **Metrics**:
  - Sharpe: {metrics.get('sharpe')}
  - Fitness: {metrics.get('fitness')}
  - Turnover: {metrics.get('turnover')}
"""
        finish_node_bundle(
            node_dir=node_dir,
            status="success",
            message=msg,
            handoff_content=handoff_content
        )
        sys.exit(0)
        
    # If submitted or processing, resume polling
    if status in ["submitted", "processing"] and sim_id:
        log_process(node_dir, f"Resuming simulation polling for ID: {sim_id}")
        try:
            poll_simulation(str(node_dir), sim_id)
            log_process(node_dir, "Polling completed successfully.")
        except Exception as e:
            err_msg = f"Polling resumed simulation failed: {e}"
            log_process(node_dir, err_msg)
            finish_node_bundle(node_dir, "failed", err_msg, errors=[err_msg])
            sys.exit(0)
    else:
        log_process(node_dir, f"Submitting new simulation for region={region}, universe={universe}, delay={delay}...")
        try:
            sim_id = submit_alpha(
                node_dir_str=str(node_dir),
                code_path_str=str(code_path),
                region=region,
                universe=universe,
                delay=delay
            )
            add_evidence(node_dir, "outputs/submitted_batch.json", "write", reason="New simulation batch submitted")
            add_evidence(node_dir, "outputs/resume_state.json", "write", reason="Resume state initialized")
            
            log_process(node_dir, f"Submitted simulation task. ID: {sim_id}. Polling progress...")
            poll_simulation(str(node_dir), sim_id)
            log_process(node_dir, "Simulation and polling completed successfully.")
        except Exception as e:
            err_msg = f"Submission or polling failed: {e}"
            log_process(node_dir, err_msg)
            results_path = node_dir / "outputs" / "simulation_results.json"
            
            # If we successfully retrieved and wrote simulation results with error status,
            # we classify this as 'degraded' (business logic error) to let Node 07 (diagnosis) handle it.
            # Otherwise, it remains a critical infrastructure 'failed' status.
            is_business_error = False
            if results_path.is_file():
                try:
                    with open(results_path, "r", encoding="utf-8") as f:
                        res_data = json.load(f)
                    if res_data.get("status") == "error":
                        is_business_error = True
                except Exception:
                    pass
            
            if not results_path.is_file():
                results = {
                    "simulation_id": sim_id or "unknown",
                    "status": "error",
                    "error": err_msg
                }
                with open(results_path, "w", encoding="utf-8") as f:
                    json.dump(results, f, indent=2, ensure_ascii=False)
            
            final_status = "degraded" if is_business_error else "failed"
            finish_node_bundle(node_dir, final_status, err_msg, errors=[err_msg])
            sys.exit(0)
            
    # Read simulation results to build handoff
    results_path = node_dir / "outputs" / "simulation_results.json"
    with open(results_path, "r", encoding="utf-8") as f:
        res_data = json.load(f)
        
    alpha_id = res_data.get("alpha_id")
    metrics = res_data.get("metrics", {})
    
    handoff_content = f"""# Handoff: 06_simulation
- **Simulation ID**: {sim_id}
- **Alpha ID**: {alpha_id}
- **Status**: success
- **Metrics**:
  - Sharpe: {metrics.get('sharpe')}
  - Fitness: {metrics.get('fitness')}
  - Turnover: {metrics.get('turnover')}
  - Returns: {metrics.get('returns')}
  - Drawdown: {metrics.get('drawdown')}
  - Margin: {metrics.get('margin')}
"""
    add_evidence(node_dir, "outputs/simulation_results.json", "write", reason="Final simulation results")
    finish_node_bundle(
        node_dir=node_dir,
        status="success",
        message=f"Simulation complete. Alpha ID: {alpha_id}",
        handoff_content=handoff_content
    )
    sys.exit(0)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run platform simulation node.")
    parser.add_argument("--run-dir", required=True, help="Path to pipeline run directory")
    args = parser.parse_args()
    
    run(args.run_dir)
