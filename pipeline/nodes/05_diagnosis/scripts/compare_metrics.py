import json
from typing import Dict, List, Tuple

def compare_metrics(metrics: Dict[str, float], thresholds: Dict[str, float]) -> Tuple[bool, List[str]]:
    """Compare simulation metrics against target thresholds.
    
    Returns:
        (passed, failed_reasons)
    """
    failed_reasons = []
    
    min_sharpe = thresholds.get("min_sharpe", 1.0)
    min_fitness = thresholds.get("min_fitness", 0.5)
    max_turnover = thresholds.get("max_turnover", 0.5)
    
    sharpe = metrics.get("sharpe")
    fitness = metrics.get("fitness")
    turnover = metrics.get("turnover")
    
    # Check Sharpe
    if sharpe is None:
        failed_reasons.append("Sharpe ratio is missing.")
    elif sharpe < min_sharpe:
        failed_reasons.append(f"Sharpe ratio {sharpe:.4f} is below the threshold of {min_sharpe:.4f}.")
        
    # Check Fitness
    if fitness is None:
        failed_reasons.append("Fitness is missing.")
    elif fitness < min_fitness:
        failed_reasons.append(f"Fitness {fitness:.4f} is below the threshold of {min_fitness:.4f}.")
        
    # Check Turnover
    if turnover is None:
        failed_reasons.append("Turnover is missing.")
    elif turnover > max_turnover:
        failed_reasons.append(f"Turnover {turnover:.4f} is above the threshold of {max_turnover:.4f}.")
        
    passed = len(failed_reasons) == 0
    return passed, failed_reasons
