import os
import sys
import json
import argparse
import shutil
from pathlib import Path

# Add project root and node dir to sys.path
node_dir = Path(__file__).resolve().parent
project_root = node_dir.parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(node_dir))

from pipeline.shared.write_summary import init_node_bundle, finish_node_bundle, log_process, add_evidence
from scripts.format_validation_feedback import format_validation_feedback

def run(run_dir_str: str):
    run_dir = Path(run_dir_str).resolve()
    node_dir = run_dir / "nodes" / "02_codegen"
    
    # Locate inputs
    spec_path = run_dir / "nodes" / "01_fastexpr_parser" / "outputs" / "strategy_spec.json"
    cookbook_path = project_root / "references" / "wq_brain_cookbook.md"
    
    # Resolve iteration count to set up attempt_N
    # Standard task init creates the directory and writes node_input.json
    node_input_path = node_dir / "node_input.json"
    iteration = 0
    if node_input_path.is_file():
        try:
            with open(node_input_path, "r", encoding="utf-8") as f:
                node_input = json.load(f)
                iteration = node_input.get("iteration", 0)
        except Exception:
            pass
            
    # Set up attempt directory
    attempt_dir = node_dir / f"attempt_{iteration}"
    attempt_outputs_dir = attempt_dir / "outputs"
    attempt_outputs_dir.mkdir(parents=True, exist_ok=True)
    
    # Copy node_input.json and initialize logs inside attempt_N
    shutil.copy(node_input_path, attempt_dir / "node_input.json")
    init_node_bundle(attempt_dir, input_data={"run_id": run_dir.name, "iteration": iteration})
    
    log_process(node_dir, f"Starting code generation, iteration/attempt {iteration}...")
    log_process(attempt_dir, f"Starting code generation attempt {iteration}...")
    
    if not spec_path.is_file() or not cookbook_path.is_file():
        err_msg = "Required inputs strategy_spec.json or wq_brain_cookbook.md missing."
        log_process(node_dir, err_msg)
        log_process(attempt_dir, err_msg)
        finish_node_bundle(attempt_dir, "failed", err_msg, errors=[err_msg])
        finish_node_bundle(node_dir, "failed", err_msg, errors=[err_msg])
        sys.exit(1)
        
    try:
        with open(spec_path, "r", encoding="utf-8") as f:
            spec_dict = json.load(f)
        with open(cookbook_path, "r", encoding="utf-8") as f:
            cookbook_content = f.read()
    except Exception as e:
        err_msg = f"Failed to read input files: {e}"
        log_process(node_dir, err_msg)
        log_process(attempt_dir, err_msg)
        finish_node_bundle(attempt_dir, "failed", err_msg, errors=[err_msg])
        finish_node_bundle(node_dir, "failed", err_msg, errors=[err_msg])
        sys.exit(1)
        
    # Read optional validation feedback
    validation_feedback = ""
    val_res_path = run_dir / "nodes" / "03_validate" / "outputs" / "validation_results.json"
    if val_res_path.is_file() and iteration > 0:
        try:
            with open(val_res_path, "r", encoding="utf-8") as f:
                val_json = json.load(f)
            
            # Find the previous code from the previous attempt
            prev_attempt_dir = node_dir / f"attempt_{iteration - 1}"
            prev_code_path = prev_attempt_dir / "outputs" / "alphas.py"
            if prev_code_path.is_file():
                with open(prev_code_path, "r", encoding="utf-8") as f:
                    prev_code = f.read()
                validation_feedback = format_validation_feedback(prev_code, val_json)
                log_process(attempt_dir, "Loaded previous validation feedback for self-healing.")
        except Exception as e:
            log_process(attempt_dir, f"Warning: failed to load validation feedback: {e}")
            
    # Read optional diagnosis feedback
    diagnosis_feedback = ""
    diag_res_path = run_dir / "nodes" / "05_diagnosis" / "outputs" / "diagnosis.json"
    if diag_res_path.is_file() and iteration > 0:
        try:
            with open(diag_res_path, "r", encoding="utf-8") as f:
                diag_json = json.load(f)
            diag_feedback_text = diag_json.get("feedback", "")
            if diag_feedback_text:
                prev_attempt_dir = node_dir / f"attempt_{iteration - 1}"
                prev_code_path = prev_attempt_dir / "outputs" / "alphas.py"
                prev_code = ""
                if prev_code_path.is_file():
                    with open(prev_code_path, "r", encoding="utf-8") as f:
                        prev_code = f.read()
                diagnosis_feedback = f"""
>>> PREVIOUS CODE GENERATION ATTEMPT <<<
```python
{prev_code}
```

>>> DIAGNOSIS AND PERFORMANCE FEEDBACK <<<
Feedback: {diag_feedback_text}
Metrics: {json.dumps(diag_json.get('metrics', {}), indent=2)}

CRITICAL: Carefully analyze the diagnosis feedback. Modify the alpha implementation to correct the issues described above and improve performance/validity.
"""
                log_process(attempt_dir, "Loaded previous diagnosis feedback for self-healing.")
        except Exception as e:
            log_process(attempt_dir, f"Warning: failed to load diagnosis feedback: {e}")

    # Path to alphas.py for this attempt
    attempt_code_path = attempt_outputs_dir / "alphas.py"
    primary_code_path = node_dir / "outputs" / "alphas.py"
    
    if not attempt_code_path.is_file():
        log_process(attempt_dir, "alphas.py not found for this attempt. Writing agent_prompt.txt and blocking node...")
        log_process(node_dir, f"alphas.py not found in attempt_{iteration}. Writing agent_prompt.txt and blocking node...")
        
        prompt_file = attempt_outputs_dir / "agent_prompt.txt"
        
        # Build prompt
        prompt = f"""【Agent Task: Python Alpha Code Generation】
You are running in a fully Agentic mode (no LLM API calls). The pipeline has reached the Code Generation stage (Attempt {iteration}).
Your task is to write a fully-compliant WorldQuant Brain Python Alpha script based on the following strategy specification and cookbook rules.

=== STRATEGY SPECIFICATION (StrategySpec) ===
{json.dumps(spec_dict, indent=2, ensure_ascii=False)}

=== WQ BRAIN PYTHON ALPHA COOKBOOK ===
{cookbook_content}
"""

        if validation_feedback:
            prompt += f"\n=== UPSTREAM VALIDATION FEEDBACK ===\n{validation_feedback}\n"
        if diagnosis_feedback:
            prompt += f"\n=== UPSTREAM DIAGNOSIS FEEDBACK ===\n{diagnosis_feedback}\n"

        prompt += f"""
=== TASK INSTRUCTIONS ===
1. Analyze the strategy spec and any feedback from upstream.
2. Implement exactly one function decorated with `@alpha(...)`. It must accept `data` and `store` as parameters and return a `float32` numpy array of shape `[n_instruments]`.
3. Only import:
   `from brain.alphas import alpha`
   `import numpy as np`
   `import numpy.typing as npt`
   DO NOT import any other `brain` packages.
4. Ensure all returns are adjusted by `adjfactor` if they are raw prices or fundamental per-share indicators.
5. Save your final executable Python code EXACTLY to:
File Path: {attempt_code_path}

Once you have written this file, please rerun the pipeline (e.g. using resume_run).
"""
        with open(prompt_file, "w", encoding="utf-8") as f:
            f.write(prompt)
            
        add_evidence(attempt_dir, "outputs/agent_prompt.txt", "write", reason="Prompt for manual Agent code generation")
        
        # Copy to primary outputs/ for visibility
        primary_outputs_dir = node_dir / "outputs"
        primary_outputs_dir.mkdir(exist_ok=True)
        shutil.copy(prompt_file, primary_outputs_dir / "agent_prompt.txt")
        add_evidence(node_dir, "outputs/agent_prompt.txt", "write", reason="Primary outputs copy of agent prompt")
        
        finish_node_bundle(
            node_dir=attempt_dir,
            status="blocked",
            message=f"alphas.py not found in attempt_{iteration}. Agent prompt generated.",
            handoff_content=f"# Handoff: 04_codegen attempt_{iteration} (Blocked)\n\n- Python Alpha code generation is blocked. Please inspect `outputs/agent_prompt.txt`.\n"
        )
        finish_node_bundle(
            node_dir=node_dir,
            status="blocked",
            message=f"alphas.py not found in attempt_{iteration}. Agent prompt generated.",
            handoff_content=f"# Handoff: 04_codegen (Blocked)\n\n- Python Alpha code generation is blocked. Please inspect `outputs/agent_prompt.txt`.\n"
        )
        print(f"🛑 Node 04_codegen blocked on attempt {iteration}. Agent prompt written.")
        sys.exit(0)
        
    # If attempt_code_path is present, copy it to the primary outputs and complete
    log_process(attempt_dir, f"alphas.py found for attempt {iteration}. Promoting to success.")
    log_process(node_dir, f"alphas.py found in attempt_{iteration}. Copying to primary outputs.")
    
    with open(attempt_code_path, "r", encoding="utf-8") as f:
        code_output = f.read()
        
    # Ensure primary outputs directory exists
    primary_outputs_dir = node_dir / "outputs"
    primary_outputs_dir.mkdir(exist_ok=True)
    
    with open(primary_code_path, "w", encoding="utf-8") as f:
        f.write(code_output)
        
    add_evidence(attempt_dir, "outputs/alphas.py", "write", reason="Generated alphas.py source code")
    add_evidence(node_dir, "outputs/alphas.py", "write", reason="Generated alphas.py copied to primary outputs")
    
    # Complete bundles
    msg = f"Successfully generated alphas.py under attempt_{iteration}"
    finish_node_bundle(
        node_dir=attempt_dir,
        status="success",
        message=msg,
        handoff_content=f"# Handoff: 04_codegen attempt_{iteration}\n\n- Python Alpha generated successfully.\n"
    )
    finish_node_bundle(
        node_dir=node_dir,
        status="success",
        message=msg,
        handoff_content=f"# Handoff: 04_codegen\n\n- Python Alpha code generated successfully in `outputs/alphas.py`.\n- Attempt version: {iteration}.\n"
    )

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Python Alpha generation node.")
    parser.add_argument("--run-dir", required=True, help="Path to pipeline run directory")
    args = parser.parse_args()
    
    run(args.run_dir)
