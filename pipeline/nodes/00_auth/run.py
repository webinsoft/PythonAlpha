import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from pipeline.shared.write_summary import init_node_bundle, finish_node_bundle, log_process, add_evidence
from pipeline.shared.wq_brain_client import authenticate_session, get_brain_credentials

def run(run_dir_str: str):
    run_dir = Path(run_dir_str).resolve()
    node_dir = run_dir / "nodes" / "00_auth"
    
    # Initialize the 7-piece bundle
    init_node_bundle(node_dir, input_data={})
    log_process(node_dir, "Starting WorldQuant Brain authentication test...")
    
    success, session, err_msg = authenticate_session()
    
    if success:
        email, _ = get_brain_credentials()
        auth_status = {
            "authenticated": True,
            "email": email,
            "authenticated_at": datetime.now().isoformat()
        }
        
        # Write business output
        output_file = node_dir / "outputs" / "auth_status.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(auth_status, f, indent=2)
            
        add_evidence(node_dir, "outputs/auth_status.json", "write", reason="Authentication status details")
        log_process(node_dir, f"Successfully authenticated as {email}.")
        
        finish_node_bundle(
            node_dir=node_dir,
            status="success",
            message="WorldQuant Brain authentication successful.",
            handoff_content=f"# Handoff: 00_auth\n\n- Authentication successful for {email}.\n- Downstream nodes can safely communicate with WorldQuant Brain API.\n"
        )
    else:
        log_process(node_dir, f"Authentication failed: {err_msg}")
        finish_node_bundle(
            node_dir=node_dir,
            status="failed",
            message=f"WorldQuant Brain authentication failed: {err_msg}",
            errors=[err_msg]
        )
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run authentication check node.")
    parser.add_argument("--run-dir", required=True, help="Path to pipeline run directory")
    args = parser.parse_args()
    
    run(args.run_dir)
