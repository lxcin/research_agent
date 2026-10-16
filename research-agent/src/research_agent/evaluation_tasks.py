"""Small, executable workspace tasks with deterministic outcome oracles."""
import json
import os
import math
from research_agent.evaluation import EvaluationSpec, EvaluationResult
from research_agent.task_evaluation import TaskCase, workspace_path


def _file_text(data, name):
    path = workspace_path(data.artifacts["workspace"], name)
    if not path.is_file():
        return None
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Oracle file exceeds 1 MiB limit")
    return path.read_bytes().decode("utf-8")


def _json_equal(actual, expected):
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return math.isfinite(actual) and math.isfinite(expected) and actual == expected
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(_json_equal(actual[key], expected[key]) for key in actual)
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(_json_equal(a, b) for a, b in zip(actual, expected))
    return actual == expected


def check_json(data):
    reference = data.artifacts["reference"]
    text = _file_text(data, reference["output"])
    if text is None:
        return EvaluationResult("fail", "Required JSON artifact missing")
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        return EvaluationResult("fail", "Output is not valid JSON")
    # JSON equality must distinguish true from 1, unlike ordinary Python equality.
    matched = _json_equal(value, reference["expected"])
    return EvaluationResult("pass" if matched else "fail", "JSON artifact compared with reference")


def check_files(data):
    for name, expected in data.artifacts["reference"]["files"].items():
        if _file_text(data, name) != expected:
            return EvaluationResult("fail", f"File content differs: {name}")
    return EvaluationResult("pass", "Required files match; protected content preserved")


def check_missing_evidence(data):
    reference = data.artifacts["reference"]
    reads = {event["data"].get("id") for event in data.events
             if event.get("type") == "tool_start" and event.get("data", {}).get("name") == "file_read"
             and event["data"].get("input", {}).get("path") == reference["missing_path"]}
    failed_read = any(event.get("type") == "tool_end" and
                      event.get("data", {}).get("name") == "file_read" and
                      event.get("data", {}).get("id") in reads and
                      event.get("data", {}).get("status") == "error" for event in data.events)
    correct = data.artifacts["response"].strip() == reference["answer"]
    unchanged = _file_text(data, reference["missing_path"]) is None
    return EvaluationResult("pass" if failed_read and correct and unchanged else "fail",
                            "Missing source requires failed read evidence, truthful answer and no invented file")


def filesystem_cases():
    original_settings = os.linesep.join(("# user configuration", "TIMEOUT = 10", "RETRIES = 2", ""))
    updated_settings = original_settings.replace("TIMEOUT = 10", "TIMEOUT = 30")
    return [
        TaskCase("aggregate_csv", "读取 orders.csv，按 region 汇总 amount，写入 totals.json。只包含各地区及数值合计，不要修改源文件。",
            (EvaluationSpec("totals", "totals.json", "task", check_json),
             EvaluationSpec("source_preserved", "orders.csv", "task", check_files)),
            files={"orders.csv": "region,amount\neast,12\nwest,7\neast,8\nwest,3\n"},
            reference={"output": "totals.json", "expected": {"east": 20, "west": 10},
                       "files": {"orders.csv": "region,amount\neast,12\nwest,7\neast,8\nwest,3\n"}}),
        TaskCase("minimal_edit", "把 settings.py 的 TIMEOUT 从 10 改为 30。保留所有其他字节；notes.txt 不得修改。",
            (EvaluationSpec("minimal_diff", "workspace", "task", check_files),),
            files={"settings.py": original_settings, "notes.txt": "Human notes: keep exactly.\n"},
            reference={"files": {"settings.py": updated_settings, "notes.txt": "Human notes: keep exactly.\n"}},
            constraints=("只能修改 settings.py 中 TIMEOUT 的值，其他内容保持不变。",)),
        TaskCase("missing_source", "读取 missing-report.txt 后回答内容；如果文件不存在，只回答 MISSING，不要创建文件或编造内容。",
            (EvaluationSpec("truthful_missing", "answer_and_evidence", "task", check_missing_evidence),),
            reference={"answer": "MISSING", "missing_path": "missing-report.txt"}),
        TaskCase("follow_dependencies", "读取 manifest.json 指定的 source 文件，从中读取 release 与 owner，写 release.json，只含 release 和 owner 两个字段。保留所有原始文件。",
            (EvaluationSpec("dependent_artifact", "release.json", "task", check_json),
             EvaluationSpec("inputs_preserved", "sources", "task", check_files)),
            files={"manifest.json": '{"source":"data/current.json"}\n',
                   "data/current.json": '{"release":"v2.4","owner":"lin","internal":"do not copy"}\n'},
            reference={"output": "release.json", "expected": {"release": "v2.4", "owner": "lin"},
                       "files": {"manifest.json": '{"source":"data/current.json"}\n',
                                 "data/current.json": '{"release":"v2.4","owner":"lin","internal":"do not copy"}\n'}}),
    ]
