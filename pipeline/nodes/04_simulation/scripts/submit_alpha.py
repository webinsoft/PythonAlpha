import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

# Add project root and local node dir to sys.path
node_dir = Path(__file__).resolve().parent.parent
project_root = node_dir.parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(node_dir))

from pipeline.shared.wq_brain_client import create_session, create_simulation as wq_submit
from scripts.update_resume_state import update_resume_state


def submit_alpha(
    node_dir_str: str,
    code_path_str: str,
    region: str,
    universe: str,
    delay: int,
    settings: dict = None,
):
    node_dir = Path(node_dir_str).resolve()
    code_path = Path(code_path_str).resolve()

    if not code_path.is_file():
        raise FileNotFoundError(
            f"Python Alpha code file not found at: {code_path}"
        )

    code_content = code_path.read_text(encoding="utf-8")

    # 1. Authenticate
    print("Logging into WorldQuant Brain platform...")
    sm = create_session()

    # 2. Build payload (Python Alpha requirements)
    payload_settings = {
        "instrumentType": "EQUITY",
        "language": "PYTHON",
        "region": "USA" if region == "GLB" else region,
        "delay": delay,
        "universe": "TOP3000" if universe == "MINVOL1M" else universe,
        "neutralization": "SUBINDUSTRY",
        "decay": 5,
        "truncation": 0.01,
        "pasteurization": "ON",
        "visualization": False,
        "lookback": 5,
        "max_position": "OFF",
        "max_trade": "OFF",
    }
    if settings:
        payload_settings.update(settings)

    payload = {
        "type": "REGULAR",
        "regular": code_content,
        "settings": payload_settings,
    }

    print(
        f"Submitting Python Alpha simulation. "
        f"Region: {region}, Universe: {universe}, Delay: {delay}..."
    )

    # 3. Submit with dead-loop 429 retry (handled by shared module)
    progress_url = wq_submit(sm, payload)

    simulation_id = progress_url.split("/")[-1]
    print(f"Simulation task submitted successfully. ID: {simulation_id}")

    # 4. Save submitted_batch.json
    submitted_batch = {
        "simulation_id": simulation_id,
        "progress_url": progress_url,
        "region": region,
        "universe": universe,
        "delay": delay,
        "submitted_at": datetime.now().isoformat(),
        "payload": {
            "settings": payload_settings,
            "code_snippet": (
                code_content[:200] + "..."
                if len(code_content) > 200
                else code_content
            ),
        },
    }
    with open(node_dir / "outputs" / "submitted_batch.json", "w", encoding="utf-8") as f:
        json.dump(submitted_batch, f, indent=2, ensure_ascii=False)

    # 5. Initialize resume_state.json
    update_resume_state(
        node_dir_str=str(node_dir),
        status="submitted",
        simulation_id=simulation_id,
    )

    return simulation_id


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Submit Python Alpha to WorldQuant Brain."
    )
    parser.add_argument("--node-dir", required=True)
    parser.add_argument("--code-path", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--universe", required=True)
    parser.add_argument("--delay", type=int, required=True)
    args = parser.parse_args()

    submit_alpha(
        node_dir_str=args.node_dir,
        code_path_str=args.code_path,
        region=args.region,
        universe=args.universe,
        delay=args.delay,
    )
