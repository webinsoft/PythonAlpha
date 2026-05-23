import json
from pathlib import Path

def archive_best_k(
    node_dir: Path,
    iteration: int,
    code: str,
    sim_results: dict,
    best_k_top: int = 3
) -> list:
    """Archive the current attempt and update the survivors.json file with the top K best alpha candidates.
    
    Also writes a history.json file with all attempts.
    """
    outputs_dir = node_dir / "outputs"
    outputs_dir.mkdir(exist_ok=True)
    
    history_path = outputs_dir / "history.json"
    survivors_path = outputs_dir / "survivors.json"
    
    # 1. Load existing history
    history = []
    if history_path.is_file():
        try:
            with open(history_path, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            pass
            
    # 2. Add current attempt to history (replace if iteration already exists)
    current_attempt = {
        "iteration": iteration,
        "code": code,
        "alpha_id": sim_results.get("alpha_id", "unknown"),
        "status": sim_results.get("status"),
        "metrics": sim_results.get("metrics"),
        "error": sim_results.get("error")
    }
    
    history = [h for h in history if h.get("iteration") != iteration]
    history.append(current_attempt)
    
    # Save full history
    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)
        
    # 3. Filter successful candidates for survivors
    successful_candidates = [
        h for h in history 
        if h.get("status") == "success" and h.get("metrics") is not None
    ]
    
    # Sort successful candidates by Sharpe ratio descending, then Fitness descending
    def get_sort_key(c):
        metrics = c.get("metrics", {})
        sharpe = metrics.get("sharpe", 0.0) or 0.0
        fitness = metrics.get("fitness", 0.0) or 0.0
        return (sharpe, fitness)
        
    successful_candidates.sort(key=get_sort_key, reverse=True)
    
    # Slice to top K
    survivors = successful_candidates[:best_k_top]
    
    # Save survivors
    with open(survivors_path, "w", encoding="utf-8") as f:
        json.dump(survivors, f, indent=2, ensure_ascii=False)
        
    print(f"Archived {len(survivors)} survivor(s) to survivors.json (top {best_k_top} count).")
    return survivors
