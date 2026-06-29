from pocketflow import Node
from utils import (
    call_llm, path_join, save_file, extract_structured_sections,
    flatten_system_spec_for_context, safe_json_loads, parse_llm_json,
    build_retry_context, unwrap_list, unwrap_dict, compress_system_spec_for_tasks,
    compress_tasks_for_prioritization, compress_tasks_for_compiler, plan_to_markdown,
    TECH_STACK, TASK_GENERATOR_PROMPT, TASK_PRIORITIZER_PROMPT,
    CRITICAL_PATH_PROMPT, TASK_COMPILER_PROMPT,
)
import json, os


class SystemSpecLoaderNode(Node):
    """Load system specification from shared state or disk."""

    def prep(self, shared):
        print("SystemSpecLoaderNode")
        print("*" * 70)
        for filename in ("tasks_only.json", "implementation_tasks.json"):
            p = os.path.join(path_join(shared["workdir"], "doc"), filename)
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                tasks = data if isinstance(data, list) else data.get("tasks", [])
                if tasks:
                    shared["tasks"] = tasks
                    if isinstance(data, dict):
                        shared["implementation_plan"] = data
                    shared["_bypass"] = True
                    return None
        shared["type"] = "task"
        raw = shared.get("system_spec", {})
        return {} if isinstance(raw, str) else raw

    def exec(self, prep_res):
        if prep_res is None:
            return None
        if not prep_res or not any(k in prep_res for k in ("architecture", "domain_model", "api_design", "architecture_section", "raw_text")):
            return {"error": "No valid system specification found. Ensure system_spec is in shared state or saved to disk."}
        return {
            "system_spec": prep_res,
            "structured_sections": extract_structured_sections(prep_res),
            "flat_context": flatten_system_spec_for_context(prep_res),
            "loaded": True,
        }

    def post(self, shared, prep_res, exec_res):
        if shared.get("_bypass"):
            shared.pop("_bypass", None)
            return "next_flow"
        result = safe_json_loads(exec_res, {})
        if result.get("error"):
            shared["errors"] = shared.get("errors", []) + [result["error"]]
            return "error"
        spec = result.get("system_spec", {})
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except (json.JSONDecodeError, TypeError):
                shared["errors"] = shared.get("errors", []) + ["System spec loader returned string instead of dict"]
                return "error"
        if not isinstance(spec, dict):
            shared["errors"] = shared.get("errors", []) + [f"System spec loader returned {type(spec).__name__}, expected dict"]
            return "error"
        shared["system_spec"] = spec
        shared["structured_sections"] = result.get("structured_sections", {})
        shared["flat_context"] = result.get("flat_context", {})
        shared["_check_target"] = "loader"
        return "default"


class TaskGeneratorNode(Node):
    """Generate granular implementation tasks from system specification."""

    def prep(self, shared):
        print("TaskGeneratorNode")
        print("*" * 70)
        errors = shared.get("errors", [])
        return {
            "system_spec": shared.get("system_spec", {}),
            "structured_sections": shared.get("structured_sections", {}),
            "flat_context": shared.get("flat_context", {}),
            "existing_tasks": shared.get("tasks", []),
            "tech_stack": TECH_STACK,
            "is_retry": len(errors) > 0, "error_log": errors,
        }

    def exec(self, prep_res):
        if prep_res["existing_tasks"]:
            return json.dumps(prep_res["existing_tasks"])
        context = {
            "bounded_contexts": prep_res["flat_context"].get("bounded_contexts", []),
            "aggregates": prep_res["flat_context"].get("aggregates", []),
            "endpoints": prep_res["flat_context"].get("endpoints", []),
            "tables": prep_res["flat_context"].get("tables", []),
            "integrations": prep_res["flat_context"].get("integrations", []),
            "security_controls": prep_res["flat_context"].get("security_controls", []),
            "phases": prep_res["flat_context"].get("phases", []),
            "architectural_style": prep_res["flat_context"].get("architectural_style", ""),
            "system_summary": compress_system_spec_for_tasks(prep_res["system_spec"]),
        }
        prompt = f"Generate implementation tasks. Context:\n{json.dumps(context, indent=2, default=str)}\n{build_retry_context(prep_res['is_retry'], prep_res['error_log'])}"
        return call_llm(TASK_GENERATOR_PROMPT, prompt, temperature=0.2)

    def post(self, shared, prep_res, exec_res):
        parsed = parse_llm_json(exec_res)
        # Use explicit keys to prevent extracting wrong data structure
        extracted_tasks = unwrap_list(parsed, keys=("tasks", "task_list", "implementation_tasks"))
        if extracted_tasks is None:
            print(f"TaskGeneratorNode: Failed to extract task list. Preview: {str(exec_res)[:500]}")
            shared["errors"] = shared.get("errors", []) + ["Task generator returned unexpected format"]
            return "error"
        # Validate task structure
        if not isinstance(extracted_tasks, list) or len(extracted_tasks) == 0:
            shared["errors"] = shared.get("errors", []) + ["Task generator returned empty or invalid task list"]
            return "error"
        for i, t in enumerate(extracted_tasks[:3]):  # Check first 3
            if not isinstance(t, dict) or "task_id" not in t:
                shared["errors"] = shared.get("errors", []) + [f"Task generator: task[{i}] missing 'task_id'"]
                return "error"
        shared["tasks"] = extracted_tasks
        shared["_check_target"] = "generator"
        return "default"


class TaskPrioritizerNode(Node):
    """Review, prioritize, and refine tasks. Ensure completeness."""

    def prep(self, shared):
        print("TaskPrioritizerNode")
        print("*" * 70)
        errors = shared.get("errors", [])
        return {
            "tasks": shared.get("tasks", []), "system_spec": shared.get("system_spec", {}),
            "tech_stack": TECH_STACK, "is_retry": len(errors) > 0, "error_log": errors,
        }

    def exec(self, prep_res):
        if not prep_res["tasks"]:
            return json.dumps([])
        context = {
            "tasks": compress_tasks_for_prioritization(prep_res["tasks"]),
            "task_count": len(prep_res["tasks"]),
            "categories_present": list(set(t.get("category", "") for t in prep_res["tasks"] if isinstance(t, dict))),
        }
        prompt = f"Prioritize and refine tasks. Context:\n{json.dumps(context, indent=2, default=str)}\n{build_retry_context(prep_res['is_retry'], prep_res['error_log'])}"
        return call_llm(TASK_PRIORITIZER_PROMPT, prompt, temperature=0.2)

    def post(self, shared, prep_res, exec_res):
        parsed = parse_llm_json(exec_res)
        # Use explicit keys to prevent extracting wrong data structure
        extracted_tasks = unwrap_list(parsed, keys=("tasks", "task_list", "implementation_tasks"))
        if extracted_tasks is None:
            print(f"TaskPrioritizerNode: Failed to extract task list. Preview: {str(exec_res)[:500]}")
            shared["errors"] = shared.get("errors", []) + ["Task prioritizer returned unexpected format"]
            return "error"
        if not isinstance(extracted_tasks, list) or len(extracted_tasks) == 0:
            shared["errors"] = shared.get("errors", []) + ["Task prioritizer returned empty task list"]
            return "error"
        shared["tasks"] = extracted_tasks
        shared["_check_target"] = "prioritizer"
        return "default"


class CriticalPathNode(Node):
    """Analyze dependency graph and identify critical path."""

    def prep(self, shared):
        print("CriticalPathNode")
        print("*" * 70)
        errors = shared.get("errors", [])
        return {
            "tasks": shared.get("tasks", []),
            "existing_analysis": shared.get("critical_path_analysis", {}),
            "is_retry": len(errors) > 0, "error_log": errors,
        }

    def exec(self, prep_res):
        if not prep_res["tasks"]:
            return json.dumps({})
        context = {
            "tasks": [
                {"task_id": t["task_id"], "estimated_hours": t.get("estimated_hours", 4),
                 "dependencies": t.get("dependencies", []), "category": t.get("category", ""),
                 "priority": t.get("priority", "")}
                for t in prep_res["tasks"]
            ]
        }
        prompt = f"Analyze critical path. Context:\n{json.dumps(context, indent=2)}\n{build_retry_context(prep_res['is_retry'], prep_res['error_log'])}"
        return call_llm(CRITICAL_PATH_PROMPT, prompt, temperature=0.2)

    def post(self, shared, prep_res, exec_res):
        parsed = parse_llm_json(exec_res)
        # Use explicit keys for critical path analysis
        extracted_analysis = unwrap_dict(parsed, list_key="critical_path")
        if extracted_analysis is None:
            print(f"CriticalPathNode: Unexpected format. Preview: {str(exec_res)[:500]}")
            shared["errors"] = shared.get("errors", []) + ["Critical path analyzer returned unexpected format"]
            return "error"
        if not isinstance(extracted_analysis, dict):
            shared["errors"] = shared.get("errors", []) + ["Critical path analyzer returned non-dict"]
            return "error"
        shared["critical_path_analysis"] = extracted_analysis
        shared["_check_target"] = "critical_path"
        return "default"


class TaskCompilerNode(Node):
    """Compile final implementation_tasks.json with all metadata."""

    def prep(self, shared):
        print("TaskCompilerNode")
        print("*" * 70)
        errors = shared.get("errors", [])
        return {
            "tasks": shared.get("tasks", []),
            "critical_path": shared.get("critical_path_analysis", {}),
            "system_spec": shared.get("system_spec", {}),
            "flat_context": shared.get("flat_context", {}),
            "tech_stack": TECH_STACK,
            "is_retry": len(errors) > 0, "error_log": errors,
        }

    def exec(self, prep_res):
        if not prep_res["tasks"]:
            return json.dumps({})
        total_hours = sum(t.get("estimated_hours", 0) for t in prep_res["tasks"] if isinstance(t, dict))
        category_order = ["setup", "infrastructure", "database", "domain_model", "api", "integration", "security", "middleware", "testing", "deployment", "documentation"]
        category_map = {cat: [] for cat in category_order}
        for t in prep_res["tasks"]:
            cat = t.get("category", "uncategorized")
            if cat in category_map:
                category_map[cat].append(t["task_id"])
        phases = []
        for cat in category_order:
            task_ids = category_map[cat]
            if task_ids:
                cat_tasks = [t for t in prep_res["tasks"] if t["task_id"] in task_ids]
                phases.append({"name": f"Phase: {cat.replace('_', ' ').title()}", "tasks": task_ids, "duration_hours": sum(t.get("estimated_hours", 0) for t in cat_tasks if isinstance(t, dict)), "deliverable": f"Complete {cat.replace('_', ' ')} implementation"})
        context = {
            "tasks": compress_tasks_for_compiler(prep_res["tasks"]),
            "critical_path": prep_res["critical_path"], "phases": phases,
            "total_hours": total_hours,
            "project_name": prep_res["flat_context"].get("bounded_contexts", ["System"])[0] if prep_res["flat_context"].get("bounded_contexts") else "System",
        }
        prompt = f"Compile final implementation plan. Context:\n{json.dumps(context, indent=2, default=str)}\n{build_retry_context(prep_res['is_retry'], prep_res['error_log'])}"
        return call_llm(TASK_COMPILER_PROMPT, prompt, temperature=0.2)

    def post(self, shared, prep_res, exec_res):
        parsed = parse_llm_json(exec_res)
        # Use explicit keys for implementation plan
        extracted_plan = unwrap_dict(parsed, list_key="implementation_plan")
        if extracted_plan is None:
            print(f"TaskCompilerNode: Unexpected format. Preview: {str(exec_res)[:500]}")
            shared["errors"] = shared.get("errors", []) + ["Task compiler returned unexpected format"]
            return "error"
        if not isinstance(extracted_plan, dict):
            shared["errors"] = shared.get("errors", []) + ["Task compiler returned non-dict"]
            return "error"
        shared["implementation_plan"] = extracted_plan
        # JSON artifacts
        save_file(path_join(shared["workdir"], "doc"), parsed, "implementation_tasks.json")
        save_file(path_join(shared["workdir"], "doc"), parsed.get("tasks", []) if isinstance(parsed, dict) else [], "tasks_only.json")
        # Markdown artifact
        try:
            md_path = os.path.join(path_join(shared["workdir"], "doc"), "implementation_tasks.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(plan_to_markdown(parsed) if isinstance(parsed, dict) else "")
        except Exception as e:
            shared["errors"] = shared.get("errors", []) + [f"Failed to save implementation_tasks.md: {e}"]
        shared["_check_target"] = "implementation_plan"
        return "default"