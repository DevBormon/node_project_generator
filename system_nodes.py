from pocketflow import Node
from utils import (
    call_llm, parse_llm_json, build_retry_context, path_join, save_file,
    safe_json_loads, get_latest_feedback, compress_business_spec_for_architecture,
    compress_architecture_for_domain_model, compress_domain_model_for_api_design,
    compress_api_design_for_data_design, compress_api_design_for_integration,
    compress_data_design_for_security, compress_architecture_for_infrastructure,
    compress_security_for_infrastructure, compress_system_for_implementation,
    compress_system_for_tech_review, compress_system_for_compiler,
    TECH_STACK, ARCHITECT_PROMPT, DOMAIN_MODEL_PROMPT, API_DESIGN_PROMPT,
    DATA_DESIGN_PROMPT, INTEGRATION_PROMPT, SECURITY_PROMPT,
    INFRASTRUCTURE_PROMPT, IMPLEMENTATION_PROMPT, TECH_REVIEW_PROMPT,
    SYSTEM_COMPILER_PROMPT
)
import json, os


class BusinessSpecLoaderNode(Node):
    def prep(self, shared):
        print("BusinessSpecLoaderNode")
        print("*" * 70)
        p = os.path.join(path_join(shared["workdir"], "doc"), "system_spec.json")
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                raw = f.read()
            try:
                data = json.loads(raw)
                for k, sk in [
                    ("architecture", "architecture_section"),
                    ("domain_model", "domain_model_section"),
                    ("api_design", "api_design_section"),
                    ("data_design", "data_design_section"),
                    ("integration", "integration_section"),
                    ("security", "security_section"),
                    ("infrastructure", "infrastructure_section"),
                    ("implementation", "implementation_section"),
                ]:
                    shared[sk] = data[k]
                shared["review"] = data["review"]
                shared["quality_score"] = data["quality_score"]
                shared["current_iteration"] = data["iterations"]
                shared["system_spec"] = data
            except (json.JSONDecodeError, ValueError):
                shared["system_spec"] = raw
            shared["_bypass"] = True
            return None
        shared["type"] = "system"
        bs = shared.get("business_spec", {})
        if isinstance(bs, str) or not isinstance(bs, dict):
            bs = {k: shared.get(k, {}) for k in ("seed", "research", "pm_section", "ux_section", "ba_section", "review")}
            shared["business_spec"] = bs
        return bs

    def exec(self, prep_res):
        if prep_res is None:
            return None
        if not prep_res:
            return {"error": "No business specification found. Ensure business_spec is in shared state or saved to disk."}
        return {"business_spec": prep_res, "loaded": True}

    def post(self, shared, prep_res, exec_res):
        if shared.get("_bypass"):
            shared.pop("_bypass", None)
            return "next_flow"
        result = safe_json_loads(exec_res, {})
        if result.get("error"):
            shared["errors"] = shared.get("errors", []) + [result["error"]]
            return "error"
        bs = result.get("business_spec", {})
        if isinstance(bs, str):
            # Try to parse if it's a JSON string
            try:
                bs = json.loads(bs)
            except (json.JSONDecodeError, TypeError):
                shared["errors"] = shared.get("errors", []) + ["Business spec loader returned string instead of dict"]
                return "error"
        if not isinstance(bs, dict):
            shared["errors"] = shared.get("errors", []) + [f"Business spec loader returned {type(bs).__name__}, expected dict"]
            return "error"
        shared["business_spec"] = bs
        return "next"


class ArchitectNode(Node):
    def prep(self, shared):
        print("ArchitectNode")
        print("*" * 70)
        errors = shared.get("errors", [])
        return {
            "business_spec": shared.get("business_spec", {}),
            "existing_architecture": shared.get("architecture_section", {}),
            "feedback": get_latest_feedback(shared, "architecture"),
            "tech_stack": TECH_STACK,
            "is_retry": len(errors) > 0, "error_log": errors,
        }

    def exec(self, prep_res):
        if prep_res["existing_architecture"] and not prep_res["feedback"]:
            return json.dumps(prep_res["existing_architecture"])
        context = {
            "business_requirements": compress_business_spec_for_architecture(prep_res["business_spec"]),
            "previous_version": prep_res["existing_architecture"],
            "feedback_to_address": prep_res["feedback"],
        }
        prompt = f"Design system architecture. Context:{json.dumps(context, indent=2, default=str)}\n{build_retry_context(prep_res['is_retry'], prep_res['error_log'])}"
        return call_llm(ARCHITECT_PROMPT, prompt, temperature=0.2)

    def post(self, shared, prep_res, exec_res):
        parsed = parse_llm_json(exec_res, force_dict=True)
        if parsed is None:
            print(f"ArchitectNode: Failed to parse JSON. Preview: {str(exec_res)[:300]}")
            shared["errors"] = shared.get("errors", []) + ["Architect returned invalid JSON"]
            return "error"
        shared["architecture_section"] = parsed
        return "next"


class DomainModelNode(Node):
    def prep(self, shared):
        print("DomainModelNode")
        print("*" * 70)
        errors = shared.get("errors", [])
        return {
            "business_spec": shared.get("business_spec", {}),
            "architecture": shared.get("architecture_section", {}),
            "existing_domain": shared.get("domain_model_section", {}),
            "feedback": get_latest_feedback(shared, "domain_model"),
            "tech_stack": TECH_STACK,
            "is_retry": len(errors) > 0, "error_log": errors,
        }

    def exec(self, prep_res):
        if prep_res["existing_domain"] and not prep_res["feedback"]:
            return json.dumps(prep_res["existing_domain"])
        context = {
            "business_requirements": {
                "data_entities": [d.get("entity", "") for d in prep_res["business_spec"].get("ba_section", {}).get("data_requirements", [])[:8]],
                "integration_points": [{"system": i.get("system", ""), "protocol": i.get("protocol", "")} for i in prep_res["business_spec"].get("ba_section", {}).get("integration_points", [])[:5]],
            },
            "architecture": compress_architecture_for_domain_model(prep_res["architecture"]),
            "previous_version": prep_res["existing_domain"],
            "feedback_to_address": prep_res["feedback"],
        }
        prompt = f"Design domain model. Context:{json.dumps(context, indent=2, default=str)}\n{build_retry_context(prep_res['is_retry'], prep_res['error_log'])}"
        return call_llm(DOMAIN_MODEL_PROMPT, prompt, temperature=0.2)

    def post(self, shared, prep_res, exec_res):
        parsed = parse_llm_json(exec_res, force_dict=True)
        if parsed is None:
            print(f"DomainModelNode: Failed to parse JSON. Preview: {str(exec_res)[:500]}")
            shared["errors"] = shared.get("errors", []) + ["Domain modeler returned invalid JSON"]
            return "error"
        # Validate required keys
        if "aggregates" not in parsed:
            print(f"DomainModelNode: Missing 'aggregates' key")
            shared["errors"] = shared.get("errors", []) + ["Domain modeler missing 'aggregates' key"]
            return "error"
        # Validate aggregates have names matching BA data_entities
        ba_entities = set()
        ba = shared.get("business_spec", {})
        if isinstance(ba, dict):
            for dr in ba.get("ba_section", {}).get("data_requirements", []):
                if isinstance(dr, dict) and dr.get("entity"):
                    ba_entities.add(dr["entity"])
        if ba_entities:
            agg_names = {a.get("name", "") for a in parsed.get("aggregates", []) if isinstance(a, dict)}
            unknown = agg_names - ba_entities - {""}
            if unknown:
                print(f"DomainModelNode: Warning - aggregates not in BA entities: {unknown}")
                # Don't fail, just warn - LLM may need to add sub-aggregates
        shared["domain_model_section"] = parsed
        return "next"


class APIDesignNode(Node):
    def prep(self, shared):
        print("APIDesignNode")
        print("*" * 70)
        errors = shared.get("errors", [])
        return {
            "business_spec": shared.get("business_spec", {}),
            "architecture": shared.get("architecture_section", {}),
            "domain_model": shared.get("domain_model_section", {}),
            "existing_api": shared.get("api_design_section", {}),
            "feedback": get_latest_feedback(shared, "api_design"),
            "tech_stack": TECH_STACK,
            "is_retry": len(errors) > 0, "error_log": errors,
        }

    def exec(self, prep_res):
        if prep_res["existing_api"] and not prep_res["feedback"]:
            return json.dumps(prep_res["existing_api"])
        context = {
            "business_requirements": {"functional_requirements_count": len(prep_res["business_spec"].get("ba_section", {}).get("functional_requirements", []))},
            "architecture": compress_architecture_for_domain_model(prep_res["architecture"]),
            "domain_model": compress_domain_model_for_api_design(prep_res["domain_model"]),
            "previous_version": prep_res["existing_api"],
            "feedback_to_address": prep_res["feedback"],
        }
        prompt = f"Design APIs. Context:{json.dumps(context, indent=2, default=str)}\n{build_retry_context(prep_res['is_retry'], prep_res['error_log'])}"
        return call_llm(API_DESIGN_PROMPT, prompt, temperature=0.2)

    def post(self, shared, prep_res, exec_res):
        parsed = parse_llm_json(exec_res, force_dict=True)
        if parsed is None:
            print(f"APIDesignNode: Failed to parse JSON. Preview: {str(exec_res)[:300]}")
            shared["errors"] = shared.get("errors", []) + ["API designer returned invalid JSON"]
            return "error"
        shared["api_design_section"] = parsed
        return "next"


class DataDesignNode(Node):
    def prep(self, shared):
        print("DataDesignNode")
        print("*" * 70)
        errors = shared.get("errors", [])
        return {
            "business_spec": shared.get("business_spec", {}),
            "architecture": shared.get("architecture_section", {}),
            "domain_model": shared.get("domain_model_section", {}),
            "api_design": shared.get("api_design_section", {}),
            "existing_data": shared.get("data_design_section", {}),
            "feedback": get_latest_feedback(shared, "data_design"),
            "tech_stack": TECH_STACK,
            "is_retry": len(errors) > 0, "error_log": errors,
        }

    def exec(self, prep_res):
        if prep_res["existing_data"] and not prep_res["feedback"]:
            return json.dumps(prep_res["existing_data"])
        context = {
            "business_requirements": {"data_entities": [d.get("entity", "") for d in prep_res["business_spec"].get("ba_section", {}).get("data_requirements", [])[:8] if isinstance(d, dict)]},
            "architecture": compress_architecture_for_domain_model(prep_res["architecture"]),
            "domain_model": compress_domain_model_for_api_design(prep_res["domain_model"]),
            "api_design": compress_api_design_for_data_design(prep_res["api_design"]),
            "previous_version": prep_res["existing_data"],
            "feedback_to_address": prep_res["feedback"],
        }
        prompt = f"Design data layer. Context:{json.dumps(context, indent=2, default=str)}\n{build_retry_context(prep_res['is_retry'], prep_res['error_log'])}"
        return call_llm(DATA_DESIGN_PROMPT, prompt, temperature=0.2)

    def post(self, shared, prep_res, exec_res):
        parsed = parse_llm_json(exec_res, force_dict=True)
        if parsed is None:
            print(f"DataDesignNode: Failed to parse JSON. Preview: {str(exec_res)[:300]}")
            shared["errors"] = shared.get("errors", []) + ["Data designer returned invalid JSON"]
            return "error"
        shared["data_design_section"] = parsed
        return "next"


class IntegrationNode(Node):
    def prep(self, shared):
        print("IntegrationNode")
        print("*" * 70)
        errors = shared.get("errors", [])
        return {
            "business_spec": shared.get("business_spec", {}),
            "architecture": shared.get("architecture_section", {}),
            "api_design": shared.get("api_design_section", {}),
            "existing_integration": shared.get("integration_section", {}),
            "feedback": get_latest_feedback(shared, "integration"),
            "tech_stack": TECH_STACK,
            "is_retry": len(errors) > 0, "error_log": errors,
        }

    def exec(self, prep_res):
        if prep_res["existing_integration"] and not prep_res["feedback"]:
            return json.dumps(prep_res["existing_integration"])
        context = {
            "business_integrations": [{"system": i.get("system", ""), "protocol": i.get("protocol", "")} for i in prep_res["business_spec"].get("ba_section", {}).get("integration_points", [])[:5] if isinstance(i, dict)],
            "architecture": compress_architecture_for_domain_model(prep_res["architecture"]),
            "api_design": compress_api_design_for_integration(prep_res["api_design"]),
            "previous_version": prep_res["existing_integration"],
            "feedback_to_address": prep_res["feedback"],
        }
        prompt = f"Design integrations. Context:{json.dumps(context, indent=2, default=str)}\n{build_retry_context(prep_res['is_retry'], prep_res['error_log'])}"
        return call_llm(INTEGRATION_PROMPT, prompt, temperature=0.2)

    def post(self, shared, prep_res, exec_res):
        parsed = parse_llm_json(exec_res, force_dict=True)
        if parsed is None:
            print(f"IntegrationNode: Failed to parse JSON. Preview: {str(exec_res)[:300]}")
            shared["errors"] = shared.get("errors", []) + ["Integration designer returned invalid JSON"]
            return "error"
        shared["integration_section"] = parsed
        return "next"


class SecurityNode(Node):
    def prep(self, shared):
        print("SecurityNode")
        print("*" * 70)
        errors = shared.get("errors", [])
        return {
            "business_spec": shared.get("business_spec", {}),
            "architecture": shared.get("architecture_section", {}),
            "api_design": shared.get("api_design_section", {}),
            "data_design": shared.get("data_design_section", {}),
            "existing_security": shared.get("security_section", {}),
            "feedback": get_latest_feedback(shared, "security"),
            "tech_stack": TECH_STACK,
            "is_retry": len(errors) > 0, "error_log": errors,
        }

    def exec(self, prep_res):
        if prep_res["existing_security"] and not prep_res["feedback"]:
            return json.dumps(prep_res["existing_security"])
        context = {
            "business_constraints": prep_res["business_spec"].get("seed", {}).get("constraints", [])[:5],
            "regulations": prep_res["business_spec"].get("research", {}).get("regulations", [])[:3],
            "ba_nfrs": prep_res["business_spec"].get("ba_section", {}).get("non_functional_requirements", [])[:3],
            "architecture": compress_architecture_for_domain_model(prep_res["architecture"]),
            "api_design": compress_api_design_for_integration(prep_res["api_design"]),
            "data_design": compress_data_design_for_security(prep_res["data_design"]),
            "previous_version": prep_res["existing_security"],
            "feedback_to_address": prep_res["feedback"],
        }
        prompt = f"Design security architecture. Context:{json.dumps(context, indent=2, default=str)}\n{build_retry_context(prep_res['is_retry'], prep_res['error_log'])}"
        return call_llm(SECURITY_PROMPT, prompt, temperature=0.15)

    def post(self, shared, prep_res, exec_res):
        parsed = parse_llm_json(exec_res, force_dict=True)
        if parsed is None:
            print(f"SecurityNode: Failed to parse JSON. Preview: {str(exec_res)[:300]}")
            shared["errors"] = shared.get("errors", []) + ["Security architect returned invalid JSON"]
            return "error"
        shared["security_section"] = parsed
        return "next"


class InfrastructureNode(Node):
    def prep(self, shared):
        print("InfrastructureNode")
        print("*" * 70)
        errors = shared.get("errors", [])
        return {
            "business_spec": shared.get("business_spec", {}),
            "architecture": shared.get("architecture_section", {}),
            "data_design": shared.get("data_design_section", {}),
            "security": shared.get("security_section", {}),
            "existing_infra": shared.get("infrastructure_section", {}),
            "feedback": get_latest_feedback(shared, "infrastructure"),
            "tech_stack": TECH_STACK,
            "is_retry": len(errors) > 0, "error_log": errors,
        }

    def exec(self, prep_res):
        if prep_res["existing_infra"] and not prep_res["feedback"]:
            return json.dumps(prep_res["existing_infra"])
        context = {
            "business_constraints": prep_res["business_spec"].get("seed", {}).get("constraints", [])[:5],
            "architecture": compress_architecture_for_infrastructure(prep_res["architecture"]),
            "data_design": {"databases": [d.get("choice", "") for d in prep_res["data_design"].get("databases", [])[:3] if isinstance(d, dict)]},
            "security": compress_security_for_infrastructure(prep_res["security"]),
            "previous_version": prep_res["existing_infra"],
            "feedback_to_address": prep_res["feedback"],
        }
        prompt = f"Design infrastructure. Context:{json.dumps(context, indent=2, default=str)}\n{build_retry_context(prep_res['is_retry'], prep_res['error_log'])}"
        return call_llm(INFRASTRUCTURE_PROMPT, prompt, temperature=0.2)

    def post(self, shared, prep_res, exec_res):
        parsed = parse_llm_json(exec_res, force_dict=True)
        if parsed is None:
            print(f"InfrastructureNode: Failed to parse JSON. Preview: {str(exec_res)[:300]}")
            shared["errors"] = shared.get("errors", []) + ["Infrastructure architect returned invalid JSON"]
            return "error"
        shared["infrastructure_section"] = parsed
        return "next"


class ImplementationNode(Node):
    def prep(self, shared):
        print("ImplementationNode")
        print("*" * 70)
        errors = shared.get("errors", [])
        return {
            "business_spec": shared.get("business_spec", {}),
            "architecture": shared.get("architecture_section", {}),
            "domain_model": shared.get("domain_model_section", {}),
            "api_design": shared.get("api_design_section", {}),
            "data_design": shared.get("data_design_section", {}),
            "integration": shared.get("integration_section", {}),
            "security": shared.get("security_section", {}),
            "infrastructure": shared.get("infrastructure_section", {}),
            "existing_implementation": shared.get("implementation_section", {}),
            "feedback": get_latest_feedback(shared, "implementation"),
            "tech_stack": TECH_STACK,
            "is_retry": len(errors) > 0, "error_log": errors,
        }

    def exec(self, prep_res):
        if prep_res["existing_implementation"] and not prep_res["feedback"]:
            return json.dumps(prep_res["existing_implementation"])
        context = {
            "business_timeline": prep_res["business_spec"].get("seed", {}).get("constraints", [])[:5],
            "pm_goals": prep_res["business_spec"].get("pm_section", {}).get("goals", [])[:3],
            "system_summary": compress_system_for_implementation({
                "architecture_section": prep_res["architecture"],
                "domain_model_section": prep_res["domain_model"],
                "api_design_section": prep_res["api_design"],
                "data_design_section": prep_res["data_design"],
                "integration_section": prep_res["integration"],
                "security_section": prep_res["security"],
                "infrastructure_section": prep_res["infrastructure"],
            }),
            "previous_version": prep_res["existing_implementation"],
            "feedback_to_address": prep_res["feedback"],
        }
        prompt = f"Create implementation roadmap. Context:{json.dumps(context, indent=2, default=str)}\n{build_retry_context(prep_res['is_retry'], prep_res['error_log'])}"
        return call_llm(IMPLEMENTATION_PROMPT, prompt, temperature=0.2)

    def post(self, shared, prep_res, exec_res):
        parsed = parse_llm_json(exec_res, force_dict=True)
        if parsed is None:
            print(f"ImplementationNode: Failed to parse JSON. Preview: {str(exec_res)[:300]}")
            shared["errors"] = shared.get("errors", []) + ["Implementation planner returned invalid JSON"]
            return "error"
        shared["implementation_section"] = parsed
        return "next"


class TechReviewNode(Node):
    QUALITY_THRESHOLD = 8

    def prep(self, shared):
        print("TechReviewNode")
        print("*" * 70)
        return {
            "architecture": shared.get("architecture_section", {}),
            "domain_model": shared.get("domain_model_section", {}),
            "api_design": shared.get("api_design_section", {}),
            "data_design": shared.get("data_design_section", {}),
            "integration": shared.get("integration_section", {}),
            "security": shared.get("security_section", {}),
            "infrastructure": shared.get("infrastructure_section", {}),
            "implementation": shared.get("implementation_section", {}),
            "iteration": shared.get("current_iteration", 0),
            "max_iterations": shared.get("max_iterations", 3),
            "tech_stack": TECH_STACK,
        }

    def exec(self, prep_res):
        context = {
            "system_summary": compress_system_for_tech_review({
                "architecture_section": prep_res["architecture"],
                "domain_model_section": prep_res["domain_model"],
                "api_design_section": prep_res["api_design"],
                "data_design_section": prep_res["data_design"],
                "integration_section": prep_res["integration"],
                "security_section": prep_res["security"],
                "infrastructure_section": prep_res["infrastructure"],
                "implementation_section": prep_res["implementation"],
            }),
            "iteration": prep_res["iteration"],
            "max_iterations": prep_res["max_iterations"],
        }
        prompt = f"Review system specification. Context:{json.dumps(context, indent=2, default=str)}"
        return call_llm(TECH_REVIEW_PROMPT, prompt, temperature=0.15)

    def post(self, shared, prep_res, exec_res):
        parsed = parse_llm_json(exec_res)
        if parsed is None:
            print(f"TechReviewNode: Failed to parse JSON. Preview: {str(exec_res)[:300]}")
            shared["errors"] = shared.get("errors", []) + ["Tech review returned invalid JSON"]
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
                    f"Warning: Max iterations ({max_iter}) reached with score {score}. Proceeding with best effort."
                ]
                return "pass"
            feedback = review.get("section_feedback", {})
            if not isinstance(feedback, dict):
                feedback = {}
            actions = [
                (s, feedback.get(s, {}).get("score", 0))
                for s in ("architecture", "domain_model", "api_design", "data_design",
                          "integration", "security", "infrastructure", "implementation")
                if (isinstance(feedback.get(s), dict) and feedback.get(s, {}).get("score", 0) < self.QUALITY_THRESHOLD)
            ]
            if actions:
                actions.sort(key=lambda x: x[1])
                return actions[0][0]
            return "architecture"
        except json.JSONDecodeError:
            shared["errors"] = shared.get("errors", []) + ["Tech review returned invalid JSON"]
            return "error"


class SystemCompilerNode(Node):
    def prep(self, shared):
        print("SystemCompilerNode")
        print("*" * 70)
        return {
            "business_spec": shared.get("business_spec", {}),
            "architecture": shared.get("architecture_section", {}),
            "domain_model": shared.get("domain_model_section", {}),
            "api_design": shared.get("api_design_section", {}),
            "data_design": shared.get("data_design_section", {}),
            "integration": shared.get("integration_section", {}),
            "security": shared.get("security_section", {}),
            "infrastructure": shared.get("infrastructure_section", {}),
            "implementation": shared.get("implementation_section", {}),
            "review": shared.get("review", {}),
            "feedback_history": shared.get("feedback_history", []),
            "tech_stack": TECH_STACK,
        }

    def exec(self, prep_res):
        context = {
            "system_spec": compress_system_for_compiler({
                "architecture_section": prep_res["architecture"],
                "domain_model_section": prep_res["domain_model"],
                "api_design_section": prep_res["api_design"],
                "data_design_section": prep_res["data_design"],
                "integration_section": prep_res["integration"],
                "security_section": prep_res["security"],
                "infrastructure_section": prep_res["infrastructure"],
                "implementation_section": prep_res["implementation"],
            }),
            "review_summary": {
                "final_score": prep_res["review"].get("quality_score"),
                "iterations": len(prep_res["feedback_history"]),
                "assessment": prep_res["review"].get("overall_assessment"),
                "feasibility": prep_res["review"].get("feasibility_verdict"),
                "stack_compliance": prep_res["review"].get("stack_compliance", {}),
            },
        }
        prompt = f"Compile final system specification. Context:{json.dumps(context, indent=2, default=str)}"
        return call_llm(SYSTEM_COMPILER_PROMPT, prompt, temperature=0.2)

    def post(self, shared, prep_res, exec_res):
        # Validate exec_res is a string (markdown/json)
        if not isinstance(exec_res, str):
            shared["errors"] = shared.get("errors", []) + [f"System compiler returned {type(exec_res).__name__}, expected string"]
            return "error"
        shared["system_spec"] = exec_res
        try:
            md_path = os.path.join(path_join(shared["workdir"], "doc"), "system_spec.md")
            with open(md_path, "w") as f:
                f.write(exec_res)
        except Exception as e:
            shared["errors"] = shared.get("errors", []) + [f"Failed to save system_spec.md: {e}"]
        try:
            shared["system_spec"] = {
                "tech_stack": TECH_STACK,
                "architecture": shared.get("architecture_section", {}),
                "domain_model": shared.get("domain_model_section", {}),
                "api_design": shared.get("api_design_section", {}),
                "data_design": shared.get("data_design_section", {}),
                "integration": shared.get("integration_section", {}),
                "security": shared.get("security_section", {}),
                "infrastructure": shared.get("infrastructure_section", {}),
                "implementation": shared.get("implementation_section", {}),
                "review": shared.get("review", {}),
                "quality_score": shared.get("quality_score", 0),
                "iterations": shared.get("current_iteration", 0),
            }
            save_file(path_join(shared["workdir"], "doc"), shared["system_spec"], "system_spec.json")
        except Exception as e:
            shared["errors"] = shared.get("errors", []) + [f"Failed to save system_spec.json: {e}"]
        shared["current_iteration"] = 0
        shared["feedback_history"] = []
        shared["errors"] = []
        return "next_flow"