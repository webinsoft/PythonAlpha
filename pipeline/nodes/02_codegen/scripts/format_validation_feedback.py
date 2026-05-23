def format_validation_feedback(prev_code: str, feedback_json: dict) -> str:
    """Format previous code and errors/warnings into a prompt block for LLM self-healing."""
    errors = feedback_json.get("errors", [])
    warnings = feedback_json.get("warnings", [])
    
    error_list = "\n".join(f"  - {e}" for e in errors) or "  - No critical errors."
    warning_list = "\n".join(f"  - {w}" for w in warnings) or "  - No warnings."
    
    return f"""
>>> PREVIOUS CODE GENERATION ATTEMPT <<<
```python
{prev_code}
```

>>> VALIDATION FEEDBACK / ERRORS <<<
Errors:
{error_list}

Warnings:
{warning_list}

CRITICAL: Carefully analyze the errors above. Make sure you fix the root cause of these errors. Do not repeat the same mistakes. Apply the rules in the cookbook.
"""
