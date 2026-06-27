"""
Code Generation Nodes for PocketFlow 0.0.3.
Consumes implementation_tasks.json & tasks_only.json → produces production-ready code.

FAIL-STOP DESIGN: When repair is exhausted, the flow HALTS at EndNode.
All diagnostic state is preserved in shared for inspection.
"""

import json, os, re
from pocketflow import Node
from utils import (
    call_llm, write_file,
    read_file, run_yarn_command, scan_project_files, calculate_coverage,
    safe_json_loads, extract_json, parse_llm_json, normalize_file_output,
    map_failures_to_sources, failure_fingerprint, code_hash, summarize_failures,
    select_repair_strategy, exec_targeted_repair, exec_holistic_repair,
    exec_compilation_focused_repair, exec_radical_repair, exec_targeted_v2_repair,
    exec_targeted_test_repair, exec_holistic_test_repair,
    CODE_GENERATOR_PROMPT, TEST_GENERATOR_PROMPT
)


class TaskLoaderNode(Node):
    """Load implementation tasks and determine next task to process."""

    def prep(self, shared):
        print("TaskLoaderNode")
        print("*" * 70)
        return {
            "tasks": shared.get("tasks", []),
            "completed": shared.get("completed_task_ids", []),
            "failed": shared.get("failed_task_ids", []),
            "current_task": shared.get("current_task", {}),
        }

    def exec(self, prep_res):
        tasks = prep_res["tasks"]
        if not tasks:
            return json.dumps({"error": "No implementation tasks found. Run implementation task workflow first."})
        completed, failed = set(prep_res["completed"]), set(prep_res["failed"])
        next_task = None
        for task in tasks:
            tid = task.get("task_id", "")
            print(f"TASK ID: {tid}")
            if tid in completed or tid in failed:
                continue
            if all(d in completed for d in task.get("dependencies", [])):
                next_task = task
                break
        if not next_task:
            return json.dumps({"all_complete": True, "total_tasks": len(tasks), "completed": len(completed)})
        return {"task": next_task, "task_index": tasks.index(next_task), "total_tasks": len(tasks), "completed_count": len(completed)}

    def post(self, shared, prep_res, exec_res):
        result = safe_json_loads(exec_res, {})
        if result.get("error"):
            shared["errors"] = shared.get("errors", []) + [result["error"]]
            return "error"
        if result.get("all_complete"):
            shared["_all_tasks_complete"] = True
            return "all_complete"
        shared["current_task"] = result["task"]
        shared["_task_progress"] = f"{result['completed_count']}/{result['total_tasks']}"
        return "default"


class CodeGeneratorNode(Node):
    """Generate production code files for the current task."""

    def prep(self, shared):
        print("CodeGeneratorNode")
        print("*" * 70)
        task = shared.get("current_task", {})
        output_dir = shared.get("output_dir", shared.get("workdir", "."))
        scan_project_files(output_dir)
        task_id, category = task.get("task_id", ""), task.get("category", "")
        files_to_create = task.get("files_to_create", [])
        if files_to_create and not isinstance(files_to_create[0], dict):
            files_to_create = [{"path": p} for p in files_to_create]
        if category == "setup" and shared.get("setup_finalized", False):
            missing_source_files = []
            for f in files_to_create:
                path = f.get("path", "") if isinstance(f, dict) else f
                if path in ("package.json", "tsconfig.json", ".yarnrc.yml", ".gitignore", ".editorconfig", "README.md", ".env.example", "vitest.config.ts", "Dockerfile", ".dockerignore"):
                    continue
                full_path = os.path.join(output_dir, path)
                if not os.path.exists(full_path) or os.path.getsize(full_path) == 0:
                    missing_source_files.append(path)
            if not missing_source_files:
                print(f"[CodeGeneratorNode] Setup task {task_id} — all source files exist, setup finalized — skipping")
                return {"already_done": True, "task": task, "output_dir": output_dir}
        if task_id and task_id in shared.get("completed_task_ids", []):
            print(f"[CodeGeneratorNode] Task {task_id} already completed — skipping generation")
            return {"already_done": True, "task": task, "output_dir": output_dir}
        missing_files, existing_setup_files = [], []
        for f in files_to_create:
            path = f.get("path", "") if isinstance(f, dict) else f
            full_path = os.path.join(output_dir, path)
            if not os.path.exists(full_path) or os.path.getsize(full_path) == 0:
                missing_files.append(f)
            else:
                existing_setup_files.append(path)
        if existing_setup_files:
            print(f"[CodeGeneratorNode] EXISTING files will NOT be regenerated: {existing_setup_files}")
        if not missing_files and files_to_create:
            print(f"[CodeGeneratorNode] All files for {task_id} already exist — skipping generation")
            return {"already_done": True, "task": task, "output_dir": output_dir}
        return {"task": task, "missing_files": missing_files, "existing_files": existing_setup_files, "output_dir": output_dir, "project_context": shared.get("project_context", {})}

    def exec(self, prep_res):
        if prep_res.get("already_done"):
            return json.dumps([])
        task = prep_res["task"]
        if not task:
            return json.dumps([])
        existing_contents = {}
        for f in task.get("files_to_modify", []):
            path = f.get("path", "") if isinstance(f, dict) else f
            content = read_file(path, prep_res["output_dir"])
            if content:
                existing_contents[path] = content
        context = {
            "task_id": task.get("task_id", ""), "title": task.get("title", ""),
            "category": task.get("category", ""), "description": task.get("description", ""),
            "acceptance_criteria": task.get("acceptance_criteria", []),
            "files_to_create": prep_res.get("missing_files", []),
            "files_to_modify": task.get("files_to_modify", []),
            "tech_stack_components": task.get("tech_stack_components", []),
            "coding_agent_context": task.get("coding_agent_context", {}),
            "dependencies": task.get("dependencies", []),
            "existing_files": prep_res["existing_files"],
            "existing_file_contents": existing_contents,
        }
        prompt = "Generate code for task:\n" + json.dumps(context, indent=2, default=str)
        return call_llm(CODE_GENERATOR_PROMPT, prompt, temperature=0.2)

    def post(self, shared, prep_res, exec_res):
        output_dir, task = prep_res["output_dir"], prep_res.get("task", {})
        if prep_res.get("already_done"):
            existing_files = []
            for f in task.get("files_to_create", []):
                path = f.get("path", "") if isinstance(f, dict) else f
                content = read_file(path, output_dir)
                if content:
                    existing_files.append({"path": path, "content": content, "language": "typescript", "purpose": f"Existing file for {task.get('task_id', '')}"})
            shared["generated_files"] = existing_files
            shared["_code_gen_skipped_existing"] = True  # ← new flag
            return "default"
        parsed = parse_llm_json(exec_res)
        files = normalize_file_output(parsed)
        if files is None:
            print(f"CodeGeneratorNode: Could not parse LLM output for task {task.get('task_id', '?')}")
            print(f"  Raw preview: {str(exec_res)[:500]}")
            shared["errors"] = shared.get("errors", []) + [f"Code generator returned invalid output for task {task.get('task_id', '?')}"]
            return "error"
        try:
            if not isinstance(files, list):
                raise ValueError(f"Expected list, got {type(files).__name__}")
            validated_files = []
            for i, f in enumerate(files):
                if not isinstance(f, dict) or "path" not in f:
                    continue
                if "content" not in f:
                    f = {**f, "content": ""}
                validated_files.append(f)
            if not validated_files:
                raise ValueError("No valid file objects found")
            shared["generated_files"] = validated_files
            files_written, files_skipped = 0, 0
            for f in validated_files:
                full_path = os.path.join(output_dir, f["path"])
                if os.path.exists(full_path) and os.path.getsize(full_path) > 0:
                    print(f"[CodeGeneratorNode] SKIP WRITE: '{f['path']}' already exists on disk")
                    files_skipped += 1
                    continue
                write_file(f["path"], f["content"], base_dir=output_dir)
                files_written += 1
            print(f"CodeGeneratorNode: Wrote {files_written} files, skipped {files_skipped} existing for task {task.get('task_id', '?')}")
            return "default"
        except (ValueError, KeyError, TypeError) as e:
            print(f"CodeGeneratorNode: Validation failed: {e}")
            shared["errors"] = shared.get("errors", []) + [f"Code generator invalid structure for {task.get('task_id', '?')}: {e}"]
            return "error"


class TestGeneratorNode(Node):
    """Generate comprehensive tests for the generated code."""

    def prep(self, shared):
        print("TestGeneratorNode")
        print("*" * 70)
        task = shared.get("current_task", {})
        output_dir = shared.get("output_dir", shared.get("workdir", "."))
        generated_files = shared.get("generated_files", [])
        task_id = task.get("task_id", "")
        if task_id and task_id in shared.get("completed_task_ids", []):
            print(f"[TestGeneratorNode] Task {task_id} already completed — skipping")
            return {"already_done": True, "task": task, "output_dir": output_dir, "generated_files": generated_files}
        test_files = task.get("test_requirements", {}).get("test_files", [])
        if test_files and all(os.path.exists(os.path.join(output_dir, tf)) and os.path.getsize(os.path.join(output_dir, tf)) > 0 for tf in test_files):
            print(f"[TestGeneratorNode] All test files for {task_id} already exist — skipping")
            return {"already_done": True, "task": task, "output_dir": output_dir, "generated_files": generated_files}
        return {"task": task, "generated_files": generated_files, "output_dir": output_dir}

    def exec(self, prep_res):
        if prep_res.get("already_done"):
            return json.dumps([])
        task, files = prep_res["task"], prep_res["generated_files"]
        if not files:
            return json.dumps([])

        # ── FIX: skip LLM call when task has no test requirements ──
        test_req = task.get("test_requirements", {})
        tests_required = (
            test_req.get("unit_tests", False)
            or test_req.get("integration_tests", False)
            or bool(test_req.get("test_files", []))
        )
        if not tests_required:
            print(f"[TestGeneratorNode] Task {task.get('task_id', '?')} has no test_requirements — skipping LLM call")
            return json.dumps([])

        context = {
            "task_id": task.get("task_id", ""), "title": task.get("title", ""),
            "category": task.get("category", ""), "test_requirements": test_req,
            "files_to_test": [
                {"path": f["path"], "purpose": f.get("purpose", ""),
                "exports": f.get("exports", []),
                "content_preview": f["content"][:2000] if len(f["content"]) > 2000 else f["content"]}
                for f in files
            ],
        }
        prompt = "Generate tests for:\n" + json.dumps(context, indent=2, default=str)
        return call_llm(TEST_GENERATOR_PROMPT, prompt, temperature=0.2)

    def post(self, shared, prep_res, exec_res):
        output_dir = prep_res["output_dir"]
        if prep_res.get("already_done"):
            task = prep_res["task"]
            test_files = []
            for tf in task.get("test_requirements", {}).get("test_files", []):
                content = read_file(tf, output_dir)
                if content:
                    test_files.append({"path": tf, "content": content, "language": "typescript", "target_file": tf.replace(".test.ts", ".ts")})
            shared["generated_test_files"] = test_files
            return "default"
        parsed = parse_llm_json(exec_res)
        try:
            if isinstance(parsed, list):
                shared["generated_test_files"] = parsed
                for f in parsed:
                    write_file(f["path"], f["content"], base_dir=output_dir)
                return "default"
            elif isinstance(parsed, dict) and "tests" in parsed:
                shared["generated_test_files"] = parsed["tests"]
                for f in parsed["tests"]:
                    write_file(f["path"], f["content"], base_dir=output_dir)
                return "default"
            else:
                raise ValueError("Expected array of test file objects")
        except (ValueError, KeyError, TypeError) as e:
            shared["errors"] = shared.get("errors", []) + [f"Test generator invalid structure: {e}"]
            return "error"


class TestExecutorNode(Node):
    """Execute tests using Yarn 4 PnP. Distinguishes compilation errors from assertion failures."""

    def prep(self, shared):
        print("TestExecutorNode")
        print("*" * 70)
        return {"test_files": shared.get("generated_test_files", []), "output_dir": shared.get("output_dir", shared.get("workdir", "."))}

    def exec(self, prep_res):
        test_files, output_dir = prep_res["test_files"], prep_res["output_dir"]
        if not test_files:
            return json.dumps([])
        results = []
        for test_file in test_files:
            test_path, target_file = test_file.get("path", ""), test_file.get("target_file", "")
            exit_code, stdout, stderr = run_yarn_command(["vitest", "run", test_path, "--reporter=verbose"], cwd=output_dir, timeout=180)
            cov_exit, cov_stdout, cov_stderr = run_yarn_command(["vitest", "run", test_path, "--coverage", "--reporter=verbose"], cwd=output_dir, timeout=180)
            coverage = calculate_coverage(cov_stdout + cov_stderr)
            failures = [line.strip() for line in (stdout + stderr).split("\n") if "FAIL" in line or "AssertionError" in line or "Error:" in line]
            results.append({"passed": exit_code == 0 and len(failures) == 0, "test_file": test_path, "target_file": target_file, "failures": failures[:20], "errors": [], "stdout": stdout[-5000:] if len(stdout) > 5000 else stdout, "stderr": stderr[-3000:] if len(stderr) > 3000 else stderr, "coverage": coverage.get("lines", 0), "duration_ms": 0})
        return json.dumps(results)

    def post(self, shared, prep_res, exec_res):
        parsed = parse_llm_json(exec_res)
        if not isinstance(parsed, list):
            shared["errors"] = shared.get("errors", []) + ["Test executor returned unexpected format"]
            return "error"
        shared["test_results"] = parsed

        # ── FIX: empty list is NOT "all passed" unless tests are genuinely not required ──
        if not parsed:
            task = shared.get("current_task", {})
            test_req = task.get("test_requirements", {})
            tests_required = (
                test_req.get("unit_tests", False)
                or test_req.get("integration_tests", False)
                or bool(test_req.get("test_files", []))
            )
            if tests_required:
                tid = task.get("task_id", "?")
                shared["errors"] = shared.get("errors", []) + [
                    f"No tests were generated or executed for {tid} "
                    f"but test_requirements demand them. Source files may be "
                    f"config-only — check CodeGeneratorNode skip logic."
                ]
                return "error"
            # Legitimate: setup/config task with no test_requirements
            shared["_all_tests_passed"] = True
            return "all_passed"

        all_passed = all(r.get("passed", False) for r in parsed)
        shared["_all_tests_passed"] = all_passed
        if all_passed:
            return "all_passed"
        has_compilation_error = any(
            "error TS" in (r.get("stderr", "") + r.get("stdout", "")) or
            "Cannot find module" in (r.get("stderr", "") + r.get("stdout", "")) or
            "SyntaxError" in (r.get("stderr", "") + r.get("stdout", "")) or
            "ReferenceError" in (r.get("stderr", "") + r.get("stdout", ""))
            for r in parsed
        )
        return "error" if has_compilation_error else "has_failures"


class CodeRepairNode(Node):
    """Repair source code based on test failures. Adaptive escalation with fail-stop."""

    def prep(self, shared):
        print("CodeRepairNode")
        print("*" * 70)
        task = shared.get("current_task", {})
        task_id = task.get("task_id", "unknown")
        repair_key = f"_code_repair_state_{task_id}"
        repair_state = shared.get(repair_key, {
            "attempt": 0, "strategies_tried": [],
            "last_failure_fingerprint": None, "last_code_hash": None, "stuck_count": 0
        })
        generated_files = shared.get("generated_files", [])
        test_results = shared.get("test_results", [])
        output_dir = shared.get("output_dir", shared.get("workdir", "."))
        source_failures = map_failures_to_sources(test_results, generated_files)
        current_fingerprint = failure_fingerprint(test_results)
        current_code_hash = code_hash(generated_files)
        is_stuck = (
            repair_state["last_failure_fingerprint"] == current_fingerprint
            and repair_state["last_code_hash"] == current_code_hash
        )
        if is_stuck:
            repair_state["stuck_count"] += 1
        else:
            repair_state["stuck_count"] = 0
        repair_state["last_failure_fingerprint"] = current_fingerprint
        repair_state["last_code_hash"] = current_code_hash
        repair_state["attempt"] += 1

        if repair_state["stuck_count"] >= 5:
            print(f"[CodeRepairNode] EXHAUSTED for {task_id}")
            return {
                "task_id": task_id, "exhausted": True,
                "reason": f"Code repair exhausted after {repair_state['attempt']} attempts.",
                "repair_state": repair_state, "source_failures": source_failures,
                "test_results": test_results, "generated_files": generated_files,
                "task": task,
            }

        strategy = select_repair_strategy(repair_state["stuck_count"])
        repair_state["strategies_tried"].append(strategy)
        return {
            "task_id": task_id, "exhausted": False,
            "repair_state": repair_state, "source_failures": source_failures,
            "generated_files": generated_files, "test_results": test_results,
            "output_dir": output_dir, "strategy": strategy,
            "is_stuck": is_stuck, "task": task,
        }

    def exec(self, prep_res):
        if prep_res.get("exhausted"):
            return json.dumps({
                "exhausted": True, "task_id": prep_res["task_id"],
                "reason": prep_res["reason"],
                "final_files": [{"path": f["path"], "content": f["content"][:500]} for f in prep_res.get("generated_files", []) if "path" in f],
                "final_failures": summarize_failures(prep_res.get("test_results", [])),
            })

        task_id = prep_res["task_id"]
        strategy = prep_res["strategy"]
        source_failures = prep_res["source_failures"]
        generated_files = prep_res["generated_files"]
        test_results = prep_res["test_results"]

        print(f"[CodeRepairNode] Strategy: {strategy} for {task_id} (attempt {prep_res['repair_state']['attempt']})")

        if strategy == "targeted":
            result = exec_targeted_repair(prep_res, source_failures, generated_files)
        elif strategy == "holistic":
            result = exec_holistic_repair(prep_res, source_failures, generated_files, test_results)
        elif strategy == "compilation_focused":
            result = exec_compilation_focused_repair(prep_res, generated_files, test_results)
        elif strategy == "radical":
            result = exec_radical_repair(prep_res, generated_files, test_results)
        elif strategy == "targeted_v2":
            result = exec_targeted_v2_repair(prep_res, source_failures, generated_files, test_results)
        else:
            result = exec_targeted_repair(prep_res, source_failures, generated_files)

        return result

    def post(self, shared, prep_res, exec_res):
        task_id = prep_res["task_id"]
        repair_key = f"_code_repair_state_{task_id}"

        # ── Step 1: Parse LLM output ──
        parsed = parse_llm_json(exec_res)

        # ── Step 2: Check for exhausted signal ──
        if isinstance(parsed, dict) and parsed.get("exhausted"):
            shared[repair_key] = prep_res["repair_state"]
            reason = parsed.get("reason", "Repair exhausted")
            shared["errors"] = shared.get("errors", []) + [f"TASK HALTED — {task_id}: {reason}"]
            shared["_halted_task"] = {
                "task_id": task_id, "node": "CodeRepairNode", "reason": reason,
                "final_files": parsed.get("final_files"),
                "final_failures": parsed.get("final_failures"),
                "strategies_tried": prep_res["repair_state"]["strategies_tried"],
                "total_attempts": prep_res["repair_state"]["attempt"],
            }
            return "exhausted"

        # ── Step 3: Extract file list ──
        files = normalize_file_output(parsed)
        # ── Step 5: Empty array = LLM chose not to change source → route to test repair ──
        if isinstance(files, list) and len(files) == 0:
            print(f"[CodeRepairNode] LLM returned [] for {task_id} — source may be correct, routing to test repair")
            shared[repair_key] = prep_res["repair_state"]
            return "stuck"

        # ── Step 5b: Not a list at all = actual parse failure ──
        if not isinstance(files, list):
            shared["errors"] = shared.get("errors", []) + [
                f"Code repair returned no parseable files for {task_id}. "
                f"Preview: {str(exec_res)[:300]}"
            ]
            return "error"

        # ── Step 6: Validate each file has path + content ──
        validated = []
        for f in files:
            if not isinstance(f, dict):
                continue
            path = f.get("path")
            content = f.get("content")
            if not path or not isinstance(path, str):
                continue
            if content is None:
                content = ""
            validated.append({"path": path, "content": str(content)})

        if not validated:
            shared["errors"] = shared.get("errors", []) + [
                f"Code repair returned {len(files)} item(s) but none had path+content for {task_id}"
            ]
            return "error"

        # ── Step 7: Write and continue ──
        shared[repair_key] = prep_res["repair_state"]
        for f in validated:
            write_file(f["path"], f["content"], base_dir=prep_res["output_dir"])
        shared["generated_files"] = validated
        return "repaired"


class TestRepairNode(Node):
    """Repair failing tests with adaptive strategy. HALT if exhausted."""

    def prep(self, shared):
        print("TestRepairNode")
        print("*" * 70)
        task_id = shared.get("current_task", {}).get("task_id", "unknown")
        repair_key = f"_test_repair_state_{task_id}"
        repair_state = shared.get(repair_key, {"attempt": 0, "last_failure_fingerprint": None, "last_test_hash": None, "stuck_count": 0, "source_flip_count": 0, "strategies_tried": []})
        test_files, test_results, source_files = shared.get("generated_test_files", []), shared.get("test_results", []), shared.get("generated_files", [])
        current_fingerprint, current_test_hash = failure_fingerprint(test_results), code_hash(test_files)
        is_stuck = repair_state["last_failure_fingerprint"] == current_fingerprint and repair_state["last_test_hash"] == current_test_hash
        repair_state["stuck_count"] = repair_state["stuck_count"] + 1 if is_stuck else 0
        repair_state["last_failure_fingerprint"] = current_fingerprint
        repair_state["last_test_hash"] = current_test_hash
        repair_state["attempt"] += 1
        if repair_state["stuck_count"] >= 3 and repair_state["source_flip_count"] >= 1:
            print(f"[TestRepairNode] EXHAUSTED for {task_id}")
            return {"task_id": task_id, "exhausted": True, "reason": f"Test repair exhausted after {repair_state['attempt']} attempts.", "repair_state": repair_state, "test_results": test_results, "test_files": test_files, "source_files": source_files}
        if repair_state["stuck_count"] >= 2:
            repair_state["source_flip_count"] += 1
            repair_state["strategies_tried"].append("source_flip")
            return {"task_id": task_id, "exhausted": False, "source_fix_needed": True, "reason": "Test repair not making progress — tests likely correct, source needs fix", "repair_state": repair_state}
        strategy = "targeted" if repair_state["stuck_count"] == 0 else "holistic"
        repair_state["strategies_tried"].append(strategy)
        return {"task_id": task_id, "exhausted": False, "source_fix_needed": False, "strategy": strategy, "test_results": test_results, "test_files": test_files, "source_files": source_files, "output_dir": shared.get("output_dir", shared.get("workdir", ".")), "repair_state": repair_state}

    def exec(self, prep_res):
        if prep_res.get("exhausted"):
            return json.dumps({"exhausted": True, "task_id": prep_res["task_id"], "reason": prep_res["reason"], "final_tests": [{"path": t["path"], "content": t["content"][:500]} for t in prep_res.get("test_files", [])], "final_failures": summarize_failures(prep_res.get("test_results", []))})
        if prep_res.get("source_fix_needed"):
            return json.dumps({"source_fix_needed": True, "reason": prep_res["reason"], "task_id": prep_res["task_id"]})
        task_id, strategy = prep_res["task_id"], prep_res["strategy"]
        test_file_map = {t.get("path", ""): t for t in prep_res["test_files"]}
        source_file_map = {s.get("path", ""): s for s in prep_res["source_files"]}
        print(f"[TestRepairNode] Strategy: {strategy} for {task_id} (attempt {prep_res['repair_state']['attempt']})")
        if strategy == "holistic":
            return exec_holistic_test_repair(prep_res, test_file_map, source_file_map)
        return exec_targeted_test_repair(prep_res, test_file_map, source_file_map)

    def post(self, shared, prep_res, exec_res):
        task_id, repair_key = prep_res["task_id"], f"_test_repair_state_{prep_res['task_id']}"
        shared[repair_key] = prep_res["repair_state"]
        if isinstance(exec_res, str):
            try:
                check = json.loads(exec_res)
                if isinstance(check, dict) and check.get("source_fix_needed"):
                    shared["errors"] = shared.get("errors", []) + [f"Test repair suggests source fix needed: {check.get('reason', '')}"]
                    return "source_fix"
                if isinstance(check, dict) and check.get("exhausted"):
                    shared["errors"] = shared.get("errors", []) + [f"TASK HALTED — {task_id}: {check.get('reason', 'Test repair exhausted')}"]
                    shared["_halted_task"] = {"task_id": task_id, "node": "TestRepairNode", "reason": check.get("reason"), "final_tests": check.get("final_tests"), "final_failures": check.get("final_failures"), "total_attempts": prep_res["repair_state"]["attempt"]}
                    return "exhausted"
            except (json.JSONDecodeError, TypeError):
                pass
        repaired_tests = parse_llm_json(exec_res)
        if isinstance(repaired_tests, list):
            shared["generated_test_files"] = repaired_tests
            for f in repaired_tests:
                write_file(f["path"], f["content"], base_dir=prep_res["output_dir"])
            return "repaired"
        shared["errors"] = shared.get("errors", []) + [f"Test repair returned invalid format for {task_id}"]
        return "error"
    
    
class TaskFinalizerNode(Node):
    """Mark current task as completed and prepare for next task."""

    def prep(self, shared):
        print("TaskFinalizerNode")
        print("*" * 70)
        task = shared.get("current_task", {})
        task_id = task.get("task_id", "unknown")
        return {
            "task_id": task_id,
            "task_title": task.get("title", ""),
            "generated_files": [f.get("path", "") for f in shared.get("generated_files", [])],
            "test_files": [f.get("path", "") for f in shared.get("generated_test_files", [])],
            "output_dir": shared.get("output_dir", shared.get("workdir", ".")),
        }

    def exec(self, prep_res):
        task_id = prep_res["task_id"]
        files = prep_res["generated_files"]
        tests = prep_res["test_files"]
        return json.dumps({
            "task_id": task_id,
            "title": prep_res["task_title"],
            "files_written": files,
            "tests_written": tests,
            "status": "completed",
        })

    def post(self, shared, prep_res, exec_res):
        task_id = prep_res["task_id"]

        # Mark task as completed
        if "completed_task_ids" not in shared:
            shared["completed_task_ids"] = []
        if task_id not in shared["completed_task_ids"]:
            shared["completed_task_ids"].append(task_id)

        # Remove from failed if it was there
        failed = shared.get("failed_task_ids", [])
        if task_id in failed:
            failed.remove(task_id)
            shared["failed_task_ids"] = failed

        # Update task status in the tasks list
        for t in shared.get("tasks", []):
            if t.get("task_id") == task_id:
                t["status"] = "completed"
                break

        # Clean up repair state for this task
        for key in list(shared.keys()):
            if key.startswith(f"_code_repair_state_{task_id}") or key.startswith(f"_test_repair_state_{task_id}"):
                del shared[key]

        # Clean up transient state
        for key in ("generated_files", "generated_test_files", "test_results",
                     "_all_tests_passed", "current_task", "_task_progress"):
            shared.pop(key, None)

        # Clear non-fatal errors from this task's cycle
        shared["errors"] = [e for e in shared.get("errors", []) if "HALTED" in e or "PERMANENT" in e]

        total = len(shared.get("tasks", []))
        done = len(shared.get("completed_task_ids", []))
        print(f"[TaskFinalizerNode] ✅ {task_id} completed ({done}/{total})")

        # Save progress to disk
        output_dir = shared.get("output_dir", shared.get("workdir", "."))
        doc_dir = os.path.join(output_dir, "doc")
        os.makedirs(doc_dir, exist_ok=True)
        progress_path = os.path.join(doc_dir, "progress.json")
        try:
            with open(progress_path, "w", encoding="utf-8") as f:
                json.dump({
                    "completed_task_ids": shared["completed_task_ids"],
                    "failed_task_ids": shared.get("failed_task_ids", []),
                    "total_tasks": total,
                    "last_completed": task_id,
                }, f, indent=2)
        except Exception:
            pass

        # Save updated tasks list
        tasks_path = os.path.join(doc_dir, "tasks_only.json")
        try:
            with open(tasks_path, "w", encoding="utf-8") as f:
                json.dump(shared.get("tasks", []), f, indent=2, default=str)
        except Exception:
            pass

        return "default"


class DeploymentGeneratorNode(Node):
    """Generate deployment configuration files after all tasks are complete."""

    def prep(self, shared):
        print("DeploymentGeneratorNode")
        print("*" * 70)
        output_dir = shared.get("output_dir", shared.get("workdir", "."))

        # Check what deployment files already exist
        deployment_files = {
            "Dockerfile": os.path.join(output_dir, "Dockerfile"),
            ".dockerignore": os.path.join(output_dir, ".dockerignore"),
            "docker-compose.yml": os.path.join(output_dir, "docker-compose.yml"),
        }
        existing = [name for name, path in deployment_files.items() if os.path.exists(path) and os.path.getsize(path) > 0]
        missing = [name for name, path in deployment_files.items() if not os.path.exists(path) or os.path.getsize(path) == 0]

        # Scan for all generated source files
        all_files = scan_project_files(output_dir)
        has_server = any("server" in f for f in all_files)
        has_src = os.path.exists(os.path.join(output_dir, "src"))

        return {
            "output_dir": output_dir,
            "existing_deployment_files": existing,
            "missing_deployment_files": missing,
            "all_source_files": all_files,
            "has_server_entry": has_server,
            "has_src_dir": has_src,
            "completed_tasks": shared.get("completed_task_ids", []),
            "total_tasks": len(shared.get("tasks", [])),
            "infrastructure_section": shared.get("system_spec", {}).get("infrastructure_section", {}),
        }

    def exec(self, prep_res):
        if not prep_res["missing_deployment_files"] and not prep_res["existing_deployment_files"]:
            return json.dumps([])

        # If all deployment files already exist, skip
        if not prep_res["missing_deployment_files"]:
            print(f"[DeploymentGeneratorNode] All deployment files exist: {prep_res['existing_deployment_files']}")
            return json.dumps([])

        context = {
            "missing_files": prep_res["missing_deployment_files"],
            "existing_files": prep_res["existing_deployment_files"],
            "source_files": prep_res["all_source_files"][:50],
            "has_server_entry": prep_res["has_server_entry"],
            "has_src_dir": prep_res["has_src_dir"],
            "completed_tasks": len(prep_res["completed_tasks"]),
            "total_tasks": prep_res["total_tasks"],
            "infrastructure": prep_res["infrastructure_section"],
            "tech_stack": {
                "runtime": "Node.js 24 LTS",
                "language": "TypeScript 5.x",
                "framework": "Express.js 5.x",
                "database": "MySQL 8.x",
                "cache": "Memcached 1.6.42",
                "queue": "Kafka",
                "package_manager": "Yarn 4.x (PnP)",
            },
        }

        prompt = (
            f"Generate deployment configuration files for a Node.js/TypeScript/Express project.\n\n"
            f"Context:\n{json.dumps(context, indent=2, default=str)}\n\n"
            f"Rules:\n"
            f"- Dockerfile: Multi-stage build (deps → build → production), node:24-alpine base, "
            f"Yarn 4 PnP (NO node_modules), health check, non-root user\n"
            f"- .dockerignore: Exclude .yarn/cache, .pnp.cjs, node_modules, dist, .git, coverage, *.log, .env\n"
            f"- docker-compose.yml: Services for app, mysql, memcached, kafka, zookeeper. "
            f"App service must mount .yarn and .pnp.cjs. Include healthcheck for app.\n"
            f"- Each file must have COMPLETE content — no placeholders, no '...', no TODOs\n\n"
            f"Output a JSON array of file objects: [{{'path': 'Dockerfile', 'content': '...', 'language': 'dockerfile'}}]\n"
            f"Include ONLY the missing files: {prep_res['missing_deployment_files']}"
        )
        return call_llm(
            "You are a DevOps engineer. Generate production-ready deployment configuration.",
            prompt, temperature=0.15
        )

    def post(self, shared, prep_res, exec_res):
        output_dir = prep_res["output_dir"]

        # If nothing to generate, pass through
        if not prep_res["missing_deployment_files"]:
            print("[DeploymentGeneratorNode] No files to generate — skipping")
            return "default"

        parsed = parse_llm_json(exec_res)
        files = normalize_file_output(parsed)

        if not isinstance(files, list) or not files:
            # Non-fatal: deployment files are nice-to-have, not blocking
            print(f"[DeploymentGeneratorNode] Failed to parse deployment files. Preview: {str(exec_res)[:300]}")
            print("[DeploymentGeneratorNode] This is non-fatal — continuing to finalizer")
            shared["errors"] = shared.get("errors", []) + [
                f"WARNING: Deployment file generation failed (non-fatal). "
                f"Manual deployment setup may be needed."
            ]
            return "default"

        written = 0
        for f in files:
            if not isinstance(f, dict) or "path" not in f or "content" not in f:
                continue
            # Don't overwrite existing files
            full_path = os.path.join(output_dir, f["path"])
            if os.path.exists(full_path) and os.path.getsize(full_path) > 0:
                print(f"[DeploymentGeneratorNode] SKIP: '{f['path']}' already exists")
                continue
            write_file(f["path"], f["content"], base_dir=output_dir)
            written += 1
            print(f"[DeploymentGeneratorNode] Wrote: {f['path']}")

        print(f"[DeploymentGeneratorNode] Generated {written} deployment files")
        return "default"


class CodeGenFinalizerNode(Node):
    """Final node: save summary, print stats, clean up."""

    def prep(self, shared):
        print("CodeGenFinalizerNode")
        print("*" * 70)
        output_dir = shared.get("output_dir", shared.get("workdir", "."))
        all_files = scan_project_files(output_dir)

        # Count files by type
        ts_files = [f for f in all_files if f.endswith(".ts") and not f.endswith(".test.ts")]
        test_files = [f for f in all_files if f.endswith(".test.ts")]
        config_files = [f for f in all_files if f.endswith((".json", ".yml", ".yaml", ".js")) and "node_modules" not in f]
        other_files = [f for f in all_files if f not in ts_files and f not in test_files and f not in config_files]

        return {
            "output_dir": output_dir,
            "all_files": all_files,
            "ts_files": ts_files,
            "test_files": test_files,
            "config_files": config_files,
            "other_files": other_files,
            "completed_tasks": shared.get("completed_task_ids", []),
            "failed_tasks": shared.get("failed_task_ids", []),
            "total_tasks": len(shared.get("tasks", [])),
            "errors": shared.get("errors", []),
            "halted_task": shared.get("_halted_task"),
        }

    def exec(self, prep_res):
        completed = len(prep_res["completed_tasks"])
        failed = len(prep_res["failed_tasks"])
        total = prep_res["total_tasks"]
        halted = prep_res["halted_task"]

        summary = {
            "status": "completed" if not halted and completed == total else
                      "partially_completed" if completed > 0 else "failed",
            "total_tasks": total,
            "completed_tasks": completed,
            "failed_tasks": failed,
            "halted_task": halted.get("task_id") if halted else None,
            "halt_reason": halted.get("reason") if halted else None,
            "files_generated": {
                "source_files": len(prep_res["ts_files"]),
                "test_files": len(prep_res["test_files"]),
                "config_files": len(prep_res["config_files"]),
                "other_files": len(prep_res["other_files"]),
                "total": len(prep_res["all_files"]),
            },
            "all_files": prep_res["all_files"],
            "completed_task_ids": prep_res["completed_tasks"],
            "errors_count": len(prep_res["errors"]),
            "errors": [e for e in prep_res["errors"] if "HALTED" in e or "WARNING" in e or "PERMANENT" in e],
        }
        return json.dumps(summary, indent=2)

    def post(self, shared, prep_res, exec_res):
        output_dir = prep_res["output_dir"]
        summary = safe_json_loads(exec_res, {})

        # Save summary to disk
        doc_dir = os.path.join(output_dir, "doc")
        os.makedirs(doc_dir, exist_ok=True)

        summary_path = os.path.join(doc_dir, "code_gen_summary.json")
        try:
            with open(summary_path, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2, default=str)
            print(f"[CodeGenFinalizerNode] Saved summary to {summary_path}")
        except Exception as e:
            print(f"[CodeGenFinalizerNode] Failed to save summary: {e}")

        # Print final report
        print("\n" + "=" * 70)
        print("CODE GENERATION COMPLETE")
        print("=" * 70)

        status = summary.get("status", "unknown")
        status_icon = "✅" if status == "completed" else "⚠️" if status == "partially_completed" else "❌"
        print(f"\n  Status: {status_icon} {status.upper()}")
        print(f"  Tasks:  {summary.get('completed_tasks', 0)}/{summary.get('total_tasks', 0)} completed, {summary.get('failed_tasks', 0)} failed")

        files = summary.get("files_generated", {})
        print(f"\n  Files Generated:")
        print(f"    Source:   {files.get('source_files', 0)}")
        print(f"    Tests:    {files.get('test_files', 0)}")
        print(f"    Config:   {files.get('config_files', 0)}")
        print(f"    Other:    {files.get('other_files', 0)}")
        print(f"    TOTAL:    {files.get('total', 0)}")

        if summary.get("halted_task"):
            print(f"\n  ⛔ HALTED at task: {summary['halted_task']}")
            print(f"     Reason: {summary.get('halt_reason', 'Unknown')}")

        if summary.get("errors"):
            print(f"\n  Errors ({len(summary['errors'])}):")
            for err in summary["errors"][:10]:
                print(f"    - {err[:120]}")

        print("\n" + "=" * 70)

        return "done"