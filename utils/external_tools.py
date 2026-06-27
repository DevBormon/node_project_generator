import json, os, re, subprocess, hashlib

if __package__:
    from .call_llm import call_llm
    from .schema import RECHECK_CONFIG, SCHEMA_MAP, IMPLEMENTATION_PLAN_SCHEMA, TASK_SCHEMA
    from .system_prompt import TECH_STACK, CODE_REPAIR_PROMPT, TEST_REPAIR_PROMPT
else:
    from call_llm import call_llm
    from schema import RECHECK_CONFIG, SCHEMA_MAP, IMPLEMENTATION_PLAN_SCHEMA, TASK_SCHEMA
    from system_prompt import TECH_STACK, CODE_REPAIR_PROMPT, TEST_REPAIR_PROMPT

# ── PATH & FILE I/O ──

path_join = os.path.join

def save_file(workdir, data, name):
    p = os.path.join(workdir, name)
    with open(p, "w") as f:
        if isinstance(data, (dict, list)):
            json.dump(data, f, indent=2, default=str)
        else:
            f.write(str(data))

def write_file(path, content, base_dir="."):
    p = os.path.join(base_dir, path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return p

def read_file(path, base_dir="."):
    p = os.path.join(base_dir, path)
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return f.read()

# ── JSON & VALIDATION ──

def validate_json_structure(data, schema):
    errors = []
    for k in schema.get("required", []):
        if k not in data:
            errors.append(f"Missing required key: '{k}'")
    for k, t in schema.get("types", {}).items():
        if k in data and not isinstance(data[k], t):
            errors.append(f"Key '{k}' expected {t.__name__}, got {type(data[k]).__name__}")
    for k, r in (("quality_score", lambda v: 0 <= v <= 10), ("desired_format", lambda v: v in ("minimal", "standard", "enterprise")), ("overall_assessment", lambda v: v in ("green", "yellow", "red"))):
        if k in data and not r(data[k]):
            errors.append(f"{k} invalid: {data[k]}")
    return errors

def safe_json_loads(data, default=None):
    try:
        return json.loads(data if isinstance(data, str) else json.dumps(data))
    except (json.JSONDecodeError, TypeError):
        return default

def get_latest_feedback(shared, section):
    h = shared.get("feedback_history", [])
    return h[-1].get("section_feedback", {}).get(section) if h else None

# ── LLM OUTPUT HELPERS ──

# Parse LLM output with fallback chain: extract_json → remove_markdown → fix_truncated_json → json.loads.
def parse_llm_json(text, default=None, force_dict=False):
    parsed = extract_json(text)
    if parsed is None:
        cleaned = remove_markdown(text)
        cleaned = fix_truncated_json(cleaned)
        try:
            parsed = json.loads(cleaned)
        except (json.JSONDecodeError, TypeError):
            return default
    if force_dict and isinstance(parsed, list):
        return {}
    return parsed

# Build retry context string for LLM prompts.
def build_retry_context(is_retry, error_log, verbose=True):
    if not is_retry:
        return ""
    if verbose and error_log:
        print(f"ERROR LOG: {error_log}")
    return f"\nCRITICAL: Previous attempt failed with errors: {json.dumps(error_log)}. Fix these issues."

# Extract a list from LLM output that may be wrapped in a dict under various keys.
def unwrap_list(data, keys=None):
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return None
    if keys is None:
        keys = ("tasks", "files", "tests", "items", "data", "results", "output", "code", "result", "generated_files", "implementation_tasks", "task_list", "prioritized_tasks")
    for key in keys:
        if key in data and isinstance(data[key], list):
            return data[key]
    if "task_id" in data or ("path" in data and "content" in data):
        return [data]
    return None

# Extract a dict from LLM output, wrapping a bare list if needed.
def unwrap_dict(data, list_key="critical_path"):
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        return {list_key: data}
    return None

def normalize_file_output(parsed):
    """Normalize various LLM output patterns to a list of file objects."""
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for key in ("files", "code", "output", "result", "data", "generated_files", "tasks"):
            if key in parsed and isinstance(parsed[key], list):
                return parsed[key]
        if "path" in parsed and "content" in parsed:
            return [parsed]
        if all(isinstance(v, str) for v in parsed.values()):
            return [{"path": k, "content": v, "language": "typescript"} for k, v in parsed.items()]
    return None

# ── REPAIR HELPERS ──

def failure_fingerprint(test_results):
    """Create a hashable fingerprint of current test failures."""
    parts = []
    for r in sorted(test_results, key=lambda x: x.get("test_file", "")):
        if not r.get("passed", False):
            parts.append(r.get("test_file", ""))
            parts.extend(sorted(r.get("failures", [])))
    return hashlib.md5("|".join(parts).encode()).hexdigest() if parts else "none"

def code_hash(files):
    """Hash of file paths and content previews for change detection."""
    contents = []
    for f in sorted(files, key=lambda x: x.get("path", "")):
        contents.append(f.get("path", ""))
        contents.append(f.get("content", "")[:500])
    return hashlib.md5("|".join(contents).encode()).hexdigest()

def summarize_failures(test_results):
    """Create human-readable summary of failures for error logging."""
    return [
        {"test_file": r.get("test_file"), "target_file": r.get("target_file"),
         "failures": r.get("failures", [])[:10], "stderr_preview": r.get("stderr", "")[:500]}
        for r in test_results if not r.get("passed", False)
    ]

def map_failures_to_sources(test_results, generated_files):
    """Map test failures to their source files."""
    source_to_failures = {}
    file_map = {f["path"]: f for f in generated_files}
    for result in test_results:
        if result.get("passed", False):
            continue
        test_file = result.get("test_file", "")
        target_file = result.get("target_file", "") or (test_file.replace(".test.ts", ".ts") if test_file.endswith(".test.ts") else "")
        failures = result.get("failures", []) + result.get("errors", [])
        stdout_stderr = result.get("stdout", "") + result.get("stderr", "")
        if target_file:
            source_to_failures.setdefault(target_file, []).extend(failures)
        for file_path, issues in _extract_file_specific_errors(stdout_stderr, file_map).items():
            source_to_failures.setdefault(file_path, []).extend(issues)
    return source_to_failures

def _extract_file_specific_errors(output, file_map):
    """Extract TypeScript errors that mention specific file paths."""
    issues = {}
    for match in re.finditer(r'([^\s]+\.(?:ts|js))(?:\(\d+,\d+\))?:\s*error\s+TS\d+:\s*(.+?)(?=\n|$)', output, re.MULTILINE):
        file_path, message = match.group(1), match.group(2).strip()
        if file_path in file_map:
            issues.setdefault(file_path, []).append(message)
    return issues

# ── SETUP HELPERS ──

def extract_signals(shared):
    """Extract all setup-relevant signals from shared state."""
    signals = {"tech_stack": TECH_STACK, "project_name": "api-service"}
    business_spec = shared.get("business_spec", {})
    if isinstance(business_spec, dict):
        seed = business_spec.get("seed", {})
        signals["project_name"] = seed.get("domain", "api-service").lower().replace(" ", "-")
        signals["domain"] = seed.get("domain", "")
        signals["constraints"] = seed.get("constraints", [])
        signals["integration_systems"] = [i.get("system", "") for i in business_spec.get("ba_section", {}).get("integration_points", []) if isinstance(i, dict)]
    system_spec = shared.get("system_spec", {})
    if isinstance(system_spec, dict):
        arch = system_spec.get("architecture_section", {})
        signals["bounded_contexts"] = [c.get("name", "") for c in arch.get("bounded_contexts", []) if isinstance(c, dict)]
        spec_text = json.dumps(system_spec, default=str).lower()
        for key in ("kafka", "redis", "graphql", "websocket", "docker", "github actions"):
            signals[f"mentions_{key}"] = key in spec_text
    tasks = shared.get("tasks", [])
    signals["task_count"] = len(tasks)
    signals["task_categories"] = list(set(t.get("category", "") for t in tasks if isinstance(t, dict)))
    return signals

def discover_yarn_cmd():
    """Discover how to invoke yarn in this environment."""
    for cmd in (["yarn"], ["corepack", "yarn"]):
        if run_shell_command(cmd + ["--version"])[0] == 0:
            return cmd
    exit_code, node_path, _ = run_shell_command(["which", "node"])
    if exit_code == 0 and node_path.strip():
        yarn_path = os.path.join(os.path.dirname(node_path.strip()), "yarn")
        if os.path.exists(yarn_path):
            return [yarn_path]
    if run_shell_command(["npx", "yarn@stable", "--version"])[0] == 0:
        return ["npx", "yarn@stable"]
    return None

# ── TASK HELPERS ──

def plan_to_markdown(plan):
    """Convert implementation plan dict to human-readable Markdown."""
    lines = [f"# Implementation Plan: {plan.get('project_name', 'Untitled')}", "",
             f"- **Version:** {plan.get('version', '1.0.0')}",
             f"- **Generated at:** {plan.get('generated_at', '')}",
             f"- **Total Estimated Hours:** {plan.get('total_estimated_hours', 0)}", ""]
    tech = plan.get("tech_stack_snapshot", {})
    if tech:
        lines += ["## Tech Stack Snapshot", ""] + [f"- **{k}:** {v}" for k, v in tech.items()] + [""]
    for ph in plan.get("phases", []):
        lines += [f"### {ph.get('name', 'Untitled')}",
                  f"- **Duration:** {ph.get('duration_hours', 0)} hours",
                  f"- **Deliverable:** {ph.get('deliverable', '')}",
                  f"- **Tasks:** {', '.join(ph.get('tasks', []))}", ""]
    cp = plan.get("critical_path", [])
    if cp:
        lines += ["## Critical Path", "", f"`{' → '.join(cp)}`", ""]
    tasks = plan.get("tasks", [])
    if tasks:
        lines += ["## Tasks", "",
                  "| ID | Title | Category | Priority | Hours | Dependencies |",
                  "|----|-------|----------|----------|-------|--------------|"]
        for t in tasks:
            deps = ", ".join(t.get("dependencies", [])) or "-"
            lines.append(f"| {t.get('task_id', '')} | {t.get('title', '')} | {t.get('category', '')} | {t.get('priority', '')} | {t.get('estimated_hours', 0)} | {deps} |")
        lines.append("")
    for r in plan.get("risk_mitigation", []):
        lines.append(f"- **{r.get('risk', '')}**: {r.get('mitigation', '')}")
    if plan.get("risk_mitigation"):
        lines.append("")
    return "\n".join(lines)

# ── EXECUTORS ──

def exec_business_system(prep_res):
    section, data = prep_res.get("section"), prep_res.get("data")
    if not section or not data:
        return {"status": "valid", "errors": [], "inconsistencies": []}
    wt = prep_res["type"]
    config = RECHECK_CONFIG[wt]
    schema = SCHEMA_MAP.get(section, {})
    ve = validate_json_structure(data, schema) if schema else []
    inc = []
    if section in config["consistency_sections"]:
        inc = (_business_consistency_checker if wt == "business" else _system_consistency_checker)(prep_res["all_sections"], section)
    if ve:
        return {"status": "repair_json", "errors": ve, "inconsistencies": []}
    if inc:
        return {"status": "repair_consistency", "errors": [], "inconsistencies": inc}
    return {"status": "valid", "errors": [], "inconsistencies": []}

def exec_task(prep_res):
    target = prep_res["check_target"]
    if target == "tasks":
        tasks = prep_res["tasks"]
        if not tasks:
            return {"status": "valid", "errors": [], "issues": []}
        ok, errs = _validate_task_list(tasks)
        if not ok:
            return {"status": "repair_json", "errors": errs, "issues": []}
        dep = _validate_dependency_graph(tasks)
        if dep:
            return {"status": "repair_dependencies", "errors": [], "issues": dep}
        return {"status": "valid", "errors": [], "issues": []}
    elif target == "critical_path":
        analysis, tasks, issues = prep_res["critical_path"], prep_res["tasks"], []
        tids = {t["task_id"] for t in tasks}
        for tid in analysis.get("critical_path", []):
            if tid not in tids:
                issues.append(f"Critical path references unknown task '{tid}'")
        for g in analysis.get("parallel_groups", []):
            for tid in g.get("tasks", []):
                if tid not in tids:
                    issues.append(f"Parallel group references unknown task '{tid}'")
        if analysis.get("critical_path_duration_hours", 0) <= 0:
            issues.append("Critical path duration must be positive")
        return {"status": "repair_critical_path" if issues else "valid", "errors": [], "issues": issues}
    elif target == "implementation_plan":
        errs = validate_json_structure(prep_res["implementation_plan"], IMPLEMENTATION_PLAN_SCHEMA)
        return {"status": "repair_json" if errs else "valid", "errors": errs, "issues": []}
    return {"status": "valid", "errors": [], "issues": []}

def post_business_system(max_attempts, shared, prep_res, result):
    section = prep_res["section"]
    if result.get("status") == "valid":
        return f"{section}_valid"
    rk = f"_repair_count_{section}"
    shared[rk] = shared.get(rk, 0) + 1
    if shared[rk] > max_attempts:
        shared[rk] = 0
        shared["errors"] = shared.get("errors", []) + [f"Max repair attempts exceeded for {section}. Errors: {result.get('errors', []) + result.get('inconsistencies', [])}"]
        return f"{section}_max_attempt_error"
    shared["_repair_context"] = {"type": prep_res["type"], "section": section, "errors": result.get("errors", []), "inconsistencies": result.get("inconsistencies", []), "previous_output": json.dumps(prep_res["data"], indent=2), "attempt": shared[rk]}
    s = result.get("status")
    return f"{section}_repair_{s.split('_')[1]}" if s.startswith("repair_") else "error"

def post_task(max_attempts, shared, prep_res, result):
    target = prep_res["check_target"]
    if result.get("status") == "valid":
        return f"{target}_valid"
    rk = f"_repair_count_{target}"
    shared[rk] = shared.get(rk, 0) + 1
    if shared[rk] > max_attempts:
        shared[rk] = 0
        shared["errors"] = shared.get("errors", []) + [f"Max repair attempts exceeded for {target}. Issues: {result.get('errors', []) + result.get('issues', [])}"]
        return f"{target}_error"
    shared["_repair_context"] = {"type": "task", "target": target, "errors": result.get("errors", []), "issues": result.get("issues", []), "previous_output": json.dumps(prep_res.get("tasks" if target != "critical_path" else "critical_path", {}), indent=2), "attempt": shared[rk]}
    s = result.get("status")
    return f"{target}_{s}" if s in ("repair_json", "repair_dependencies", "repair_critical_path") else f"{target}_error"

def get_schema(workflow_type, section):
    return TASK_SCHEMA if workflow_type == "task" and section == "tasks" else IMPLEMENTATION_PLAN_SCHEMA if workflow_type == "task" and section == "implementation_plan" else SCHEMA_MAP.get(section, {})

def apply_repair(shared, workflow_type, section, target, repaired):
    if workflow_type == "task":
        sk = {"tasks_loader": "tasks", "tasks_generator": "tasks", "tasks_prioritizer": "tasks", "critical_path": "critical_path_analysis", "implementation_plan": "implementation_plan"}.get(target, target)
        if sk == "tasks":
            shared["tasks"] = repaired if isinstance(repaired, list) else repaired.get("tasks", [])
        elif sk in ("implementation_plan", "critical_path_analysis"):
            shared[sk] = repaired
        else:
            shared[sk] = repaired
    else:
        shared[section] = repaired

# ── EXTRACTORS ──

def extract_structured_sections(spec):
    sections = {}
    for k in ["architecture", "domain_model", "api_design", "data_design", "integration", "security", "infrastructure", "implementation", "review"]:
        if k in spec:
            sections[k] = spec[k]
        elif f"{k}_section" in spec:
            sections[k] = spec[f"{k}_section"]
    return sections or spec.get("structured", {})

def flatten_system_spec_for_context(spec):
    flat = {"tech_stack": TECH_STACK, "bounded_contexts": [], "aggregates": [], "endpoints": [], "tables": [], "integrations": [], "security_controls": [], "phases": []}
    arch = spec.get("architecture", spec.get("architecture_section", {}))
    flat["bounded_contexts"] = [c.get("name", "") for c in arch.get("bounded_contexts", [])]
    flat["architectural_style"] = arch.get("architectural_style", {}).get("choice", "")
    dom = spec.get("domain_model", spec.get("domain_model_section", {}))
    flat["aggregates"] = [a.get("name", "") for a in dom.get("aggregates", [])]
    api = spec.get("api_design", spec.get("api_design_section", {}))
    flat["endpoints"] = [{"path": e.get("path", ""), "method": e.get("method", ""), "summary": e.get("summary", "")} for e in api.get("endpoints", [])]
    dat = spec.get("data_design", spec.get("data_design_section", {}))
    flat["tables"] = [s.get("table", "") for s in dat.get("schemas", [])]
    integ = spec.get("integration", spec.get("integration_section", {}))
    flat["integrations"] = [i.get("system", "") for i in integ.get("integrations", [])]
    sec = spec.get("security", spec.get("security_section", {}))
    flat["security_controls"] = list(sec.get("threat_model", []))
    impl = spec.get("implementation", spec.get("implementation_section", {}))
    flat["phases"] = [p.get("name", "") for p in impl.get("phases", [])]
    return flat

# ── BUSINESS COMPRESSORS ──

def compress_for_pm(business_spec):
    if not isinstance(business_spec, dict):
        return {}
    s, r = business_spec.get("seed", {}), business_spec.get("research", {})
    return {"seed": {"domain": s.get("domain", ""), "target_users": s.get("target_users", ""), "core_problem": s.get("core_problem", ""), "constraints": s.get("constraints", [])[:5], "desired_format": s.get("desired_format", "standard")}, "research_insights": {"industry_standards": [str(x)[:200] for x in r.get("industry_standards", [])[:3]], "regulations": [str(x)[:200] for x in r.get("regulations", [])[:3]], "risks": [str(x)[:200] for x in r.get("risks", [])[:3]]}}

def compress_for_ux(business_spec):
    if not isinstance(business_spec, dict):
        return {}
    pm, s = business_spec.get("pm_section", {}), business_spec.get("seed", {})
    return {"problem_statement": pm.get("problem_statement", ""), "goals": pm.get("goals", [])[:5], "non_goals": pm.get("non_goals", [])[:3], "target_users": s.get("target_users", ""), "stakeholders": pm.get("stakeholders", {}), "success_metrics": pm.get("success_metrics", [])[:3]}

def compress_for_ba(business_spec):
    if not isinstance(business_spec, dict):
        return {}
    pm, ux, r = business_spec.get("pm_section", {}), business_spec.get("ux_section", {}), business_spec.get("research", {})
    return {"pm_summary": {"problem_statement": pm.get("problem_statement", ""), "goals": pm.get("goals", [])[:5], "non_goals": pm.get("non_goals", [])[:3], "stakeholders": pm.get("stakeholders", {})}, "ux_summary": {"personas": [{"name": p.get("name", ""), "role": p.get("role", ""), "goal": p.get("goal", "")} for p in ux.get("personas", [])[:3]], "key_flows": [{"name": f.get("flow", f.get("name", "")), "entry": f.get("entry_criteria", ""), "exit": f.get("exit_criteria", "")} for f in ux.get("key_flows", [])[:5]], "edge_cases": [str(e.get("scenario", e))[:150] for e in ux.get("edge_cases", [])[:5]]}, "research_regulations": r.get("regulations", [])[:3]}

def compress_for_review(business_spec):
    if not isinstance(business_spec, dict):
        return {}
    pm, ux, ba = business_spec.get("pm_section", {}), business_spec.get("ux_section", {}), business_spec.get("ba_section", {})
    return {"pm_section": {"problem_statement": pm.get("problem_statement", ""), "goals_count": len(pm.get("goals", [])), "non_goals_count": len(pm.get("non_goals", [])), "stakeholders_present": bool(pm.get("stakeholders", {})), "success_metrics_count": len(pm.get("success_metrics", []))}, "ux_section": {"personas_count": len(ux.get("personas", [])), "scenarios_count": len(ux.get("scenarios", [])), "key_flows_count": len(ux.get("key_flows", [])), "edge_cases_count": len(ux.get("edge_cases", []))}, "ba_section": {"functional_requirements_count": len(ba.get("functional_requirements", [])), "non_functional_requirements_count": len(ba.get("non_functional_requirements", [])), "data_requirements_count": len(ba.get("data_requirements", [])), "integration_points_count": len(ba.get("integration_points", [])), "requirement_scenario_map_present": bool(ba.get("requirement_scenario_map", {}))}}

def compress_for_business_compiler(business_spec):
    if not isinstance(business_spec, dict):
        return business_spec
    return {k: business_spec.get(k, {}) for k in ["seed", "research", "pm_section", "ux_section", "ba_section"]} | {"review": {"quality_score": business_spec.get("review", {}).get("quality_score", 0), "overall_assessment": business_spec.get("review", {}).get("overall_assessment", ""), "feasibility_flags": business_spec.get("review", {}).get("feasibility_flags", [])[:5]}}

# ── SYSTEM COMPRESSORS ──

def compress_business_spec_for_architecture(business_spec):
    if not isinstance(business_spec, dict):
        return {}
    s, pm, ux, ba = business_spec.get("seed", {}), business_spec.get("pm_section", {}), business_spec.get("ux_section", {}), business_spec.get("ba_section", {})
    def _perf(r):
        t = str(r.get("description", r) if isinstance(r, dict) else r).lower()
        return any(k in t for k in ["performance", "scalability", "availability", "latency", "throughput", "concurrent", "load", "traffic", "response time", "uptime", "reliability", "capacity", "bandwidth", "memory", "cpu"])
    return {"seed": {"domain": s.get("domain", ""), "target_users": s.get("target_users", ""), "core_problem": s.get("core_problem", ""), "constraints": s.get("constraints", [])[:5], "desired_format": s.get("desired_format", "standard")}, "pm_summary": {"problem_statement": pm.get("problem_statement", ""), "goals": pm.get("goals", [])[:3], "non_goals": pm.get("non_goals", [])[:3], "stakeholders": pm.get("stakeholders", {}), "success_metrics": pm.get("success_metrics", [])[:2]}, "ux_summary": {"personas": [{"role": p.get("role", ""), "goal": p.get("goal", "")} for p in ux.get("personas", [])[:3]], "key_flows": [f.get("flow", f.get("name", "")) for f in ux.get("key_flows", [])[:5]]}, "ba_summary": {"functional_requirements_count": len(ba.get("functional_requirements", [])), "functional_requirements_preview": [{"id": r.get("id", ""), "description": str(r.get("description", ""))[:120]} for r in ba.get("functional_requirements", [])[:5]], "non_functional_requirements": [r for r in ba.get("non_functional_requirements", []) if _perf(r)][:5], "data_entities": [d.get("entity", "") for d in ba.get("data_requirements", [])[:8]], "integration_points": [{"system": i.get("system", ""), "protocol": i.get("protocol", "")} for i in ba.get("integration_points", [])[:5]]}}

def _compress_api_base(api_design):
    """Shared base for API design compressors."""
    if not isinstance(api_design, dict):
        return {}
    return {"api_style": api_design.get("api_style", {}), "routers": [{"name": r.get("name", ""), "path": r.get("path", ""), "module": r.get("module", "")} for r in api_design.get("routers", [])[:10]]}

def compress_api_design_for_data_design(api_design):
    base = _compress_api_base(api_design)
    if not base:
        return {}
    base["endpoints"] = [{"path": e.get("path", ""), "method": e.get("method", ""), "summary": e.get("summary", "")[:100], "zod_request_schema": e.get("zod_request_schema", ""), "zod_response_schema": e.get("zod_response_schema", "")} for e in api_design.get("endpoints", [])[:15]]
    return base

def compress_api_design_for_integration(api_design):
    return _compress_api_base(api_design)

def compress_architecture_for_domain_model(architecture):
    if not isinstance(architecture, dict):
        return {}
    return {"architectural_style": architecture.get("architectural_style", {}).get("choice", ""), "bounded_contexts": [{"name": c.get("name", ""), "responsibility": c.get("responsibility", "")[:200], "module_path": c.get("module_path", "")} for c in architecture.get("bounded_contexts", [])[:10]], "communication_patterns": {"sync": architecture.get("communication_patterns", {}).get("sync", {}), "async": {"broker": "Kafka", "topics": architecture.get("communication_patterns", {}).get("async", {}).get("topics", [])[:5]}}}

def compress_domain_model_for_api_design(domain_model):
    if not isinstance(domain_model, dict):
        return {}
    return {"aggregates": [{"name": a.get("name", "") if isinstance(a, dict) else str(a), "root": a.get("root", "") if isinstance(a, dict) else "", "entities": [e.get("name", "") if isinstance(e, dict) else str(e) for e in (a.get("entities", []) if isinstance(a, dict) else [])[:5]]} for a in domain_model.get("aggregates", [])[:10] if isinstance(a, dict)], "domain_events": [{"name": e.get("name", ""), "publisher": e.get("publisher", ""), "kafka_topic": e.get("kafka_topic", "")} for e in domain_model.get("domain_events", [])[:10] if isinstance(e, dict)], "relationships": [{"from": r.get("from", ""), "to": r.get("to", ""), "type": r.get("type", ""), "ownership": r.get("ownership", "")} for r in domain_model.get("relationships", [])[:10] if isinstance(r, dict)]}

def compress_data_design_for_security(data_design):
    if not isinstance(data_design, dict):
        return {}
    pii = ("email", "phone", "ssn", "password", "name", "address", "dob", "credit", "card")
    schemas = []
    for s in data_design.get("schemas", [])[:15]:
        pc = [{"name": c.get("name", ""), "type": c.get("type", "")} for c in s.get("columns", []) if any(k in c.get("name", "").lower() for k in pii)]
        schemas.append({"table": s.get("table", ""), "pii_columns": pc})
    return {"schemas": schemas}

def compress_security_for_infrastructure(security):
    if not isinstance(security, dict):
        return {}
    return {"compliance": [c.get("regulation", "") for c in security.get("compliance", [])[:5]], "data_protection": {"at_rest": security.get("data_protection", {}).get("at_rest", ""), "in_transit": security.get("data_protection", {}).get("in_transit", ""), "field_level": bool(security.get("data_protection", {}).get("field_level", ""))}, "rate_limiting": security.get("rate_limiting", {})}

def compress_architecture_for_infrastructure(architecture):
    if not isinstance(architecture, dict):
        return {}
    return {"architectural_style": architecture.get("architectural_style", {}), "bounded_contexts": [{"name": c.get("name", ""), "deployable_unit": c.get("deployable_unit", "")} for c in architecture.get("bounded_contexts", [])[:10]], "principles": architecture.get("principles", {}), "cross_cutting_concerns": architecture.get("cross_cutting_concerns", {})}

def compress_system_for_implementation(system_spec):
    if not isinstance(system_spec, dict):
        return {}
    return {"architecture": {"bounded_contexts": [c.get("name", "") for c in system_spec.get("architecture_section", {}).get("bounded_contexts", [])[:10]]}, "domain_model": {"aggregates": [a.get("name", "") for a in system_spec.get("domain_model_section", {}).get("aggregates", [])[:10]]}, "api_design": {"endpoints_count": len(system_spec.get("api_design_section", {}).get("endpoints", []))}, "data_design": {"schemas_count": len(system_spec.get("data_design_section", {}).get("schemas", []))}, "integration": {"integrations_count": len(system_spec.get("integration_section", {}).get("integrations", []))}, "security": {"compliance": [c.get("regulation", "") for c in system_spec.get("security_section", {}).get("compliance", [])[:5]]}, "infrastructure": {"orchestration": system_spec.get("infrastructure_section", {}).get("orchestration", {})}}

def compress_system_for_tech_review(system_spec):
    if not isinstance(system_spec, dict):
        return {}
    result = {}
    for sec in ("architecture_section", "domain_model_section", "api_design_section", "data_design_section", "integration_section", "security_section", "infrastructure_section", "implementation_section"):
        d = system_spec.get(sec, {})
        if sec == "architecture_section":
            result[sec] = {"style": d.get("architectural_style", {}).get("choice", ""), "bounded_contexts_count": len(d.get("bounded_contexts", [])), "principles": d.get("principles", {})}
        elif sec == "domain_model_section":
            result[sec] = {"aggregates_count": len(d.get("aggregates", [])), "domain_events_count": len(d.get("domain_events", [])), "relationships_count": len(d.get("relationships", []))}
        elif sec == "api_design_section":
            result[sec] = {"endpoints_count": len(d.get("endpoints", [])), "routers_count": len(d.get("routers", [])), "versioning": d.get("versioning", "")}
        elif sec == "data_design_section":
            result[sec] = {"schemas_count": len(d.get("schemas", [])), "caching_strategy": d.get("caching", {}).get("strategy", "")}
        elif sec == "integration_section":
            result[sec] = {"integrations_count": len(d.get("integrations", [])), "patterns": d.get("patterns", {})}
        elif sec == "security_section":
            result[sec] = {"auth_model": d.get("authentication", {}).get("flow", ""), "compliance_count": len(d.get("compliance", [])), "rate_limiting": bool(d.get("rate_limiting", {}))}
        elif sec == "infrastructure_section":
            result[sec] = {"environments_count": len(d.get("environments", [])), "containerization": d.get("containerization", {}).get("base_image", ""), "cicd_tool": d.get("cicd", {}).get("tool", "")}
        elif sec == "implementation_section":
            result[sec] = {"phases_count": len(d.get("phases", [])), "work_breakdown_count": len(d.get("work_breakdown", [])), "team_roles_count": len(d.get("team", []))}
    return result

def compress_system_for_compiler(system_spec):
    if not isinstance(system_spec, dict):
        return system_spec
    result = {}
    for k, v in system_spec.items():
        if not k.endswith("_section") and k not in ("review", "feedback_history"):
            result[k] = v
        elif isinstance(v, dict):
            result[k] = {kk: vv for kk, vv in v.items() if kk not in ("c4_container_diagram", "data_flow_diagram", "sequence_diagram")}
        else:
            result[k] = v
    return result

# ── TASK COMPRESSORS ──

def compress_system_spec_for_tasks(system_spec):
    if not isinstance(system_spec, dict):
        return {}
    return {"architecture": {"bounded_contexts": [{"name": c.get("name", ""), "responsibility": c.get("responsibility", "")[:100]} for c in system_spec.get("architecture_section", {}).get("bounded_contexts", [])[:10]]}, "domain_model": {"aggregates": [{"name": a.get("name", ""), "root": a.get("root", "")} for a in system_spec.get("domain_model_section", {}).get("aggregates", [])[:10]]}, "api_design": {"endpoints": [{"path": e.get("path", ""), "method": e.get("method", ""), "summary": e.get("summary", "")[:80]} for e in system_spec.get("api_design_section", {}).get("endpoints", [])[:15]]}, "data_design": {"schemas": [{"table": s.get("table", "")} for s in system_spec.get("data_design_section", {}).get("schemas", [])[:15]]}, "integration": {"integrations": [{"system": i.get("system", ""), "protocol": i.get("protocol", "")} for i in system_spec.get("integration_section", {}).get("integrations", [])[:10]]}, "security": {"compliance": [c.get("regulation", "") for c in system_spec.get("security_section", {}).get("compliance", [])[:5]]}, "infrastructure": {"environments": [e.get("name", "") for e in system_spec.get("infrastructure_section", {}).get("environments", [])[:3]]}, "implementation": {"phases": [p.get("name", "") for p in system_spec.get("implementation_section", {}).get("phases", [])[:4]]}}

def compress_tasks_for_prioritization(tasks):
    if not isinstance(tasks, list):
        return []
    return [{"task_id": t.get("task_id", ""), "title": t.get("title", ""), "category": t.get("category", ""), "priority": t.get("priority", ""), "estimated_hours": t.get("estimated_hours", 0), "dependencies": t.get("dependencies", [])[:5]} for t in tasks]

def compress_tasks_for_compiler(tasks):
    if not isinstance(tasks, list):
        return []
    return [{"task_id": t.get("task_id", ""), "title": t.get("title", ""), "category": t.get("category", ""), "priority": t.get("priority", ""), "status": t.get("status", ""), "description": str(t.get("description", ""))[:300], "acceptance_criteria": t.get("acceptance_criteria", [])[:5], "estimated_hours": t.get("estimated_hours", 0), "dependencies": t.get("dependencies", [])[:5], "files_to_create": [{"path": f.get("path", "") if isinstance(f, dict) else f} for f in t.get("files_to_create", [])[:5]], "tech_stack_components": t.get("tech_stack_components", [])[:3]} for t in tasks]

# ── JSON REPAIR HELPERS ──

def normalize_ba_json(text):
    def fix(m):
        d = m.group(3).strip().strip('"').strip("'")
        return f'{{"id":"{m.group(1)}{m.group(2)}","description":"{d}"}}'
    return re.sub(r'(REQ-|NFR-)(\d+):\s*["\']?([^"\']+?)["\']?(?=\s*[,\]\}]|$)', fix, text)

def fix_truncated_json(text):
    text = text.rstrip()
    if not text:
        return text
    esc = in_str = False
    for ch in text:
        if esc: esc = False; continue
        if ch == '\\': esc = True; continue
        if ch == '"': in_str = not in_str
    if in_str:
        text += '"'
    if text and text[-1] not in '}]"':
        if text.rstrip().endswith(','):
            text = text.rstrip()[:-1]
        bd = brd = 0; ins = esc = False; cp = -1
        for i, ch in enumerate(text):
            if esc: esc = False; continue
            if ch == '\\': esc = True; continue
            if ch == '"': ins = not ins; continue
            if ins: continue
            if ch == '{': bd += 1
            elif ch == '}': bd -= 1
            elif ch == '[': brd += 1
            elif ch == ']': brd -= 1
            elif ch == ',' and bd == 1: cp = i
        if cp > 0:
            rem = text[cp+1:].strip()
            if rem and ':' in rem:
                ac = rem.split(':', 1)[1].strip()
                if ac and ac[0] == '"' and not ac.endswith('"'):
                    text = text[:cp]
                elif ac and ac[0] in '{[' and ac[-1] not in '}]':
                    text = text[:cp]
    stk = []; ins = esc = False
    for ch in text:
        if esc: esc = False; continue
        if ch == '\\': esc = True; continue
        if ch == '"': ins = not ins; continue
        if ins: continue
        if ch in '{[': stk.append(ch)
        elif ch == '}' and stk and stk[-1] == '{': stk.pop()
        elif ch == ']' and stk and stk[-1] == '[': stk.pop()
    while stk:
        text += '}' if stk.pop() == '{' else ']'
    return text

def extract_json(text):
    if not text or not isinstance(text, str):
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    def _esc(raw):
        res, ins, esc, i = [], False, False, 0
        while i < len(raw):
            ch = raw[i]
            if esc: res.append(ch); esc = False; i += 1; continue
            if ch == '\\': res.append(ch); esc = True; i += 1; continue
            if ch == '"':
                if not ins: ins = True; res.append(ch); i += 1; continue
                j = i + 1
                while j < len(raw) and raw[j] in ' \t\n\r': j += 1
                if j >= len(raw) or raw[j] in ',:}]{[':
                    ins = False; res.append(ch); i += 1; continue
                res.extend(['\\', ch]); i += 1; continue
            
            if ins and ch in '\n\t\r':
                if ch == '\n':
                    res.extend(['\\', 'n'])
                elif ch == '\t':
                    res.extend(['\\', 't'])
                elif ch == '\r':
                    res.extend(['\\', 'r'])
                i += 1
                continue
            
            res.append(ch); i += 1
        return ''.join(res)
    md = re.search(r'```(?:\w+)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if md:
        c = md.group(1).strip()
        for fx in (lambda x: x, _esc, fix_truncated_json):
            try: return json.loads(fx(c))
            except (json.JSONDecodeError, ValueError): continue
    for sc, ec in [('{', '}'), ('[', ']')]:
        si = text.find(sc)
        if si == -1: continue
        dp = 0; ins = esc = False
        for i in range(si, len(text)):
            ch = text[i]
            if esc: esc = False; continue
            if ch == '\\': esc = True; continue
            if ch == '"': ins = not ins; continue
            if not ins:
                if ch == sc: dp += 1
                elif ch == ec:
                    dp -= 1
                    if dp == 0:
                        c = text[si:i+1]
                        for fx in (lambda x: x, _esc, fix_truncated_json):
                            try: return json.loads(fx(c))
                            except (json.JSONDecodeError, ValueError): continue
                        break
    le, lb = text.rfind('}'), text.rfind(']')
    ei = max(le, lb)
    if ei > 0:
        for ec, sc in [('}', '{'), (']', '[')]:
            if text[ei] == ec:
                dp = 1; ins = esc = False
                for i in range(ei - 1, -1, -1):
                    ch = text[i]
                    if esc: esc = False; continue
                    if ch == '\\': esc = True; continue
                    if ch == '"': ins = not ins; continue
                    if not ins:
                        if ch == ec: dp += 1
                        elif ch == sc:
                            dp -= 1
                            if dp == 0:
                                c = text[i:ei+1]
                                for fx in (lambda x: x, _esc, fix_truncated_json):
                                    try: return json.loads(fx(c))
                                    except (json.JSONDecodeError, ValueError): continue
                                break
    try: return json.loads(fix_truncated_json(_esc(text)))
    except (json.JSONDecodeError, ValueError): return None

def remove_markdown(text):
    m = re.search(r'```(?:\w+)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    return m.group(1).strip() if m else text.strip()

# ── SHELL & YARN ──
def run_shell_command(cmd, cwd=".", timeout=120):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except Exception as e:
        return -1, "", str(e)

def is_yarn_pnp_ready(base_dir="."):
    return any(os.path.exists(os.path.join(base_dir, f)) for f in (".pnp.cjs", ".pnp.loader.mjs"))

def run_yarn_command(args, cwd=".", timeout=180):
    try:
        r = subprocess.run(["yarn"] + args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except Exception as e:
        return -1, "", str(e)

def scan_project_files(base_dir="."):
    src = os.path.join(base_dir, "src")
    files = []
    if os.path.exists(src):
        for root, _, fnames in os.walk(src):
            for f in fnames:
                if f.endswith((".ts", ".js", ".json")):
                    files.append(os.path.relpath(os.path.join(root, f), base_dir))
    return files

def calculate_coverage(test_output):
    cov = {"lines": 0, "functions": 0, "branches": 0, "statements": 0}
    for k, p in [("lines", r"Lines\s*:\s*(\d+\.?\d*)%"), ("functions", r"Functions\s*:\s*(\d+\.?\d*)%"), ("branches", r"Branches\s*:\s*(\d+\.?\d*)%"), ("statements", r"Statements\s*:\s*(\d+\.?\d*)%")]:
        m = re.search(p, test_output, re.IGNORECASE)
        if m: cov[k] = float(m.group(1))
    return cov

# ── CONSISTENCY HELPERS ──

def get_relevant_sections_for_consistency(workflow_type, section):
    if workflow_type == "business":
        return {"ux_section": ["pm_section", "ux_section"], "ba_section": ["ux_section", "ba_section", "research"]}.get(section, [section])
    elif workflow_type == "system":
        return {"domain_model_section": ["architecture_section", "domain_model_section"], "api_design_section": ["domain_model_section", "api_design_section"], "data_design_section": ["api_design_section", "data_design_section"], "integration_section": ["architecture_section", "integration_section"], "security_section": ["data_design_section", "security_section"], "infrastructure_section": ["integration_section", "infrastructure_section"], "implementation_section": ["architecture_section", "implementation_section"]}.get(section, [section])
    return [section]

# ── TASK HELPERS ──

def marked_as_completed(source, task_id):
    with open(source, "r", encoding="utf-8") as f:
        tasks = json.load(f)
    upd = any(t.update({"status": "completed"}) or True for t in tasks if t.get("task_id") == task_id)
    if upd:
        with open(source, "w", encoding="utf-8") as f:
            json.dump(tasks, f, indent=2, ensure_ascii=False)

def completed_tasks(source, shared):
    with open(source, "r", encoding="utf-8") as f:
        shared["completed_task_ids"].extend(t["task_id"] for t in json.load(f) if t.get("status") == "completed")

def detect_language(path):
    return {".json": "json", ".ts": "typescript", ".js": "javascript", ".yml": "yaml", ".yaml": "yaml", ".md": "markdown", ".txt": "text", ".sh": "bash"}.get(os.path.splitext(path)[1].lower(), "text")

# ── SETUP VALIDATION ──

def validate_setup_files(task, output_dir="."):
    issues = []
    ftc = task.get("files_to_create", [])
    ac = task.get("acceptance_criteria", [])
    paths = [f.get("path", "") if isinstance(f, dict) else f for f in ftc]
    for p in paths:
        fp = os.path.join(output_dir, p)
        if not os.path.exists(fp):
            issues.append(f"Missing file: {p}")
        elif os.path.getsize(fp) == 0:
            issues.append(f"Empty file: {p}")
    for c in ac:
        cl = str(c).lower()
        if "dependency" in cl and "includes" in cl:
            dm = re.search(r'includes[^:]*:\s*([^"]+?)(?:\s|$)', cl)
            if dm:
                deps = [d.strip().strip('"\'') for d in dm.group(1).split(",") if d.strip()]
                pjp = os.path.join(output_dir, "package.json")
                if os.path.exists(pjp):
                    try:
                        with open(pjp, "r", encoding="utf-8") as f:
                            pkg = json.load(f)
                        ad = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                        for d in deps:
                            dn = d.split("@")[0] if "@" in d and not d.startswith("@") else d
                            if dn not in ad:
                                issues.append(f"Missing dependency in package.json: {dn}")
                    except (json.JSONDecodeError, IOError):
                        issues.append("package.json exists but is unreadable/corrupt")
                else:
                    issues.append("package.json missing — cannot verify dependencies")
        for s in re.findall(r'npm run (\w+)', cl):
            pjp = os.path.join(output_dir, "package.json")
            if os.path.exists(pjp):
                try:
                    with open(pjp, "r", encoding="utf-8") as f:
                        scripts = json.load(f).get("scripts", {})
                    if s not in scripts:
                        issues.append(f"Missing npm script in package.json: {s}")
                except (json.JSONDecodeError, IOError):
                    issues.append("package.json unreadable — cannot verify scripts")
            else:
                issues.append("package.json missing — cannot verify scripts")
        if any(k in cl for k in ("tsc", "typescript", "typecheck")):
            if not os.path.exists(os.path.join(output_dir, "tsconfig.json")):
                issues.append("tsconfig.json missing")
        if "docker-compose" in cl or "docker compose" in cl:
            if not os.path.exists(os.path.join(output_dir, "docker-compose.yml")) and not os.path.exists(os.path.join(output_dir, "docker-compose.yaml")):
                issues.append("docker-compose.yml missing")
        if "yarn" in cl and ("pnp" in cl or "install" in cl):
            if not os.path.exists(os.path.join(output_dir, ".yarnrc.yml")) and not os.path.exists(os.path.join(output_dir, ".pnp.cjs")):
                issues.append("Yarn PnP not configured (.yarnrc.yml or .pnp.cjs missing)")
        if "health" in cl or "server" in cl:
            if not os.path.exists(os.path.join(output_dir, "src", "server.ts")):
                issues.append("src/server.ts missing")
    return len(issues) == 0, issues

# ── CONSISTENCY CHECKERS ──

def _safe_get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else default

def _safe_dict_items(obj_list):
    return (x for x in (obj_list if isinstance(obj_list, list) else []) if isinstance(x, dict))

def _business_consistency_checker(sections, current_section):
    issues = []
    seed, pm, ux, ba = _safe_get(sections, "seed", {}), _safe_get(sections, "pm_section", {}), _safe_get(sections, "ux_section", {}), _safe_get(sections, "ba_section", {})
    if pm and ux:
        st = _safe_get(pm, "stakeholders", {})
        cats = set()
        if isinstance(st, dict):
            for k in ("decision_maker", "end_users", "blockers"):
                if st.get(k): cats.add(k)
        pers = list(_safe_dict_items(_safe_get(ux, "personas", [])))
        prc = set()
        for p in pers:
            rl, gl = str(p.get("role", "")).lower(), str(p.get("goal", "")).lower()
            if any(k in rl or k in gl for k in ("owner", "admin", "decision", "approve", "authorize", "manage")): prc.add("decision_maker")
            if any(k in rl or k in gl for k in ("seeker", "applicant", "candidate", "user", "employee", "hiring", "recruiter", "post", "apply", "search", "browse")): prc.add("end_users")
            if any(k in rl or k in gl for k in ("legal", "compliance", "security", "audit", "regulator", "block", "restrict", "approve", "review")): prc.add("blockers")
        miss = cats - prc
        if miss:
            issues.append(f"PM stakeholder categories {sorted(miss)} not covered by UX personas. Personas cover: {sorted(prc)}. Add personas for: {sorted(miss)}")
        if cats and not pers:
            issues.append("PM defines stakeholders but UX has no personas")
    if ux and ba:
        scens = list(_safe_dict_items(_safe_get(ux, "scenarios", [])))
        sids = {str(s.get("given", ""))[:30] for s in scens if s.get("given")}
        rmap = _safe_get(ba, "requirement_scenario_map", {})
        if sids and rmap and isinstance(rmap, dict):
            if not any(s in json.dumps(rmap) for s in sids):
                issues.append(f"None of the {len(sids)} UX scenarios are mapped in BA requirement_scenario_map. At least one scenario should be traceable.")
    if ba and sections.get("research"):
        res = sections["research"] if isinstance(sections["research"], dict) else {}
        bi = [str(i.get("system", "")) for i in _safe_dict_items(_safe_get(ba, "integration_points", [])) if i.get("system")]
        rt = json.dumps(res, default=str).lower()
        if bi and not any(k in rt for k in ("integration", "api", "third-party", "external", "vendor", "payment", "storage", "webhook")):
            issues.append("BA defines integration points but research section lacks any integration-related findings. Consider adding third-party/vendor analysis to research.")
    if issues:
        print(f"_business_consistency_checker ({current_section}) found issues:")
        for i in issues: print(f"  - {i}")
    return issues

def _system_consistency_checker(sections, current_section):
    issues = []
    arch, domain, api, data = _safe_get(sections, "architecture_section", {}), _safe_get(sections, "domain_model_section", {}), _safe_get(sections, "api_design_section", {}), _safe_get(sections, "data_design_section", {})
    integration, security, infra, impl = _safe_get(sections, "integration_section", {}), _safe_get(sections, "security_section", {}), _safe_get(sections, "infrastructure_section", {}), _safe_get(sections, "implementation_section", {})

    if current_section == "domain_model_section" and arch:
        ctxs = [c.get("name", "") for c in _safe_dict_items(arch.get("bounded_contexts", []))]
        aggs = [a.get("name", "") for a in _safe_dict_items(domain.get("aggregates", []))]
        for c in ctxs:
            if c and not any(c.lower() in a.lower() or a.lower() in c.lower() for a in aggs if a):
                issues.append(f"Bounded context '{c}' has no matching aggregate in domain model")

    if current_section == "api_design_section" and domain:
        skip = {"health", "auth", "webhook", "metrics", "compliance", "docs", "roles", "permissions", "teams", "notifications", "audit", "login", "logout", "register", "verify", "reset", "invite", "admin", "config", "settings", "search", "export", "import", "dashboard", "reports", "analytics", "billing", "payment", "subscribe", "unsubscribe", "webhooks", "callbacks", "oauth"}
        aggs = [a.get("name", "") for a in _safe_dict_items(domain.get("aggregates", []))]
        for ep in _safe_dict_items(api.get("endpoints", [])):
            path = ep.get("path", "")
            if not path or any(x in path for x in ("/health", "/auth", "/webhook", "/metrics", "/compliance")):
                continue
            path_parts = [p for p in path.split("/") if p and not p.startswith("{") and p not in ("api", "v1")]
            if not path_parts:
                continue
            matched = False
            for pp in path_parts:
                for agg in aggs:
                    if agg and (pp.lower() == agg.lower().replace(" ", "-") or pp.lower() in agg.lower() or agg.lower() in pp.lower()):
                        matched = True
                        break
                if matched:
                    break
            if not matched and aggs:
                issues.append(f"API endpoint '{path}' does not map to any domain aggregate: {[a for a in aggs if a]}")

    if current_section == "data_design_section" and domain:
        tables = [s.get("table", "") for s in _safe_dict_items(data.get("schemas", []))]
        aggs = [a.get("name", "") for a in _safe_dict_items(domain.get("aggregates", []))]
        for agg in aggs:
            if agg and not any(agg.lower().replace(" ", "_") in t.lower() or agg.lower() in t.lower() for t in tables if t):
                issues.append(f"Aggregate '{agg}' has no matching table in data design")

    if current_section == "integration_section" and arch:
        ctxs = [c.get("name", "") for c in _safe_dict_items(arch.get("bounded_contexts", []))]
        integ_systems = [i.get("system", "") for i in _safe_dict_items(integration.get("integrations", []))]
        async_topics = arch.get("communication_patterns", {}).get("async", {}).get("topics", [])
        for topic in async_topics:
            if topic and not any(topic.lower() in s.lower() or s.lower() in topic.lower() for s in integ_systems if s):
                issues.append(f"Kafka topic '{topic}' defined in architecture but no matching integration")

    if current_section == "security_section" and api:
        endpoints = list(_safe_dict_items(api.get("endpoints", [])))
        public_endpoints = [e.get("path", "") for e in endpoints if e.get("auth") == "none"]
        auth_middleware = security.get("authentication", {})
        if auth_middleware and not public_endpoints:
            issues.append("Security defines auth but API has no public endpoints — verify all endpoints require auth")

    if current_section == "infrastructure_section" and arch:
        ctxs = [c.get("name", "") for c in _safe_dict_items(arch.get("bounded_contexts", []))]
        if len(ctxs) > 1:
            orchestration = infra.get("orchestration", {})
            if "docker-compose" in str(orchestration).lower() and "kubernetes" not in str(orchestration).lower():
                issues.append(f"Architecture has {len(ctxs)} bounded contexts but infrastructure only uses Docker Compose — consider if this is sufficient")

    if current_section == "implementation_section" and arch:
        ctxs = [c.get("name", "") for c in _safe_dict_items(arch.get("bounded_contexts", []))]
        phases = impl.get("phases", [])
        if phases and ctxs:
            phase_names = " ".join([p.get("name", "").lower() for p in phases])
            uncovered = [c for c in ctxs if c.lower() not in phase_names]
            if uncovered:
                issues.append(f"Implementation phases don't mention bounded contexts: {uncovered}")

    if issues:
        print(f"_system_consistency_checker ({current_section}) found issues:")
        for i in issues:
            print(f"  - {i}")
    return issues


def _validate_task_list(tasks):
    errors = []
    if not isinstance(tasks, list):
        return False, ["Tasks must be a list"]
    seen_ids = set()
    for i, t in enumerate(tasks):
        if not isinstance(t, dict):
            errors.append(f"Task at index {i} is not a dict")
            continue
        tid = t.get("task_id", "")
        if not tid:
            errors.append(f"Task at index {i} missing task_id")
        elif tid in seen_ids:
            errors.append(f"Duplicate task_id: {tid}")
        seen_ids.add(tid)
        for req in ("title", "category", "priority", "status", "description", "files_to_create"):
            if not t.get(req):
                errors.append(f"Task {tid} missing required field: {req}")
        if not isinstance(t.get("files_to_create", []), list):
            errors.append(f"Task {tid} files_to_create must be a list")
        if not isinstance(t.get("dependencies", []), list):
            errors.append(f"Task {tid} dependencies must be a list")
    return len(errors) == 0, errors


def _validate_dependency_graph(tasks):
    issues = []
    task_ids = {t["task_id"] for t in tasks if isinstance(t, dict) and t.get("task_id")}
    for t in tasks:
        if not isinstance(t, dict):
            continue
        tid = t.get("task_id", "")
        for dep in t.get("dependencies", []):
            if dep not in task_ids:
                issues.append(f"Task '{tid}' depends on unknown task '{dep}'")
            if dep == tid:
                issues.append(f"Task '{tid}' has circular dependency on itself")
    # Check for circular dependencies (simple DFS)
    adj = {t["task_id"]: t.get("dependencies", []) for t in tasks if isinstance(t, dict) and t.get("task_id")}
    visited, stack = set(), set()
    def dfs(node, path):
        if node in stack:
            cycle = path[path.index(node):] + [node]
            issues.append(f"Circular dependency: {' → '.join(cycle)}")
            return
        if node in visited:
            return
        visited.add(node)
        stack.add(node)
        for dep in adj.get(node, []):
            dfs(dep, path + [node])
        stack.discard(node)
    for tid in list(adj.keys()):
        dfs(tid, [])
    return issues


# ── REPAIR STRATEGY SELECTION ──

def select_repair_strategy(stuck_count):
    if stuck_count == 0:
        return "targeted"
    elif stuck_count == 1:
        return "holistic"
    elif stuck_count == 2:
        return "compilation_focused"
    elif stuck_count == 3:
        return "targeted_v2"
    else:
        return "radical"


# ── CODE REPAIR EXECUTORS ──

def exec_targeted_repair(prep_res, source_failures, generated_files):
    task_id = prep_res["task_id"]
    files_to_fix = []
    for path, failures in source_failures.items():
        file_obj = next((f for f in generated_files if f.get("path") == path), None)
        if file_obj:
            files_to_fix.append({"path": file_obj["path"], "content": file_obj["content"], "reported_failures": failures})
    if not files_to_fix:
        files_to_fix = [{"path": f["path"], "content": f["content"], "reported_failures": ["No specific failures mapped"]} for f in generated_files if isinstance(f, dict) and "path" in f]
    context = {"task_id": task_id, "strategy": "targeted", "files_to_fix": files_to_fix, "test_failures_summary": summarize_failures(prep_res.get("test_results", []))}
    prompt = (
        f"TARGETED REPAIR for task {task_id}.\n\n"
        f"Fix ONLY the reported failures in the specified files.\n"
        f"Do NOT rewrite files that have no failures.\n"
        f"Do NOT change the public API or add new exports.\n\n"
        f"Context:\n{json.dumps(context, indent=2, default=str)}\n\n"
        f"Output a JSON array of file objects with 'path' and 'content' keys.\n"
        f"Include ONLY files that were actually changed."
    )
    return call_llm(CODE_REPAIR_PROMPT, prompt, temperature=0.2)


def exec_holistic_repair(prep_res, source_failures, generated_files, test_results):
    task_id = prep_res["task_id"]
    context = {
        "task_id": task_id, "strategy": "holistic",
        "all_files": [{"path": f.get("path", ""), "content": f.get("content", "")} for f in generated_files if isinstance(f, dict) and "path" in f],
        "source_failures": source_failures,
        "test_failures_summary": summarize_failures(test_results),
    }
    prompt = (
        f"HOLISTIC REPAIR for task {task_id}.\n\n"
        f"Consider ALL files and ALL test failures as a system.\n"
        f"The bug may be in one file but manifest in another.\n"
        f"Fix the root cause, not just symptoms.\n\n"
        f"Context:\n{json.dumps(context, indent=2, default=str)}\n\n"
        f"Output a JSON array of file objects with 'path' and 'content' keys.\n"
        f"Include ALL files that were changed."
    )
    return call_llm(CODE_REPAIR_PROMPT, prompt, temperature=0.2)


def exec_compilation_focused_repair(prep_res, generated_files, test_results):
    task_id = prep_res["task_id"]
    compilation_errors = []
    for r in test_results:
        combined = r.get("stdout", "") + r.get("stderr", "")
        for line in combined.split("\n"):
            if "error TS" in line or "Cannot find module" in line or "SyntaxError" in line:
                compilation_errors.append(line.strip())
    context = {
        "task_id": task_id, "strategy": "compilation_focused",
        "compilation_errors": compilation_errors[:30],
        "all_files": [{"path": f.get("path", ""), "content": f.get("content", "")} for f in generated_files if isinstance(f, dict) and "path" in f],
    }
    prompt = (
        f"COMPILATION-FOCUSED REPAIR for task {task_id}.\n\n"
        f"Fix these compilation/import errors FIRST. Ignore assertion failures.\n"
        f"Common causes: missing imports, wrong type annotations, incorrect decorators, "
        f"missing type exports, circular dependencies.\n\n"
        f"Compilation errors:\n{json.dumps(compilation_errors[:30], indent=2)}\n\n"
        f"Files:\n{json.dumps(context['all_files'], indent=2, default=str)}\n\n"
        f"Output a JSON array of file objects with 'path' and 'content' keys."
    )
    return call_llm(CODE_REPAIR_PROMPT, prompt, temperature=0.15)


def exec_radical_repair(prep_res, generated_files, test_results):
    task_id = prep_res["task_id"]
    task = prep_res.get("task", {})  # ✅ CHANGED from "_raw_task" to "task"
    if not task or not task.get("description"):
        task = {"task_id": task_id}
    valid_files = [f for f in generated_files if isinstance(f, dict) and "path" in f]
    regenerated_paths = {f["path"] for f in valid_files}
    context = {
        "task_id": task_id, "strategy": "radical",
        "previous_attempt_paths": sorted(regenerated_paths),
        "previous_files": [{"path": f["path"], "content": f["content"]} for f in valid_files if "content" in f],
        "all_test_failures": summarize_failures(test_results),
        "task_description": task.get("description", ""),
        "acceptance_criteria": task.get("acceptance_criteria", []),
        "files_to_create": task.get("files_to_create", []),  # ✅ ADD THIS
        "tech_stack_components": task.get("tech_stack_components", []),
        "category": task.get("category", ""),
        "coding_agent_context": task.get("coding_agent_context", {}),  # ✅ ADD THIS
    }
    prompt = (
        f"RADICAL REPAIR for task {task_id}.\n\n"
        f"Previous attempts failed repeatedly. REGENERATE all files from scratch.\n"
        f"Learn from the failures below — do NOT repeat the same mistakes.\n\n"
        f"Task context:\n{json.dumps(context, indent=2, default=str)}\n\n"
        f"Output a JSON array of file objects with 'path', 'content', and 'language' keys.\n"
        f"Include ALL files needed for this task."
    )
    return call_llm(CODE_REPAIR_PROMPT, prompt, temperature=0.3)


def exec_targeted_v2_repair(prep_res, source_failures, generated_files, test_results):
    task_id = prep_res["task_id"]
    task = prep_res.get("task", {})  # ✅ CHANGED from missing to "task"
    files_to_fix = []
    for path, failures in source_failures.items():
        file_obj = next((f for f in generated_files if f.get("path") == path), None)
        if file_obj:
            files_to_fix.append({"path": file_obj["path"], "content": file_obj["content"], "reported_failures": failures})
    if not files_to_fix:
        files_to_fix = [{"path": f["path"], "content": f["content"], "reported_failures": ["Full review needed"]} for f in generated_files if isinstance(f, dict) and "path" in f]
    error_details = []
    for r in test_results:
        if not r.get("passed", False):
            combined = r.get("stdout", "") + r.get("stderr", "")
            for line in combined.split("\n"):
                stripped = line.strip()
                if stripped and ("error TS" in stripped or "AssertionError" in stripped or "Expected" in stripped or "Received" in stripped or "Cannot find" in stripped or "is not defined" in stripped or "is not a function" in stripped):
                    error_details.append(stripped)
    context = {
        "task_id": task_id, "strategy": "targeted_v2",
        "files_to_fix": files_to_fix,
        "specific_error_lines": error_details[:40],
        "task_description": task.get("description", ""),  # ✅ ADD THIS
        "files_to_create": task.get("files_to_create", []),  # ✅ ADD THIS
        "instruction": "For each error, identify the EXACT line that needs to change and what it should become.",
    }
    prompt = (
        f"TARGETED V2 REPAIR for task {task_id}.\n\n"
        f"Be SURGICAL — change only what's needed.\n\n"
        f"Context:\n{json.dumps(context, indent=2, default=str)}\n\n"
        f"Output a JSON array of file objects with 'path' and 'content' keys.\n"
        f"Include ONLY files that were actually changed."
    )
    return call_llm(CODE_REPAIR_PROMPT, prompt, temperature=0.15)


# ── TEST REPAIR EXECUTORS ──

def exec_targeted_test_repair(prep_res, test_file_map, source_file_map):
    task_id = prep_res["task_id"]
    test_results = prep_res.get("test_results", [])
    failing_tests = []
    for r in test_results:
        if not r.get("passed", False):
            test_path = r.get("test_file", "")
            test_obj = test_file_map.get(test_path, {})
            if test_obj and "path" in test_obj:
                failing_tests.append({"path": test_obj["path"], "content": test_obj["content"], "target_file": r.get("target_file", ""), "failures": r.get("failures", [])[:15], "stdout": r.get("stdout", "")[-2000:], "stderr": r.get("stderr", "")[-1000:]})
    if not failing_tests:
        failing_tests = [{"path": t["path"], "content": t["content"], "failures": ["Generic failure"]} for t in test_file_map.values() if isinstance(t, dict) and "path" in t]
    source_exports = {}
    for path, obj in source_file_map.items():
        if isinstance(obj, dict) and "content" in obj and "path" in obj:
            exports = []
            for line in obj["content"].split("\n"):
                stripped = line.strip()
                if stripped.startswith("export "):
                    exports.append(stripped[:200])
            source_exports[path] = exports[:20]
    context = {"task_id": task_id, "strategy": "targeted_test_repair", "failing_tests": failing_tests, "source_file_exports": source_exports}
    prompt = (
        f"TEST REPAIR for task {task_id}.\n\n"
        f"Fix the failing tests. The SOURCE CODE is correct — fix the TESTS.\n\n"
        f"Source exports:\n{json.dumps(source_exports, indent=2)}\n\n"
        f"Failing tests:\n{json.dumps(failing_tests, indent=2, default=str)}\n\n"
        f"Output a JSON array of test file objects with 'path', 'content', and 'target_file' keys."
    )
    return call_llm(TEST_REPAIR_PROMPT, prompt, temperature=0.2)


def exec_holistic_test_repair(prep_res, test_file_map, source_file_map):
    task_id = prep_res["task_id"]
    test_results = prep_res.get("test_results", [])
    context = {
        "task_id": task_id, "strategy": "holistic_test_repair",
        "all_source_files": [{"path": obj.get("path", ""), "content": obj.get("content", "")} for obj in source_file_map.values() if isinstance(obj, dict) and "path" in obj],
        "all_test_files": [{"path": obj.get("path", ""), "content": obj.get("content", ""), "target_file": obj.get("target_file", "")} for obj in test_file_map.values() if isinstance(obj, dict) and "path" in obj],
        "all_failures": summarize_failures(test_results),
    }
    prompt = (
        f"HOLISTIC TEST REPAIR for task {task_id}.\n\n"
        f"Rewrite ALL tests with full awareness of the source code.\n\n"
        f"Source files:\n{json.dumps(context['all_source_files'], indent=2, default=str)}\n\n"
        f"Current tests:\n{json.dumps(context['all_test_files'], indent=2, default=str)}\n\n"
        f"Failures:\n{json.dumps(context['all_failures'], indent=2)}\n\n"
        f"Output a JSON array of test file objects with 'path', 'content', and 'target_file' keys."
    )
    return call_llm(TEST_REPAIR_PROMPT, prompt, temperature=0.2)