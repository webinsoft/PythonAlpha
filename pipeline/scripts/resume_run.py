"""
Resume (or start) a pipeline run from its current state.

Delegates all orchestration to PipelineAgent — the script is now a thin
CLI wrapper that instantiates the PipelineAgent and calls run().

Usage:
    python pipeline/scripts/resume_run.py --run-dir pipeline_runs/run_YYYYMMDD_HHMMSS [--mode subprocess|agent]
"""

import sys
import argparse
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))

from pipeline.pipeline_agent import PipelineAgent


def resume(run_dir_str: str, dispatch_mode: str = "subprocess") -> bool:
    """Resume pipeline execution via PipelineAgent orchestrator.

    Key fix over the old while-loop approach:
    - PipelineAgent._agent_has_intervened() checks for Agent output files
      BEFORE resetting blocked -> running, preventing the dead-loop where
      resume_run.py was called 300+ times in 25 seconds without Agent input.

    Parameters
    ----------
    run_dir_str : str
        Path to the pipeline run directory.
    dispatch_mode : str
        "subprocess" (default) — uses subprocess.run on node scripts.
        "agent" (future) — would use agent_open for NodeAgent dispatch.

    Returns
    -------
    bool
        True if pipeline reached a terminal state or is blocked (waiting for Agent).
        False if pipeline failed or exhausted.
    """
    try:
        agent = PipelineAgent(run_dir_str)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        return False

    status = agent.status()
    print(f"Pipeline state: current_node={status['current_node']}, "
          f"status={status['status']}, "
          f"completed={status['completed_nodes']}, "
          f"iteration={status['iteration_count']}")

    return agent.run(dispatch_mode=dispatch_mode)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Resume pipeline run via PipelineAgent orchestrator."
    )
    parser.add_argument(
        "--run-dir", required=True,
        help="Path to pipeline run directory"
    )
    parser.add_argument(
        "--mode", default="subprocess", choices=["subprocess", "agent"],
        help="Dispatch mode: subprocess (default) or agent (requires Tool Proxy)"
    )
    args = parser.parse_args()

    success = resume(args.run_dir, dispatch_mode=args.mode)
    if not success:
        sys.exit(1)
