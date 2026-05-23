import ast
import json
import argparse
import sys
from pathlib import Path

def check_alphas_ast(code_content: str, fields_whitelist: dict) -> dict:
    errors = []
    warnings = []
    
    # 1. Parse AST
    try:
        tree = ast.parse(code_content)
    except SyntaxError as e:
        errors.append(f"Syntax/Compilation Error: {e.msg} at line {e.lineno}, col {e.offset}")
        return {"valid": False, "errors": errors, "warnings": warnings}
        
    # Get whitelist of fields
    whitelist = set()
    if fields_whitelist:
        for f in fields_whitelist.get("fields", []):
            whitelist.add(f.get("field_id"))
            
    # 2. Check Imports
    # Allowed imports are: numpy, pandas, math, typing, numpy.typing, and brain.alphas.alpha
    # Any other brain imports or non-approved libraries are prohibited.
    allowed_modules = {"numpy", "pandas", "math", "typing", "numpy.typing"}
    
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                root_module = name.name.split(".")[0]
                if root_module not in allowed_modules:
                    errors.append(f"Import Error: Module '{name.name}' is not allowed in submission file.")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root_module = node.module.split(".")[0]
                if root_module == "brain":
                    # Only allow 'from brain.alphas import alpha'
                    if node.module != "brain.alphas":
                        errors.append(f"Import Error: Importing from '{node.module}' is forbidden. Only 'brain.alphas' is allowed.")
                    else:
                        for name in node.names:
                            if name.name not in ("alpha", "*"):
                                errors.append(f"Import Error: Symbol '{name.name}' imported from brain.alphas is not allowed.")
                elif root_module not in allowed_modules:
                    errors.append(f"Import Error: Module '{node.module}' is not allowed in submission file.")

    # 3. Check Decorator & Function definitions
    alpha_decorated_funcs = []
    
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            # Check decorators
            is_alpha = False
            alpha_call_node = None
            
            for dec in node.decorator_list:
                # Check for @alpha or @alpha(...) or @alphas.alpha
                if isinstance(dec, ast.Name) and dec.id == "alpha":
                    is_alpha = True
                elif isinstance(dec, ast.Call):
                    func_node = dec.func
                    if isinstance(func_node, ast.Name) and func_node.id == "alpha":
                        is_alpha = True
                        alpha_call_node = dec
                    elif isinstance(func_node, ast.Attribute):
                        if isinstance(func_node.value, ast.Name) and func_node.value.id == "alphas" and func_node.attr == "alpha":
                            is_alpha = True
                            alpha_call_node = dec
                            
            if is_alpha:
                alpha_decorated_funcs.append((node, alpha_call_node))

    if len(alpha_decorated_funcs) == 0:
        errors.append("Decorator Error: No function decorated with @alpha was found.")
    elif len(alpha_decorated_funcs) > 1:
        errors.append(f"Decorator Error: Exactly one decorated function is allowed, found {len(alpha_decorated_funcs)}.")
    else:
        # Enforce exact two parameters named data and store
        func_node, call_node = alpha_decorated_funcs[0]
        
        args = func_node.args.args
        arg_names = [a.arg for a in args]
        
        if len(args) != 2:
            errors.append(f"Signature Error: The @alpha decorated function must accept exactly 2 arguments, found {len(args)}.")
        else:
            if arg_names != ["data", "store"]:
                warnings.append(f"Signature Warning: Function parameters are normally named ('data', 'store'), found {arg_names}.")
                
        # Validate @alpha decorator arguments
        if call_node:
            data_declared = False
            for kw in call_node.keywords:
                if kw.arg == "data":
                    data_declared = True
                    # Must be a list of strings
                    if isinstance(kw.value, ast.List):
                        for el in kw.value.elts:
                            if isinstance(el, ast.Constant) and isinstance(el.value, str):
                                field_name = el.value
                                if field_name == "universe":
                                    warnings.append("Decorator Warning: 'universe' should not be listed in data declaration, as data.universe is always available.")
                                elif whitelist and field_name not in whitelist:
                                    warnings.append(f"Decorator Warning: Field '{field_name}' is not in the whitelist of available fields ({list(whitelist)[:10]}...).")
                            else:
                                errors.append("Decorator Error: data list elements must be string literals.")
                    else:
                        errors.append("Decorator Error: 'data' parameter in @alpha must be a list.")
                        
                elif kw.arg == "store":
                    # Must be a list of strings or dicts
                    if isinstance(kw.value, ast.List):
                        for el in kw.value.elts:
                            if isinstance(el, ast.Dict):
                                # Check typed store dict keys and values
                                dict_keys = []
                                for k in el.keys:
                                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                                        dict_keys.append(k.value)
                                    else:
                                        dict_keys.append(None)
                                        
                                if "name" not in dict_keys or "dims" not in dict_keys or "extend" not in dict_keys:
                                    errors.append("Decorator Error: Typed store dictionary must contain 'name', 'dims', and 'extend' keys.")
                                else:
                                    # Check the 'extend' value
                                    extend_idx = dict_keys.index("extend")
                                    extend_val = el.values[extend_idx]
                                    
                                    # Must be a NumPy scalar constructor Call e.g. np.float32(np.nan)
                                    if not isinstance(extend_val, ast.Call):
                                        errors.append(
                                            "Decorator Error: Typed store parameter 'extend' must be an explicit NumPy scalar constructor call (e.g. np.float32(np.nan)), not a bare literal or attribute."
                                        )
                            elif isinstance(el, ast.Constant) and isinstance(el.value, str):
                                pass  # Untyped store element is fine
                            else:
                                errors.append("Decorator Error: store list elements must be strings or dicts.")
                    else:
                        errors.append("Decorator Error: 'store' parameter in @alpha must be a list.")
                        
            if not data_declared:
                warnings.append("Decorator Warning: 'data' list parameter not found in @alpha decorator.")
                
    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings
    }

def main():
    parser = argparse.ArgumentParser(description="Statically validate Python Alpha file.")
    parser.add_argument("--code", required=True, help="Path to alphas.py code file")
    parser.add_argument("--fields", required=True, help="Path to fields.json whitelist file")
    parser.add_argument("--output", required=True, help="Path to write validation_results.json")
    args = parser.parse_args()
    
    code_path = Path(args.code)
    fields_path = Path(args.fields)
    out_path = Path(args.output)
    
    if not code_path.is_file():
        print(f"Code file {code_path} not found.")
        sys.exit(1)
        
    try:
        with open(code_path, "r", encoding="utf-8") as f:
            code_content = f.read()
    except Exception as e:
        print(f"Failed to read code file: {e}")
        sys.exit(1)
        
    fields_whitelist = None
    if fields_path.is_file():
        try:
            with open(fields_path, "r", encoding="utf-8") as f:
                fields_whitelist = json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load fields whitelist {fields_path}: {e}")
            
    res = check_alphas_ast(code_content, fields_whitelist)
    
    # Save results
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=2, ensure_ascii=False)
        print(f"Validation finished. Valid: {res['valid']}. Errors: {len(res['errors'])}. Warnings: {len(res['warnings'])}.")
    except Exception as e:
        print(f"Failed to write output to {out_path}: {e}")
        sys.exit(1)
        
    if not res["valid"]:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    main()
