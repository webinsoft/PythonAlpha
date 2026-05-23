import json
from pathlib import Path
from datetime import datetime

def init_node_bundle(node_dir: Path, input_data: dict):
    """Initialize the 7-piece bundle structure for a node."""
    node_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir = node_dir / "outputs"
    outputs_dir.mkdir(exist_ok=True)

    # 1. Write node_input.json
    with open(node_dir / "node_input.json", "w", encoding="utf-8") as f:
        json.dump(input_data, f, indent=2, ensure_ascii=False)

    # 2. Write process_log.md (first write rule)
    start_time = datetime.now().isoformat()
    log_content = f"# Process Log: {node_dir.name}\n\n- **Initialized at**: {start_time}\n"
    with open(node_dir / "process_log.md", "w", encoding="utf-8") as f:
        f.write(log_content)

    # 3. Write validation_report.json (first write rule: status="started")
    val_report = {
        "status": "started",
        "started_at": start_time,
        "errors": [],
        "warnings": []
    }
    with open(node_dir / "validation_report.json", "w", encoding="utf-8") as f:
        json.dump(val_report, f, indent=2, ensure_ascii=False)

    # 4. Write initial empty evidence_index.json
    evidence = {
        "node": node_dir.name,
        "created_at": start_time,
        "files": []
    }
    with open(node_dir / "evidence_index.json", "w", encoding="utf-8") as f:
        json.dump(evidence, f, indent=2, ensure_ascii=False)

    # Register initial files as evidence
    add_evidence(node_dir, "node_input.json", "write", reason="Input configuration")
    add_evidence(node_dir, "process_log.md", "write", reason="Initial process log")
    add_evidence(node_dir, "validation_report.json", "write", reason="Initial validation report")
    add_evidence(node_dir, "evidence_index.json", "write", reason="Initial evidence registry")

def add_evidence(node_dir: Path, file_path: str, action: str, line_count: int = None, reason: str = ""):
    """Record a file read/write operation inside evidence_index.json."""
    evidence_path = node_dir / "evidence_index.json"
    if not evidence_path.exists():
        return

    try:
        with open(evidence_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return

    # Normalise paths relative to node_dir if they are subpaths
    try:
        full_path = Path(file_path)
        if full_path.is_absolute() and node_dir.resolve() in full_path.resolve().parents:
            rel_path = str(full_path.relative_to(node_dir))
        else:
            rel_path = file_path
    except Exception:
        rel_path = file_path

    # Try to calculate line count if not provided and file exists
    if line_count is None:
        try:
            abs_p = node_dir / rel_path if not Path(rel_path).is_absolute() else Path(rel_path)
            if abs_p.is_file():
                with open(abs_p, "rb") as f:
                    line_count = sum(1 for _ in f)
        except Exception:
            pass

    record = {
        "path": rel_path,
        "action": action,
        "timestamp": datetime.now().isoformat(),
        "line_count": line_count,
        "reason": reason
    }
    data["files"].append(record)

    with open(evidence_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def log_process(node_dir: Path, message: str):
    """Append a message to process_log.md."""
    log_path = node_dir / "process_log.md"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"- [{timestamp}] {message}\n")

def finish_node_bundle(
    node_dir: Path,
    status: str,
    message: str,
    errors: list = None,
    warnings: list = None,
    handoff_content: str = ""
):
    """Complete the 7-piece bundle and write final status/reports."""
    errors = errors or []
    warnings = warnings or []
    end_time = datetime.now().isoformat()

    # 1. Update validation_report.json to final status
    val_path = node_dir / "validation_report.json"
    try:
        with open(val_path, "r", encoding="utf-8") as f:
            val_report = json.load(f)
    except Exception:
        val_report = {"started_at": end_time}

    val_report.update({
        "status": "passed" if status == "success" else "failed",
        "completed_at": end_time,
        "errors": errors,
        "warnings": warnings
    })
    with open(val_path, "w", encoding="utf-8") as f:
        json.dump(val_report, f, indent=2, ensure_ascii=False)

    # 2. Write node_result.json
    result = {
        "status": status,
        "message": message,
        "completed_at": end_time,
        "errors_count": len(errors),
        "warnings_count": len(warnings)
    }
    with open(node_dir / "node_result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    # 3. Write handoff.md
    with open(node_dir / "handoff.md", "w", encoding="utf-8") as f:
        f.write(handoff_content or f"# Handoff: {node_dir.name}\n\n- **Status**: {status}\n- **Message**: {message}\n")

    # Log process completion
    log_process(node_dir, f"Execution completed with status: {status}. Message: {message}")
    
    # Add final files to evidence
    add_evidence(node_dir, "node_result.json", "write", reason="Final result status")
    add_evidence(node_dir, "handoff.md", "write", reason="Downstream handoff notes")
