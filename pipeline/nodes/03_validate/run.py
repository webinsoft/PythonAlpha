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
from scripts.wq_ast_validator import check_alphas_ast

def run(run_dir_str: str):
    run_dir = Path(run_dir_str).resolve()
    node_dir = run_dir / "nodes" / "03_validate"
    
    # Locate inputs
    code_path = run_dir / "nodes" / "02_codegen" / "outputs" / "alphas.py"
    fields_path = run_dir / "nodes" / "01_fastexpr_parser" / "outputs" / "fields.json"
    
    # Initialize bundle
    init_node_bundle(node_dir, input_data={
        "upstream_artifacts": {
            "alphas.py": str(code_path),
            "fields.json": str(fields_path)
        }
    })
    
    log_process(node_dir, "Starting static AST validation of alphas.py...")
    
    if not code_path.is_file():
        err_msg = f"Generated code file not found at: {code_path}"
        log_process(node_dir, err_msg)
        finish_node_bundle(node_dir, "failed", err_msg, errors=[err_msg])
        sys.exit(1)
        
    fields_whitelist = None
    if fields_path.is_file():
        try:
            with open(fields_path, "r", encoding="utf-8") as f:
                fields_whitelist = json.load(f)
        except Exception as e:
            log_process(node_dir, f"Warning: Failed to load fields whitelist: {e}")
            
    # Perform AST checks
    try:
        res = check_alphas_ast(code_path.read_text(encoding="utf-8"), fields_whitelist)
    except Exception as e:
        err_msg = f"Exceptions raised during AST validation process: {e}"
        log_process(node_dir, err_msg)
        finish_node_bundle(node_dir, "failed", err_msg, errors=[err_msg])
        sys.exit(1)
        
    # Write output to outputs/validation_results.json
    out_dir = node_dir / "outputs"
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / "validation_results.json"
    
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2, ensure_ascii=False)
    add_evidence(node_dir, "outputs/validation_results.json", "write", reason="AST validation results JSON")
    
    valid = res.get("valid", False)
    errors = res.get("errors", [])
    warnings = res.get("warnings", [])
    
    status = "success" if valid else "failed"
    msg = f"AST validation {'passed' if valid else 'failed'}. Errors: {len(errors)}, Warnings: {len(warnings)}."
    log_process(node_dir, msg)
    
    # Handoff content
    error_list = "\n".join(f"  - {e}" for e in errors) or "  - No errors."
    warning_list = "\n".join(f"  - {w}" for w in warnings) or "  - No warnings."
    handoff_content = f"""# Handoff: 05_validate

- **Valid**: {valid}
- **Errors**:
{error_list}
- **Warnings**:
{warning_list}
"""
    
    finish_node_bundle(
        node_dir=node_dir,
        status=status,
        message=msg,
        errors=errors,
        warnings=warnings,
        handoff_content=handoff_content
    )
    
    # Crucial: Exit with 0 so run_node can complete, read validation_results.json, and route correctly!
    sys.exit(0)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run AST validation node.")
    parser.add_argument("--run-dir", required=True, help="Path to pipeline run directory")
    args = parser.parse_args()
    
    run(args.run_dir)
