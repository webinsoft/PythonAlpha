def build_wq_field_context(fields_json: dict) -> str:
    """Format available WorldQuant Brain data fields to inject into LLM prompts as constraints."""
    lines = ["\n>>> AVAILABLE WQ BRAIN DATA FIELDS (use ONLY these fields) <<<"]
    fields = fields_json.get("fields", [])
    for f in fields:
        dataset_id = f.get("dataset_id", "")
        field_id = f.get("field_id", "")
        field_type = f.get("type", "unknown")
        lines.append(f"  - {dataset_id}.{field_id} (type={field_type})")
    lines.append("CRITICAL: Map paper indicators to ONLY the fields listed above.")
    return "\n".join(lines)
