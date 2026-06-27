from pocketflow import Node
from utils import (
    call_llm, parse_llm_json, build_retry_context, path_join, save_file,
    get_latest_feedback, extract_json, remove_markdown, fix_truncated_json,
    normalize_ba_json, compress_for_pm, compress_for_ux, compress_for_ba,
    compress_for_review, compress_for_business_compiler,
    INPUT_PARSER_PROMPT, RESEARCHER_PROMPT, PM_PROMPT, UX_PROMPT, BA_PROMPT,
    REVIEW_PROMPT, COMPILER_PROMPT,
)
import json, os


class InputParserNode(Node):
    def prep(self, shared):
        print("InputParserNode")
        print("*" * 70)
        json_path = os.path.join(path_join(shared["workdir"], "doc"), "business_spec.json")
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                raw = f.read()
            try:
                structured = json.loads(raw)
                for k in ("seed", "research", "pm_section", "ux_section", "ba_section", "review", "quality_score"):
                    shared[k] = structured.get(k, {} if k != "quality_score" else 0)
                shared["business_spec"] = structured
                shared["_bypass"] = True
                return None
            except (json.JSONDecodeError, ValueError):
                shared["business_spec"] = raw
            shared["_bypass"] = True
            return None
        shared["type"] = "business"
        errors = shared.get("errors", [])
        return {"user_input": shared.get("input", ""), "is_retry": len(errors) > 0, "error_log": errors}

    def exec(self, prep_res):
        if prep_res is None:
            return None
        if not prep_res["user_input"]:
            return json.dumps({"error": "No raw input provided"})
        prompt = (
            f"Parse this business idea into structured seed data:{prep_res['user_input']}\n"
            f"{build_retry_context(prep_res['is_retry'], prep_res['error_log'], verbose=False)}"
        )
        return call_llm(INPUT_PARSER_PROMPT, prompt)

    def post(self, shared, prep_res, exec_res):
        if shared.get("_bypass"):
            shared.pop("_bypass", None)
            return "next_flow"
        parsed = parse_llm_json(exec_res, force_dict=True)
        if parsed is None:
            print(f"INPUT: {str(exec_res)[:500]}")
            shared["errors"] = ["Input parser returned invalid JSON"]
            return "error"
        shared["seed"] = parsed
        shared["errors"] = []
        return "next"


class ResearcherNode(Node):
    def prep(self, shared):
        print("ResearcherNode")
        print("*" * 70)
        errors = shared.get("errors", [])
        seed = shared.get("seed", {})
        return {
            "domain": seed.get("domain"), "problem": seed.get("core_problem"),
            "existing_research": shared.get("research"),
            "is_retry": len(errors) > 0, "error_log": errors,
        }

    def exec(self, prep_res):
        if prep_res["existing_research"]:
            return json.dumps(prep_res["existing_research"])
        prompt = (
            f"Research domain: {prep_res['domain']}\nProblem: {prep_res['problem']}\n"
            f"{build_retry_context(prep_res['is_retry'], prep_res['error_log'])}"
        )
        return call_llm(RESEARCHER_PROMPT, prompt)

    def post(self, shared, prep_res, exec_res):
        parsed = parse_llm_json(exec_res, force_dict=True)
        if parsed is None:
            print(f"ResearcherNode: Failed to parse JSON. Preview: {str(exec_res)[:300]}")
            shared["errors"] = shared.get("errors", []) + ["Researcher returned invalid JSON"]
            return "error"
        shared["research"] = parsed
        return "next"


class PMAgentNode(Node):
    def prep(self, shared):
        print("PMAgentNode")
        print("*" * 70)
        errors = shared.get("errors", [])
        return {
            "seed": shared.get("seed", {}), "research": shared.get("research", {}),
            "existing_pm": shared.get("pm_section", {}),
            "feedback": get_latest_feedback(shared, "pm"),
            "is_retry": len(errors) > 0, "error_log": errors,
        }

    def exec(self, prep_res):
        if prep_res["existing_pm"] and not prep_res["feedback"]:
            return json.dumps(prep_res["existing_pm"])
        context = {
            "business_requirements": compress_for_pm({"seed": prep_res["seed"], "research": prep_res["research"]}),
            "previous_version": prep_res["existing_pm"],
            "feedback_to_address": prep_res["feedback"],
        }
        prompt = (
            f"Write PM section. Context:{json.dumps(context, indent=2)}\n"
            f"{build_retry_context(prep_res['is_retry'], prep_res['error_log'])}"
        )
        return call_llm(PM_PROMPT, prompt)

    def post(self, shared, prep_res, exec_res):
        parsed = parse_llm_json(exec_res, force_dict=True)
        if parsed is None:
            print(f"PMAgentNode: Failed to parse JSON. Preview: {str(exec_res)[:300]}")
            shared["errors"] = shared.get("errors", []) + ["PM agent returned invalid JSON"]
            return "error"
        shared["pm_section"] = parsed
        return "next"


class UXAgentNode(Node):
    def prep(self, shared):
        print("UXAgentNode")
        print("*" * 70)
        errors = shared.get("errors", [])
        return {
            "pm_section": shared.get("pm_section", {}), "seed": shared.get("seed", {}),
            "existing_ux": shared.get("ux_section", {}),
            "feedback": get_latest_feedback(shared, "ux"),
            "is_retry": len(errors) > 0, "error_log": errors,
        }

    def exec(self, prep_res):
        if prep_res["existing_ux"] and not prep_res["feedback"]:
            return json.dumps(prep_res["existing_ux"])
        context = {
            "business_requirements": compress_for_ux({"pm_section": prep_res["pm_section"], "seed": prep_res["seed"]}),
            "previous_version": prep_res["existing_ux"],
            "feedback_to_address": prep_res["feedback"],
        }
        prompt = (
            f"Write UX section. Context:{json.dumps(context, indent=2)}\n"
            f"{build_retry_context(prep_res['is_retry'], prep_res['error_log'])}"
        )
        return call_llm(UX_PROMPT, prompt)

    def post(self, shared, prep_res, exec_res):
        parsed = parse_llm_json(exec_res, force_dict=True)
        if parsed is None:
            print(f"UXAgentNode: Failed to parse JSON. Preview: {str(exec_res)[:300]}")
            shared["errors"] = shared.get("errors", []) + ["UX agent returned invalid JSON"]
            return "error"
        shared["ux_section"] = parsed
        return "next"


class BAAgentNode(Node):
    def prep(self, shared):
        print("BAAgentNode")
        print("*" * 70)
        errors = shared.get("errors", [])
        return {
            "pm_section": shared.get("pm_section", {}),
            "ux_section": shared.get("ux_section", {}),
            "research": shared.get("research", {}),
            "existing_ba": shared.get("ba_section", {}),
            "feedback": get_latest_feedback(shared, "ba"),
            "is_retry": len(errors) > 0, "error_log": errors,
        }

    def exec(self, prep_res):
        if prep_res["existing_ba"] and not prep_res["feedback"]:
            return json.dumps(prep_res["existing_ba"])
        context = {
            "business_requirements": compress_for_ba({
                "pm_section": prep_res["pm_section"],
                "ux_section": prep_res["ux_section"],
                "research": prep_res["research"],
            }),
            "previous_version": prep_res["existing_ba"],
            "feedback_to_address": prep_res["feedback"],
        }
        prompt = (
            f"Write BA section. Context:{json.dumps(context, indent=2)}\n"
            f"{build_retry_context(prep_res['is_retry'], prep_res['error_log'])}"
        )
        return call_llm(BA_PROMPT, prompt)

    def post(self, shared, prep_res, exec_res):
        # BA needs normalize_ba_json before fix_truncated_json, so can't use parse_llm_json directly
        parsed = extract_json(exec_res)
        if parsed is not None:
            if isinstance(parsed, list):
                parsed = {}
            shared["ba_section"] = parsed
            return "next"
        # Fallback: normalize BA pseudo-JSON patterns then fix truncation
        cleaned = remove_markdown(exec_res)
        normalized = fix_truncated_json(normalize_ba_json(cleaned))
        try:
            shared["ba_section"] = json.loads(normalized)
            print("[BA] Recovered via normalizer+fixer")
            return "next"
        except (json.JSONDecodeError, TypeError):
            print(f"BAAgentNode: Failed to parse JSON. Preview: {str(exec_res)[:500]}")
            shared["errors"] = shared.get("errors", []) + ["BA agent returned invalid JSON"]
            return "error"


class ReviewAgentNode(Node):
    QUALITY_THRESHOLD = 8

    def prep(self, shared):
        print("ReviewAgentNode")
        print("*" * 70)
        return {
            "pm_section": shared.get("pm_section", {}),
            "ux_section": shared.get("ux_section", {}),
            "ba_section": shared.get("ba_section", {}),
            "research": shared.get("research", {}),
            "iteration": shared.get("current_iteration", 0),
            "max_iterations": shared.get("max_iterations", 3),
        }

    def exec(self, prep_res):
        context = {
            "business_requirements": compress_for_review({
                "pm_section": prep_res["pm_section"],
                "ux_section": prep_res["ux_section"],
                "ba_section": prep_res["ba_section"],
            }),
            "iteration": prep_res["iteration"],
            "max_iterations": prep_res["max_iterations"],
        }
        prompt = f"Review all sections and score quality. Context:{json.dumps(context, indent=2)}"
        return call_llm(REVIEW_PROMPT, prompt)

    def post(self, shared, prep_res, exec_res):
        parsed = parse_llm_json(exec_res)
        if parsed is None:
            print(f"ReviewAgentNode: Failed to parse JSON. Preview: {str(exec_res)[:300]}")
            shared["errors"] = shared.get("errors", []) + ["Review agent returned invalid JSON"]
            return "error"
        try:
            review = parsed
            shared["review"] = review
            shared["quality_score"] = review.get("quality_score", 0)
            shared.setdefault("feedback_history", []).append(review)
            shared["current_iteration"] = shared.get("current_iteration", 0) + 1
            score = review.get("quality_score", 0)
            iteration = shared["current_iteration"]
            max_iter = shared.get("max_iterations", 3)
            if score >= self.QUALITY_THRESHOLD:
                return "pass"
            if iteration >= max_iter:
                shared["errors"] = shared.get("errors", []) + [
                    f"Warning: Max iterations ({max_iter}) reached with score {score}. Proceeding anyway."
                ]
                return "pass"
            feedback = review.get("section_feedback", {})
            actions = [
                s for s in ("pm", "ux", "ba")
                if feedback.get(s, {}).get("score", 0) < self.QUALITY_THRESHOLD
            ]
            return actions[0] if actions else "pm"
        except Exception as e:
            print(f"ReviewAgentNode: Error processing review: {e}")
            shared["errors"] = shared.get("errors", []) + [f"Review agent error: {e}"]
            return "error"


class CompilerNode(Node):
    def prep(self, shared):
        print("CompilerNode")
        print("*" * 70)
        return {
            "seed": shared.get("seed", {}),
            "pm_section": shared.get("pm_section", {}),
            "ux_section": shared.get("ux_section", {}),
            "ba_section": shared.get("ba_section", {}),
            "review": shared.get("review", {}),
            "feedback_history": shared.get("feedback_history", []),
            "desired_format": shared.get("seed", {}).get("desired_format", "standard"),
        }

    def exec(self, prep_res):
        context = {
            "business_requirements": compress_for_business_compiler({
                "seed": prep_res["seed"],
                "pm_section": prep_res["pm_section"],
                "ux_section": prep_res["ux_section"],
                "ba_section": prep_res["ba_section"],
                "review": prep_res["review"],
            }),
            "review_summary": {
                "final_score": prep_res["review"].get("quality_score"),
                "iterations": len(prep_res["feedback_history"]),
                "assessment": prep_res["review"].get("overall_assessment"),
            },
            "desired_format": prep_res["desired_format"],
        }
        prompt = f"Compile final business specification. Context:{json.dumps(context, indent=2)}"
        return call_llm(COMPILER_PROMPT, prompt)

    def post(self, shared, prep_res, exec_res):
        shared["business_spec"] = exec_res
        shared["feedback_history"] = []
        shared["errors"] = []
        shared["current_iteration"] = 0
        try:
            save_file(path_join(shared["workdir"], "doc"), shared["business_spec"], "business_spec.md")
        except Exception as e:
            print("!" * 80)
            print(f"FILE SAVE Error: {e}")
            print("!" * 80)
        try:
            structured_spec = {
                k: shared.get(k, {} if k != "quality_score" else 0)
                for k in ("seed", "research", "pm_section", "ux_section", "ba_section", "review", "quality_score", "current_iteration")
            }
            save_file(path_join(shared["workdir"], "doc"), structured_spec, "business_spec.json")
        except Exception as e:
            print("!" * 80)
            print(f"JSON SAVE Error: {e}")
            print("!" * 80)
        return "next_flow"