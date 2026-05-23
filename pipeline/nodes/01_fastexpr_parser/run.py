import sys
import json
import argparse
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from pipeline.shared.write_summary import init_node_bundle, finish_node_bundle, log_process, add_evidence
from pipeline.shared.wq_brain_client import create_session, fetch_alpha


def run(run_dir_str: str):
    run_dir = Path(run_dir_str).resolve()
    node_dir = run_dir / "nodes" / "01_fastexpr_parser"

    node_input_path = node_dir / "node_input.json"
    spec_path = node_dir / "outputs" / "strategy_spec.json"
    prompt_path = node_dir / "outputs" / "agent_prompt.txt"

    if not node_input_path.is_file():
        print(f"Error: node_input.json not found at {node_input_path}")
        sys.exit(1)

    with open(node_input_path, "r", encoding="utf-8") as f:
        node_input = json.load(f)

    init_node_bundle(node_dir, input_data=node_input)
    log_process(node_dir, "FastExpr Parser node starting...")

    if spec_path.is_file():
        try:
            with open(spec_path, "r", encoding="utf-8") as f:
                spec = json.load(f)
            log_process(node_dir, f"Loaded strategy_spec.json: {spec.get('strategy_name', 'unknown')}")
            add_evidence(node_dir, "outputs/strategy_spec.json", "read", reason="Agent wrote strategy spec")
            finish_node_bundle(node_dir, "success", "strategy_spec.json ready.")
            sys.exit(0)
        except Exception as e:
            log_process(node_dir, f"Failed to read strategy_spec.json: {e}")

    fastexpr = node_input.get("fastexpr", "").strip()
    alpha_id = node_input.get("alpha_id", "").strip()

    if not fastexpr and not alpha_id:
        log_process(node_dir, "Neither 'fastexpr' nor 'alpha_id' provided in node_input.json.")
        finish_node_bundle(node_dir, "failed",
            "Provide either 'fastexpr' (FastExpr string) or 'alpha_id' (WQ Brain alpha ID).")
        sys.exit(1)

    if alpha_id:
        log_process(node_dir, f"Fetching alpha {alpha_id} from WQ Brain...")
        try:
            sm = create_session()
            alpha = fetch_alpha(sm, alpha_id)
            add_evidence(node_dir, f"alpha:{alpha_id}", "read", reason="Fetched alpha detail from WQ Brain")
        except Exception as e:
            log_process(node_dir, f"Failed to fetch alpha {alpha_id}: {e}")
            finish_node_bundle(node_dir, "failed", f"WQ Brain API error: {e}")
            sys.exit(1)

        regular = alpha.get("regular", {})
        fastexpr = regular.get("code", "")
        language = alpha.get("settings", {}).get("language", "UNKNOWN")

        if not fastexpr:
            log_process(node_dir, "Alpha has no regular.code field.")
            finish_node_bundle(node_dir, "failed", "No expression found in alpha.")
            sys.exit(1)

        if language != "FASTEXPR":
            log_process(node_dir, f"Warning: alpha language is '{language}', not 'FASTEXPR'. "
                                  f"The expression may be Python code or another format.")

        log_process(node_dir, f"Alpha language: {language}, expression: {fastexpr[:120]}...")

    log_process(node_dir, "Writing agent prompt for FastExpr -> strategy_spec.json conversion...")

    fields_in_expr = sorted(set(
        w for w in fastexpr.replace("(", " ").replace(")", " ").replace(",", " ").replace("/", " ").split()
        if w and not w.startswith("ts_") and not w.startswith("group_")
        and w not in ("rank", "zscore", "scale", "winsorize", "densify", "std", "std_dev",
                      "correlation", "covariance", "delay", "signedpower")
    ))

    prompt = f"""【Agent Task: FastExpr -> StrategySpec 转换】

你在一个完全 Agentic 模式下运行。流水线已到达 FastExpr 解析阶段。
你的任务是根据下面的 FastExpr 表达式分析其结构，编写对应的 strategy_spec.json 文件。

=== FastExpr 表达式 ===
{fastexpr}

=== 任务说明 ===
1. 分析该 FastExpr 表达式的操作符和参数结构。
2. 将每个子操作拆解为 logic_pipeline 中的一步。
3. 识别所有用到的数据字段，填入 indicators[]。
4. 将结果保存到以下路径的 JSON 文件中：
   File Path: {spec_path}

=== StrategySpec JSON Schema ===
{{
  "strategy_name": "策略名称",
  "strategy_type": "cross_sectional_mean_reversion",
  "asset_class": "EQUITY",
  "indicators": [
    {{
      "indicator_id": "唯一标识符",
      "name": "指标名称",
      "category": "volatility",
      "formula": "计算公式",
      "inputs": ["字段1", "字段2"],
      "parameters": {{}}
    }}
  ],
  "logic_pipeline": [
    {{
      "step_id": "步骤ID",
      "description": "步骤描述",
      "function": "操作函数名",
      "scope": "element_wise | cross_sectional | time_series | group_wise",
      "expression": "子表达式",
      "output": "输出变量名",
      "parameters": {{}}
    }}
  ],
  "execution_plan": [
    {{
      "trigger": "daily",
      "action": "long/short 逻辑说明",
      "position_sizing": "仓位策略"
    }}
  ]
}}

=== 常见 FastExpr 操作符映射 ===
| FastExpr       | Type                  | scope           |
|----------------|----------------------|-----------------|
| rank(x)        | 截面排序(0-1)        | cross_sectional |
| zscore(x)      | 截面标准化           | cross_sectional |
| scale(x)       | L1范数缩放           | cross_sectional |
| ts_mean(x,n)   | n期滚动均值          | time_series     |
| ts_std(x,n)    | n期滚动标准差        | time_series     |
| ts_delta(x,n)  | n期差分              | time_series     |
| ts_sum(x,n)    | n期滚动和            | time_series     |
| ts_backfill(x,n)| 前向填充NaN最长n期  | time_series     |
| winsorize(x,std=n)| 截面clip均值±n*std | cross_sectional |
| group_rank(x,group)| 组内排序          | group_wise      |
| group_neutralize(x,group)| 组内减均值   | group_wise      |
| densify(x)     | 分组标签引用         | group_wise      |
| correlation(x,y,n)| n期滚动相关系数    | time_series     |
| delay(x,n)     | 延迟n期              | time_series     |
| signedpower(x,n)| 保号幂变换          | element_wise    |

=== 检测到的数据字段 ===
{json.dumps(fields_in_expr, indent=2)}

请按上述 schema 将 strategy_spec.json 写入指定路径后，重新运行流水线。
"""

    outputs_dir = node_dir / "outputs"
    outputs_dir.mkdir(exist_ok=True)

    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(prompt)
    add_evidence(node_dir, "outputs/agent_prompt.txt", "write",
                 reason="Agent prompt for FastExpr -> strategy_spec.json conversion")

    finish_node_bundle(
        node_dir=node_dir,
        status="blocked",
        message="Waiting for agent to write strategy_spec.json.",
        handoff_content=f"# Handoff: 01_fastexpr_parser\n\n"
                       f"- FastExpr parser blocked. Inspect outputs/agent_prompt.txt\n"
                       f"- Write outputs/strategy_spec.json following the schema.\n"
    )
    print(f"\U0001f6d1 Node 01_fastexpr_parser blocked. Prompt: {prompt_path}")
    sys.exit(0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FastExpr Parser node")
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    run(args.run_dir)
