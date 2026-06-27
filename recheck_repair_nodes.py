"""
Shared Recheck & Repair Nodes for PocketFlow Agentic Workflows.
Used by: Business Spec, System Spec, and Implementation Tasks workflows.
"""

import json
from pocketflow import Node
from utils import call_llm, exec_business_system, exec_task, post_task, post_business_system, get_schema, apply_repair, safe_json_loads, extract_json, get_relevant_sections_for_consistency, RECHECK_CONFIG, REPAIR_CONSISTENCY_SECTIONS_MAP, REPAIR_JSON_PROMPT, REPAIR_CONSISTENCY_PROMPT, REPAIR_DEPENDENCIES_PROMPT, REPAIR_CRITICAL_PATH_PROMPT


# ─────────────────────────────────────────────────────────────
# BASE RECHECK NODE
# ─────────────────────────────────────────────────────────────
class RecheckNode(Node):    
    def prep(self, shared):

        print("RecheckNode")
        print("*" * 70)

        workflow_type = shared.get("type", "business")

        if workflow_type == "task":
            check_target = shared.get("_check_target", "tasks")
            return {
                "type": "task",
                "tasks": shared.get("tasks", []),
                "critical_path": shared.get("critical_path_analysis", {}),
                "implementation_plan": shared.get("implementation_plan", {}),
                "repair_count": shared.get("_repair_count", 0),
                "check_target": check_target
            }

        config = RECHECK_CONFIG[workflow_type]
        section_order = config["section_order"]

        for section in reversed(section_order):
            if section in shared and shared[section]:
                return {
                    "type": workflow_type,
                    "section": section,
                    "data": shared[section],
                    "repair_count": shared.get(f"_repair_count_{section}", 0),
                    "all_sections": {k: shared.get(k, {}) for k in section_order if k in shared}
                }
        return {"type": workflow_type, "section": None, "data": None, "repair_count": 0, "all_sections": {}}

    def exec(self, prep_res):
        workflow_type = prep_res["type"]
        if workflow_type == "task":
            return exec_task(prep_res)
        return exec_business_system(prep_res)

    def post(self, shared, prep_res, exec_res):
        result = safe_json_loads(exec_res, {})

        workflow_type = prep_res["type"]

        if workflow_type == "task":
            return post_task(3, shared, prep_res, result)
        else:
            return post_business_system(3, shared, prep_res, result)


# ─────────────────────────────────────────────────────────────
# BASE REPAIR JSON NODE
# ─────────────────────────────────────────────────────────────
class RepairJSONNode(Node):    
    def prep(self, shared):

        print("RepairJSONNode")
        print("*" * 70)

        context = shared.get("_repair_context", {})
        return {
            "type": context.get("type", shared.get("type", "business")),
            "section": context.get("section"),
            "target": context.get("target"),
            "errors": context.get("errors", []),
            "previous_output": context.get("previous_output", ""),
            "attempt": context.get("attempt", 1)
        }

    def exec(self, prep_res):
        workflow_type = prep_res["type"]
        section = prep_res.get("section") or prep_res.get("target")
        schema = get_schema(workflow_type, section)

        prompt = REPAIR_JSON_PROMPT.format(
            validation_errors=json.dumps(prep_res["errors"], indent=2),
            previous_output=prep_res["previous_output"],
            required_keys=json.dumps(schema.get("required", []))
        )
        return call_llm("You are a JSON repair specialist.", prompt, temperature=0.1)

    def post(self, shared, prep_res, exec_res):
        workflow_type = prep_res["type"]
        section = prep_res.get("section")
        target = prep_res.get("target")

        if isinstance(exec_res, dict):
            exec_res = json.dumps(exec_res)

        try:
            repaired = json.loads(exec_res)
            apply_repair(shared, workflow_type, section, target, repaired)
            return "done"
        except json.JSONDecodeError:
            label = section or target or "unknown"
            shared["errors"] = shared.get("errors", []) + [
                f"Repair attempt {prep_res['attempt']} failed for {label}: still invalid JSON"
            ]
            return "error"       

# ─────────────────────────────────────────────────────────────
# BASE REPAIR CONSISTENCY NODE
# ─────────────────────────────────────────────────────────────
class RepairConsistencyNode(Node):
    def prep(self, shared):

        print("RepairConsistencyNode")
        print("*" * 70)

        workflow_type = shared.get("type", "business")
        context = shared.get("_repair_context", {})

        if workflow_type == "task":
            return {"type": "task", "skip": True}

        sections = {}
        sections_map = REPAIR_CONSISTENCY_SECTIONS_MAP.get(workflow_type, {})
        current_section = context.get("section", "")

        # Only include sections that the consistency checker actually uses for this section
        relevant_sections = get_relevant_sections_for_consistency(workflow_type, current_section)
        for key in relevant_sections:
            shared_key = sections_map.get(key, key)
            sections[key] = shared.get(shared_key, {})

        errors = context.get("errors", [])

        return {
            "type": workflow_type,
            "section": context.get("section"),
            "inconsistencies": context.get("inconsistencies", []),
            "sections": sections,
            "is_retry": len(errors) > 0,
            "error_log": errors
        }

    def exec(self, prep_res):
        if prep_res.get("skip"):
            return "{}"

        retry_context = ""
        if prep_res["is_retry"]:
            print(f"ERROR LOG: {prep_res['error_log']}")
            retry_context = f"\nCRITICAL: Previous attempt failed with errors: {json.dumps(prep_res['error_log'])}. Fix these issues."

        inconsistencies = prep_res.get("inconsistencies", [])
        json_like_errors = [
            inc for inc in inconsistencies
            if any(kw in inc.lower() for kw in [
                "missing required key", "expected", "got", "must be", 
                "invalid type", "not a valid", "json", "schema"
            ])
        ]
        if json_like_errors:
            # Return a marker that post() can use to short-circuit
            return json.dumps({
                "_json_errors_detected": True,
                "_actual_errors": json_like_errors
            })

        prompt = REPAIR_CONSISTENCY_PROMPT.format(
            inconsistencies=json.dumps(prep_res["inconsistencies"], indent=2),
            sections_json=json.dumps(prep_res["sections"], indent=2, default=str)
        )

        final_prompt = retry_context + "\n" + prompt if retry_context else prompt

        return call_llm("You are a specification consistency specialist.", final_prompt, temperature=0.2)

    def post(self, shared, prep_res, exec_res):
        if prep_res.get("skip"):
            return "done"

        section = prep_res["section"]
        inconsistencies = prep_res.get("inconsistencies", [])

        # ── SAFETY NET: detect unfixable consistency loops ──
        _seen_key = f"_seen_inconsistencies_{section}"
        seen = shared.get(_seen_key, [])
        inconsistency_signature = json.dumps(sorted(inconsistencies), sort_keys=True)

        if inconsistency_signature in seen:
            print(f"RepairConsistencyNode: WARNING — same inconsistencies seen again for {section}")
            print(f"  Issues: {inconsistencies}")
            if seen.count(inconsistency_signature) >= 2:
                print(f"  LOOP DETECTED. Breaking to prevent infinite repair.")
                shared["errors"] = shared.get("errors", []) + [
                    f"Unfixable consistency issues in {section} (loop detected): {inconsistencies}"
                ]
                return "done"

        seen.append(inconsistency_signature)
        shared[_seen_key] = seen

        # ── FAST-PATH: detect JSON structure errors masquerading as inconsistencies ──
        if isinstance(exec_res, str):
            try:
                parsed_marker = json.loads(exec_res)
                if parsed_marker.get("_json_errors_detected"):
                    shared["_repair_context"] = {
                        "type": prep_res["type"],
                        "section": section,
                        "errors": parsed_marker["_actual_errors"],
                        "previous_output": json.dumps(shared.get(section, {}), indent=2),
                        "attempt": shared.get(f"_repair_count_{section}", 0)
                    }
                    shared["errors"] = shared.get("errors", []) + parsed_marker["_actual_errors"]
                    return "done"
            except (json.JSONDecodeError, TypeError):
                pass

        # ── ROBUST JSON EXTRACTION from LLM response ──
        repaired = extract_json(exec_res)

        if repaired is None:
            raw_preview = str(exec_res)[:500] if exec_res else "<empty>"
            print(f"RepairConsistency: Failed to extract JSON from response")
            print(f"  Raw preview: {raw_preview}")

            shared["errors"] = shared.get("errors", []) + [
                f"Consistency repair failed for {section}: could not extract valid JSON from LLM response"
            ]
            return "error"

        # ── APPLY REPAIR to shared state ──
        if isinstance(repaired, dict):
            shared[section] = {**shared.get(section, {}), **repaired}

        elif isinstance(repaired, list):
            print(f"RepairConsistency: WARNING - LLM returned array instead of dict for {section}")
            current_section = shared.get(section, {})

            if isinstance(current_section, list):
                shared[section] = repaired
                print(f"  -> Replaced list section {section} with repaired list ({len(repaired)} items)")
            else:
                section_key = section.replace("_section", "")
                if section_key in ["ux", "pm", "ba"]:
                    if len(repaired) > 0 and isinstance(repaired[0], dict):
                        first_keys = set(repaired[0].keys())
                        if "name" in first_keys and "role" in first_keys:
                            shared[section] = {**current_section, "personas": repaired}
                        elif "given" in first_keys and "when" in first_keys:
                            shared[section] = {**current_section, "scenarios": repaired}
                        elif "flow" in first_keys:
                            shared[section] = {**current_section, "key_flows": repaired}
                        else:
                            shared["errors"] = shared.get("errors", []) + [
                                f"Consistency repair for {section}: LLM returned unrecognizable array"
                            ]
                            return "error"
                    else:
                        shared["errors"] = shared.get("errors", []) + [
                            f"Consistency repair for {section}: expected dict, got array with non-dict items"
                        ]
                        return "error"
                else:
                    shared["errors"] = shared.get("errors", []) + [
                        f"Consistency repair for {section}: expected dict, got {type(repaired).__name__}"
                    ]
                    return "error"
        else:
            print(f"RepairConsistency: Expected dict, got {type(repaired).__name__}")
            shared["errors"] = shared.get("errors", []) + [
                f"Consistency repair for {section}: expected JSON object, got {type(repaired).__name__}"
            ]
            return "error"

        return "done"

class RepairDependenciesNode(Node):
    """Repair dependency graph issues (cycles, missing refs)."""

    def prep(self, shared):

        print("RepairDependenciesNode")
        print("*" * 70)

        context = shared.get("_repair_context", {})
        return {
            "issues": context.get("issues", []),
            "tasks": shared.get("tasks", []),
            "attempt": context.get("attempt", 1)
        }

    def exec(self, prep_res):
        prompt = REPAIR_DEPENDENCIES_PROMPT.format(
            issues=json.dumps(prep_res["issues"], indent=2),
            tasks_json=json.dumps(prep_res["tasks"], indent=2, default=str)
        )
        return call_llm("You are a dependency graph repair specialist.", prompt, temperature=0.1)

    def post(self, shared, prep_res, exec_res):
        if isinstance(exec_res, dict):
            exec_res = json.dumps(exec_res)

        try:
            repaired = json.loads(exec_res)
            if isinstance(repaired, list):
                shared["tasks"] = repaired
            elif isinstance(repaired, dict) and "tasks" in repaired:
                shared["tasks"] = repaired["tasks"]

            return "default"
        except json.JSONDecodeError:
            shared["errors"] = shared.get("errors", []) + [
                f"Dependency repair attempt {prep_res['attempt']} failed: invalid JSON"
            ]
            return "error"


class RepairCriticalPathNode(Node):
    """Repair critical path analysis inconsistencies."""

    def prep(self, shared):

        print("RepairCriticalPathNode")
        print("*" * 70)

        context = shared.get("_repair_context", {})
        return {
            "issues": context.get("issues", []),
            "tasks": shared.get("tasks", []),
            "current_analysis": shared.get("critical_path_analysis", {}),
            "attempt": context.get("attempt", 1)
        }

    def exec(self, prep_res):
        prompt = REPAIR_CRITICAL_PATH_PROMPT.format(
            issues=json.dumps(prep_res["issues"], indent=2),
            tasks_json=json.dumps(prep_res["tasks"], indent=2, default=str),
            current_analysis=json.dumps(prep_res["current_analysis"], indent=2, default=str)
        )
        return call_llm("You are a critical path repair specialist.", prompt, temperature=0.1)

    def post(self, shared, prep_res, exec_res):
        if isinstance(exec_res, dict):
            exec_res = json.dumps(exec_res)

        try:
            repaired = json.loads(exec_res)
            shared["critical_path_analysis"] = repaired

            return "repaired"
        except json.JSONDecodeError:
            shared["errors"] = shared.get("errors", []) + [
                f"Critical path repair attempt {prep_res['attempt']} failed: invalid JSON"
            ]
            return "error"
