# ─────────────────────────────────────────────────────────────
# SCHEMA VALIDATORS (Recheck Layer)
# ─────────────────────────────────────────────────────────────

SEED_SCHEMA = {
    "required": ["domain", "target_users", "core_problem", "constraints", "desired_format"],
    "types": {"domain": str, "target_users": str, "core_problem": str, 
              "constraints": list, "desired_format": str}
}

RESEARCH_SCHEMA = {
    "required": ["industry_standards", "competitors", "regulations", 
                 "user_expectations", "risks"],
    "types": {"industry_standards": list, "competitors": list, "regulations": list,
              "user_expectations": list, "risks": list}
}

PM_SCHEMA = {
    "required": ["problem_statement", "goals", "non_goals", 
                 "success_metrics", "stakeholders", "assumptions"],
    "types": {"problem_statement": str, "goals": list, "non_goals": list,
              "success_metrics": list, "stakeholders": dict, "assumptions": list}
}

UX_SCHEMA = {
    "required": ["personas", "scenarios", "key_flows", "edge_cases"],
    "types": {"personas": list, "scenarios": list, "key_flows": list, "edge_cases": list}
}

BA_SCHEMA = {
    "required": ["functional_requirements", "non_functional_requirements",
                 "data_requirements", "integration_points", "requirement_scenario_map"],
    "types": {"functional_requirements": list, "non_functional_requirements": list,
              "data_requirements": list, "integration_points": list, 
              "requirement_scenario_map": dict}
}

REVIEW_SCHEMA = {
    "required": ["quality_score", "overall_assessment", "section_feedback",
                 "feasibility_flags", "effort_indicators", "risks"],
    "types": {"quality_score": (int, float), "overall_assessment": str,
              "section_feedback": dict, "feasibility_flags": list,
              "effort_indicators": dict, "risks": list}
}

SCHEMA_MAP = {
    "seed": SEED_SCHEMA,
    "research": RESEARCH_SCHEMA,
    "pm_section": PM_SCHEMA,
    "ux_section": UX_SCHEMA,
    "ba_section": BA_SCHEMA,
    "review": REVIEW_SCHEMA
}

RECHECK_CONFIG = {
    "business": {
        "section_order": ["seed", "research", "pm_section", "ux_section", "ba_section"],
        "consistency_sections": ["ux_section", "ba_section"],
    },
    "system": {
        "section_order": [
            "business_spec", "architecture_section", "domain_model_section",
            "api_design_section", "data_design_section", "integration_section",
            "security_section", "infrastructure_section", "implementation_section"
        ],
        "consistency_sections": [
            "api_design_section", "data_design_section", "integration_section",
            "security_section", "infrastructure_section", "implementation_section"
        ],
    },
    "task": {
        "section_order": ["tasks", "critical_path_analysis", "implementation_plan"],
        "consistency_sections": [],
    }
}

REPAIR_CONSISTENCY_SECTIONS_MAP = {
    "business": {
        "seed": "seed", "pm_section": "pm_section",
        "ux_section": "ux_section", "ba_section": "ba_section"
    },
    "system": {
        "architecture_section": "architecture_section",
        "domain_model_section": "domain_model_section",
        "api_design_section": "api_design_section",
        "data_design_section": "data_design_section",
        "integration_section": "integration_section",
        "security_section": "security_section",
        "infrastructure_section": "infrastructure_section",
        "implementation_section": "implementation_section"
    },
}

IMPLEMENTATION_PLAN_SCHEMA = {
    "required": ["project_name", "version", "generated_at", "tech_stack_snapshot", 
                 "phases", "tasks", "dependency_graph", "critical_path", "risk_mitigation",
                 "coding_agent_instructions", "total_estimated_hours"],
    "types": {
        "project_name": str,
        "version": str,
        "generated_at": str,
        "tech_stack_snapshot": dict,
        "phases": list,
        "tasks": list,
        "dependency_graph": dict,
        "critical_path": list,
        "risk_mitigation": list,
        "coding_agent_instructions": dict,
        "total_estimated_hours": (int, float)
    }
}

TASK_SCHEMA = {
    "required": ["task_id", "title", "category", "priority", "status", "description", 
                 "acceptance_criteria", "estimated_hours", "dependencies", "files_to_create",
                 "tech_stack_components", "test_requirements", "coding_agent_context"],
    "types": {
        "task_id": str,
        "title": str,
        "category": str,
        "priority": str,
        "status": str,
        "description": str,
        "acceptance_criteria": list,
        "estimated_hours": (int, float),
        "dependencies": list,
        "files_to_create": list,
        "tech_stack_components": list,
        "test_requirements": dict,
        "coding_agent_context": dict
    }
}