"""
Setup Nodes for Project Foundation Generation.
Fully dynamic, LLM decides everything. Errors route to repair, not finalizer.
"""

import json, os, re, shutil
from pocketflow import Node
from utils import (
    call_llm, write_file, run_shell_command, is_yarn_pnp_ready, resolve_dependencies,
    safe_json_loads, parse_llm_json, build_retry_context, extract_signals, discover_yarn_cmd, PROJECT_SETUP_PROMPT, TECH_STACK
)


class SetupPlannerNode(Node):
    """Ask LLM which setup files this project needs."""

    def prep(self, shared):
        print("SetupPlannerNode")
        print("*" * 70)
        workdir = shared.get("workdir", ".")
        if shared.get("setup_finalized") is True:
            return {"skip": True, "reason": "Setup workflow already completed successfully"}
        critical_files = ["package.json", "tsconfig.json", ".yarnrc.yml"]
        all_critical_exist = all(
            os.path.exists(os.path.join(workdir, f)) and os.path.getsize(os.path.join(workdir, f)) > 0
            for f in critical_files
        )
        if all_critical_exist and is_yarn_pnp_ready(workdir):
            print("[SetupPlannerNode] Critical files exist and Yarn PnP is ready — skipping setup")
            shared["setup_finalized"] = True
            shared["yarn_install_status"] = "success"
            return {"skip": True, "reason": "Critical files exist and Yarn PnP is ready"}
        errors = shared.get("errors", [])
        return {
            "skip": False, "output_dir": workdir, "signals": extract_signals(shared),
            "is_retry": len(errors) > 0, "error_log": errors,
        }

    def exec(self, prep_res):
        if prep_res.get("skip"):
            return json.dumps({"skipped": True})
        prompt = (
            f"You are a project setup architect. Your job is to decide ONLY the "
            f"MINIMAL configuration files needed to bootstrap a project before any code is written.\n\n"
            f"Signals:\n{json.dumps(prep_res['signals'], indent=2, default=str)}\n"
            f"{build_retry_context(prep_res['is_retry'], prep_res['error_log'], verbose=False)}\n\n"
            f"STRICT RULES - VIOLATING ANY RULE IS AN ERROR:\n"
            f"1. ONLY configuration/setup files. NEVER source code files (*.ts, *.js in src/ or test/)\n"
            f"2. NEVER include: controllers, services, entities, routes, migrations, tests, or any src/ files\n"
            f"3. ONLY these categories are allowed:\n"
            f"   - Package manifest: package.json\n"
            f"   - TypeScript config: tsconfig.json\n"
            f"   - Package manager config: .yarnrc.yml (ONLY if using Yarn PnP)\n"
            f"   - Test runner config: vitest.config.ts (ONLY if testing framework in signals)\n"
            f"   - Git config: .gitignore, .editorconfig\n"
            f"   - Environment template: .env.example\n"
            f"   - Docker config: Dockerfile, .dockerignore (ONLY if Docker in signals)\n"
            f"   - CI/CD config: .github/workflows/*.yml (ONLY if CI/CD in signals)\n"
            f"   - README: README.md\n"
            f"4. MAXIMUM 10 files. If you generate more than 10, you have failed.\n"
            f"5. If a category is not mentioned in signals, DO NOT include it.\n\n"
            f"Output ONLY a JSON array of objects with keys: path, purpose.\n"
            f"Example: [{{'path': 'package.json', 'purpose': 'Project manifest'}}]"
        )
        return call_llm("You are a senior DevOps engineer who decides project structure.", prompt, temperature=0.2)

    def post(self, shared, prep_res, exec_res):
        if prep_res.get("skip"):
            shared["setup_status"] = "skipped"
            shared["yarn_install_status"] = "skipped"
            return "skip"
        shared["_setup_planner_raw"] = exec_res
        parsed = parse_llm_json(exec_res)
        if not isinstance(parsed, list) or not parsed:
            shared["errors"] = shared.get("errors", []) + ["Setup planner returned empty or invalid file list"]
            return "error"
        files_needed = [{"path": item["path"], "purpose": item.get("purpose", "Configuration file")} for item in parsed if isinstance(item, dict) and "path" in item]
        if not files_needed:
            shared["errors"] = shared.get("errors", []) + ["Setup planner returned no valid files"]
            return "error"
        for path, purpose in {"package.json": "Project manifest", "tsconfig.json": "TypeScript configuration"}.items():
            if not any(f["path"] == path for f in files_needed):
                files_needed.append({"path": path, "purpose": purpose})
                print(f"[SetupPlannerNode] FORCE-INJECTED critical file: {path}")
        print(f"[SetupPlannerNode] LLM decided {len(files_needed)} files: {[f['path'] for f in files_needed]}")
        shared["_setup_files_needed"] = files_needed
        shared["_setup_files_done"] = []
        shared["_setup_current_index"] = 0
        shared["_setup_signals"] = prep_res["signals"]
        shared["errors"] = []
        return "next"


class SetupFileGeneratorNode(Node):
    """Generate ONE setup file at a time."""

    def prep(self, shared):
        print("SetupFileGeneratorNode")
        print("*" * 70)
        files_needed = shared.get("_setup_files_needed", [])
        current_index = shared.get("_setup_current_index", 0)
        output_dir = shared.get("workdir", ".")
        if files_needed:
            all_exist = all(
                os.path.exists(os.path.join(output_dir, f.get("path", ""))) and os.path.getsize(os.path.join(output_dir, f.get("path", ""))) > 0
                for f in files_needed
            )
            if all_exist:
                print(f"[SetupFileGeneratorNode] ALL {len(files_needed)} files already exist — skipping entire batch")
                shared["_setup_files_done"] = [f["path"] for f in files_needed]
                shared["_setup_current_index"] = len(files_needed)
                return {"all_done": True, "all_skipped": True}
        if current_index >= len(files_needed):
            return {"all_done": True}
        current_file = files_needed[current_index]
        full_path = os.path.join(output_dir, current_file["path"])
        if os.path.exists(full_path) and os.path.getsize(full_path) > 0:
            return {"skip": True, "file": current_file}
        errors = shared.get("errors", [])
        return {
            "file": current_file, "index": current_index, "total": len(files_needed),
            "signals": shared.get("_setup_signals", {}), "output_dir": output_dir,
            "is_retry": len(errors) > 0, "error_log": errors,
        }

    def exec(self, prep_res):
        if prep_res.get("all_done"):
            return json.dumps({"all_done": True})
        if prep_res.get("skip"):
            print(f"SKIP PATH: {prep_res['file']['path']}")
            return json.dumps({"skipped": True, "path": prep_res["file"]["path"]})
        file_info, signals = prep_res["file"], prep_res["signals"]
        context = {
            "file_path": file_info["path"], "purpose": file_info["purpose"],
            "project_name": signals.get("project_name", "api-service"),
            "domain": signals.get("domain", ""), "tech_stack": signals.get("tech_stack", {}),
            "bounded_contexts": signals.get("bounded_contexts", []),
            "integration_systems": signals.get("integration_systems", []),
            "task_categories": signals.get("task_categories", []),
        }
        prompt = (
            f"Generate ONLY the file: {file_info['path']}\n\n"
            f"Purpose: {file_info['purpose']}\n\n"
            f"Project Context:\n{json.dumps(context, indent=2, default=str)}\n"
            f"{build_retry_context(prep_res['is_retry'], prep_res['error_log'], verbose=False)}\n\n"
            f"Rules:\n"
            f"- Output ONLY a JSON object with keys: path, content, language\n"
            f"- The 'content' value must be the COMPLETE file content as a string\n"
            f"- No markdown, no explanations, no code fences\n"
            f"- Make content specific to this project's domain and tech stack"
        )
        return call_llm(PROJECT_SETUP_PROMPT, prompt, temperature=0.2)

    def post(self, shared, prep_res, exec_res):
        if prep_res.get("all_done") and prep_res.get("all_skipped"):
            print("[SetupFileGeneratorNode] All files verified existing — proceeding to yarn install")
            shared["errors"] = []
            return "all_done"
        if prep_res.get("all_done"):
            return "all_done"
        if prep_res.get("skip"):
            shared["_setup_files_done"].append(prep_res["file"]["path"])
            shared["_setup_current_index"] += 1
            return "next"
        shared["_setup_filegen_raw"] = exec_res
        parsed = parse_llm_json(exec_res)
        if not isinstance(parsed, dict) or "path" not in parsed or "content" not in parsed:
            shared["errors"] = shared.get("errors", []) + [f"Invalid output format for {prep_res['file']['path']}"]
            return "error"
        write_file(parsed["path"], parsed["content"], base_dir=prep_res["output_dir"])
        shared["_setup_files_done"].append(parsed["path"])
        shared["_setup_current_index"] += 1
        shared["errors"] = []
        return "next" if shared["_setup_current_index"] < len(shared["_setup_files_needed"]) else "all_done"


class SetupRepairNode(Node):
    """Repair setup JSON using the raw LLM response."""

    def prep(self, shared):
        print("SetupRepairNode")
        print("*" * 70)
        return {
            "raw_planner": shared.get("_setup_planner_raw", ""),
            "raw_filegen": shared.get("_setup_filegen_raw", ""),
            "errors": shared.get("errors", []),
            "attempt": shared.get("_setup_repair_attempt", 0),
        }

    def exec(self, prep_res):
        raw = prep_res["raw_filegen"] or prep_res["raw_planner"]
        prompt = (
            f"The previous LLM output failed to parse as valid JSON.\n\n"
            f"Errors: {json.dumps(prep_res['errors'])}\n\n"
            f"Raw output (may be truncated or malformed):\n{raw}\n\n"
            f"Fix ONLY the JSON syntax issues. Preserve all content. "
            f"Output ONLY valid JSON with no markdown, no explanations."
        )
        return call_llm("You are a JSON repair specialist.", prompt, temperature=0.1)

    def post(self, shared, prep_res, exec_res):
        shared["_setup_repair_attempt"] = prep_res["attempt"] + 1
        parsed = parse_llm_json(exec_res)
        if parsed is None:
            shared["errors"] = shared.get("errors", []) + ["Setup repair failed: still invalid JSON"]
            return "error"
        if shared.get("_setup_filegen_raw"):
            shared["_setup_filegen_raw"] = exec_res
            return "filegen_repaired"
        else:
            shared["_setup_planner_raw"] = exec_res
            return "planner_repaired"


class YarnInstallNode(Node):
    """Run yarn add with pre-flight validation."""

    def prep(self, shared):
        print("YarnInstallNode")
        print("*" * 70)
        output_dir = shared.get("workdir", ".")
        if shared.get("yarn_install_status") == "success":
            return {"output_dir": output_dir, "skip": True}
        if is_yarn_pnp_ready(output_dir):
            shared["yarn_install_status"] = "success"
            return {"output_dir": output_dir, "skip": True}
        if shared.get("yarn_install_status") == "failed_permanently":
            return {"output_dir": output_dir, "skip": True, "permanent_failure": True}
        return {
            "output_dir": output_dir,
            "attempt": shared.get("_yarn_install_attempt", 0),
            "extra_args": shared.get("_yarn_extra_args", []),
            "skipped_packages": shared.get("_skipped_packages", set()),
        }

    def exec(self, prep_res):
        if prep_res.get("skip"):
            return json.dumps({"success": True, "skipped": True})

        output_dir = prep_res["output_dir"]
        pkg_path = os.path.join(output_dir, "package.json")

        # 1. Validate package.json
        if not os.path.exists(pkg_path):
            return json.dumps({"success": False, "error_type": "missing_package_json",
                               "stderr": "package.json does not exist", "stdout": ""})
        try:
            with open(pkg_path, "r", encoding="utf-8") as f:
                pkg = json.load(f)
            if not isinstance(pkg, dict):
                raise ValueError("not a dict")
        except (json.JSONDecodeError, ValueError) as e:
            return json.dumps({"success": False, "error_type": "corrupt_package_json",
                               "stderr": f"package.json corrupt: {e}", "stdout": ""})

        # 2. Clear deps — yarn add will populate them
        pkg["dependencies"] = {}
        pkg["devDependencies"] = {}
        if "packageManager" not in pkg:
            pkg["packageManager"] = "yarn@4.5.1"
        with open(pkg_path, "w", encoding="utf-8") as f:
            json.dump(pkg, f, indent=2)

        # 3. Corepack
        for cmd in (["corepack", "enable"], ["corepack", "prepare", "yarn@stable", "--activate"]):
            exit_code, stdout, stderr = run_shell_command(cmd)
            if exit_code != 0:
                return json.dumps({"success": False, "error_type": "corepack_failed",
                                   "stderr": stderr, "stdout": stdout})

        # 4. Find yarn
        yarn_cmd = discover_yarn_cmd()
        if not yarn_cmd:
            return json.dumps({"success": False, "error_type": "yarn_not_found",
                               "stderr": "yarn not found after corepack prepare", "stdout": ""})

        # 5. Fix .yarnrc.yml before any yarn command
        yarnrc_path = os.path.join(output_dir, ".yarnrc.yml")
        yarnrc = ""
        if os.path.exists(yarnrc_path):
            with open(yarnrc_path, "r", encoding="utf-8") as f:
                yarnrc = f.read()

        changed = False
        # Remove yarnPath if target file doesn't exist
        yarnpath_match = re.search(r'^yarnPath:\s*(.+)$', yarnrc, re.MULTILINE)
        if yarnpath_match:
            ref = yarnpath_match.group(1).strip().strip('"').strip("'")
            if not os.path.exists(os.path.join(output_dir, ref)):
                yarnrc = re.sub(r'^yarnPath:.*$\n?', '', yarnrc, flags=re.MULTILINE)
                changed = True
        if "nodeLinker: pnp" not in yarnrc:
            yarnrc = re.sub(r'^nodeLinker:.*$', 'nodeLinker: pnp', yarnrc, flags=re.MULTILINE)
            if "nodeLinker: pnp" not in yarnrc:
                yarnrc += "\nnodeLinker: pnp\n"
            changed = True
        if "pnpMode: strict" not in yarnrc:
            yarnrc += "pnpMode: strict\n"
            changed = True
        if changed:
            with open(yarnrc_path, "w", encoding="utf-8") as f:
                f.write(yarnrc)

        # 6. Ensure local yarn binary
        releases_dir = os.path.join(output_dir, ".yarn", "releases")
        has_release = (
            os.path.exists(releases_dir)
            and any(f.endswith(".cjs") for f in os.listdir(releases_dir))
        )
        if not has_release:
            cmd = yarn_cmd + ["set", "version", "stable"]
            exit_code, stdout, stderr = run_shell_command(cmd, cwd=output_dir, timeout=180)
            if exit_code != 0:
                return json.dumps({"success": False, "error_type": "yarn_set_version_failed",
                                   "stderr": stderr, "stdout": stdout})

        # 7. Clean stale artifacts on retry
        if prep_res["attempt"] > 0:
            for stale in ("yarn.lock", ".pnp.cjs", ".pnp.loader.mjs"):
                p = os.path.join(output_dir, stale)
                if os.path.exists(p):
                    os.remove(p)
            cache_dir = os.path.join(output_dir, ".yarn", "cache")
            if os.path.exists(cache_dir):
                shutil.rmtree(cache_dir)

        # ── 8. Write deps from resolver into package.json, yarn install, pin versions ──
        resolved = resolve_dependencies(TECH_STACK)
        skipped = prep_res.get("skipped_packages", set())
        pkg["dependencies"] = {
            name: "*" for name in resolved["dependencies"] if name not in skipped
        }
        pkg["devDependencies"] = {
            name: "*" for name in resolved["devDependencies"] if name not in skipped
        }
        with open(pkg_path, "w", encoding="utf-8") as f:
            json.dump(pkg, f, indent=2)

        no_lockfile = not os.path.exists(os.path.join(output_dir, "yarn.lock"))
        cmd = yarn_cmd + ["install"]
        if no_lockfile:
            cmd.append("--no-immutable")
        cmd.extend(prep_res.get("extra_args", []))

        print(f"[YarnInstallNode] yarn install")
        exit_code, stdout, stderr = run_shell_command(cmd, cwd=output_dir, timeout=300)
        if exit_code != 0:
            return json.dumps({
                "success": False, "error_type": "yarn_install_failed",
                "exit_code": exit_code, "stderr": stderr, "stdout": stdout,
            })

        # Pin exact versions from yarn.lock into package.json
        lock_path = os.path.join(output_dir, "yarn.lock")
        if os.path.exists(lock_path):
            with open(lock_path, "r", encoding="utf-8") as f:
                lock_content = f.read()
            for section in ("dependencies", "devDependencies"):
                pinned = {}
                for name in resolved[section]:
                    if name in skipped:
                        continue
                    m = re.search(
                        rf'^"{re.escape(name)}@npm:\*":\s*\n\s*version:\s*([^\s]+)',
                        lock_content, re.MULTILINE,
                    )
                    if m:
                        pinned[name] = m.group(1)
                if pinned:
                    pkg[section] = pinned
            if pkg.get("dependencies") or pkg.get("devDependencies"):
                with open(pkg_path, "w", encoding="utf-8") as f:
                    json.dump(pkg, f, indent=2)
                print(f"[YarnInstallNode] Pinned exact versions from yarn.lock")

        # 9. Verify PnP
        if not is_yarn_pnp_ready(output_dir):
            return json.dumps({"success": False, "error_type": "pnp_not_ready",
                               "stderr": ".pnp.cjs not created", "stdout": ""})

        return json.dumps({"success": True})

    def post(self, shared, prep_res, exec_res):
        result = safe_json_loads(exec_res, {})
        if prep_res.get("permanent_failure"):
            return "next"
        if result.get("skipped"):
            shared["yarn_install_status"] = "skipped"
            return "next"
        if result.get("success"):
            shared["yarn_install_status"] = "success"
            shared["_yarn_install_attempt"] = 0
            shared["_yarn_extra_args"] = []
            shared["_corrupted_binary_attempts"] = 0
            shared["errors"] = []
            return "next"
        shared["_yarn_install_attempt"] = prep_res.get("attempt", 0) + 1
        error_type = result.get("error_type", "unknown")
        shared["errors"] = shared.get("errors", []) + [
            f"[{error_type}] {result.get('stderr', '')[:800]}"
        ]
        shared["_last_yarn_error"] = result
        return "error"


class YarnRepairNode(Node):
    """Repair yarn install issues. Escalates corrupted binary 3 times then halts."""
    MAX_REPAIR_ATTEMPTS = 5

    def prep(self, shared):
        print("YarnRepairNode")
        print("*" * 70)
        last_error = shared.get("_last_yarn_error", {})
        if not isinstance(last_error, dict):
            last_error = {}
        output_dir = shared.get("workdir", ".")

        file_state = {}
        for fname in ("package.json", ".yarnrc.yml", "yarn.lock", ".pnp.cjs"):
            fpath = os.path.join(output_dir, fname)
            if os.path.exists(fpath):
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        file_state[fname] = f.read()[:2000]
                except Exception:
                    file_state[fname] = "<unreadable>"
            else:
                file_state[fname] = "<missing>"

        releases_dir = os.path.join(output_dir, ".yarn", "releases")
        file_state["releases_dir_exists"] = os.path.exists(releases_dir)
        if os.path.exists(releases_dir):
            file_state["release_files"] = os.listdir(releases_dir)

        cb_attempts = shared.get("_corrupted_binary_attempts", 0) + 1
        shared["_corrupted_binary_attempts"] = cb_attempts

        return {
            "output_dir": output_dir,
            "last_error": last_error,
            "last_stderr": last_error.get("stderr", ""),
            "last_stdout": last_error.get("stdout", ""),
            "error_type": last_error.get("error_type", "unknown"),
            "file_state": file_state,
            "attempt": shared.get("_yarn_repair_attempt", 0),
            "corrupted_binary_attempts": cb_attempts,
        }

    def exec(self, prep_res):
        output_dir = prep_res["output_dir"]
        stderr = prep_res["last_stderr"]
        stdout = prep_res["last_stdout"]
        error_type = prep_res["error_type"]
        combined = (stderr + " " + stdout).lower()

        # ── ALWAYS print what we received ──
        print(f"[YarnRepairNode] error_type={error_type}")
        print(f"[YarnRepairNode] stderr={stderr[:300]}")
        print(f"[YarnRepairNode] stdout={stdout[:300]}")

        # ── Network error ──
        if any(k in combined for k in (
            "econnrefused", "etimedout", "enotfound", "network",
            "unable to connect", "fetch failed", "getaddrinfo",
            "request timeout", "socket hang up",
        )):
            print("[YarnRepairNode] → network_retry")
            return json.dumps({
                "action": "network_retry", "reason": "Network failure",
                "extra_args": ["--network-timeout", "600000"],
            })

        # ── Permission / Disk ──
        if any(k in combined for k in (
            "permission denied", "eacces", "eperm", "enospc", "no space left",
        )):
            reason = ("Disk full" if "enospc" in combined or "no space" in combined
                      else "Permission denied")
            print(f"[YarnRepairNode] → manual_fix ({reason})")
            return json.dumps({"action": "manual_fix", "reason": reason})

        # ── YN0082 ──
        yn0082 = re.findall(
            r'YN0082:\s*│\s*(@?[^@\s]+)@npm:[^\s:]+:\s*No candidates found', combined)
        if yn0082:
            print(f"[YarnRepairNode] → YN0082: {yn0082}")
            pkg_path = os.path.join(output_dir, "package.json")
            if os.path.exists(pkg_path):
                try:
                    with open(pkg_path, "r", encoding="utf-8") as f:
                        pkg = json.load(f)
                    for section in ("dependencies", "devDependencies"):
                        for bad in yn0082:
                            if bad in (pkg.get(section) or {}):
                                del pkg[section][bad]
                    with open(pkg_path, "w", encoding="utf-8") as f:
                        json.dump(pkg, f, indent=2)
                except (json.JSONDecodeError, IOError):
                    pass
            for s in ("yarn.lock", ".pnp.cjs", ".pnp.loader.mjs"):
                p = os.path.join(output_dir, s)
                if os.path.exists(p):
                    os.remove(p)
            return json.dumps({"action": "delete_lockfile", "reason": f"YN0082: {yn0082}"})

                # ── YN0035: Package not found (404) ──
        
        yn0035 = re.findall(r'YN0035:\s*│\s*(@?[^@\s]+)@npm:[^\s:]+:\s*Package not found', combined)
        if yn0035:
            print(f"[YarnRepairNode] → YN0035: {yn0035}")
            return json.dumps({
                "action": "skip_package",
                "failed_package": yn0035[0],
                "reason": f"Package '{yn0035[0]}' not found on registry (404)",
            })
            
        # ── Peer dependency ──
        if "peer dependency" in combined:
            print("[YarnRepairNode] → peer dependency")
            pkg_content = "{}"
            pkg_path = os.path.join(output_dir, "package.json")
            if os.path.exists(pkg_path):
                try:
                    with open(pkg_path, "r", encoding="utf-8") as f:
                        pkg_content = f.read()
                except Exception:
                    pass
            prompt = (
                f"Peer dependency error:\n{stderr}\n\n"
                f"package.json:\n```json\n{pkg_content}\n```\n\n"
                f"Add missing peer deps. Output JSON: "
                f"{{'path':'package.json','content':'...'}}"
            )
            return call_llm("Fix peer dependencies.", prompt, temperature=0.1)

        # ── Cache corruption ──
        if any(k in combined for k in ("checksum", "corrupted", "integrity", "bad archive")):
            print("[YarnRepairNode] → cache corruption")
            for p in (os.path.join(output_dir, ".pnp.cjs"),
                      os.path.join(output_dir, ".pnp.loader.mjs")):
                if os.path.exists(p):
                    os.remove(p)
            cache_dir = os.path.join(output_dir, ".yarn", "cache")
            if os.path.exists(cache_dir):
                shutil.rmtree(cache_dir)
            return json.dumps({"action": "clear_cache", "reason": "Cache corruption"})

        # ── Lockfile conflict ──
        if "lockfile" in combined or "immutable" in combined:
            print("[YarnRepairNode] → lockfile conflict")
            lock_path = os.path.join(output_dir, "yarn.lock")
            if os.path.exists(lock_path):
                os.remove(lock_path)
            return json.dumps({"action": "delete_lockfile", "reason": "Lockfile conflict"})

        # ── nodeLinker wrong ──
        if "node_modules" in combined:
            print("[YarnRepairNode] → nodeLinker fix")
            yarnrc_path = os.path.join(output_dir, ".yarnrc.yml")
            if os.path.exists(yarnrc_path):
                with open(yarnrc_path, "r", encoding="utf-8") as f:
                    content = f.read()
                content = re.sub(r'^nodeLinker:.*$', 'nodeLinker: pnp',
                                 content, flags=re.MULTILINE)
                if "nodeLinker: pnp" not in content:
                    content += "\nnodeLinker: pnp\n"
                with open(yarnrc_path, "w", encoding="utf-8") as f:
                    f.write(content)
            return json.dumps({"action": "fix_nodelinker", "reason": "Forced pnp"})

        # ═══════════════════════════════════════════════════════════
        # FALLBACK: error_type
        # ═══════════════════════════════════════════════════════════

        if error_type in ("missing_package_json", "corrupt_package_json"):
            print(f"[YarnRepairNode] → manual_fix ({error_type})")
            return json.dumps({"action": "manual_fix", "reason": error_type})

        if error_type in ("corepack_failed", "yarn_not_found",
                          "corepack_enable_failed", "corepack_prepare_failed"):
            print(f"[YarnRepairNode] → corepack_refresh ({error_type})")
            run_shell_command(["npm", "install", "-g", "corepack"], timeout=120)
            run_shell_command(["corepack", "disable"])
            run_shell_command(["corepack", "enable"])
            run_shell_command(
                ["corepack", "prepare", "yarn@stable", "--activate"], timeout=120)
            return json.dumps({"action": "corepack_refresh", "reason": error_type})

        if error_type == "yarn_set_version_failed":
            print(f"[YarnRepairNode] → redownload_yarn ({error_type})")
            releases_dir = os.path.join(output_dir, ".yarn", "releases")
            if os.path.exists(releases_dir):
                shutil.rmtree(releases_dir)
            yarnrc_path = os.path.join(output_dir, ".yarnrc.yml")
            if os.path.exists(yarnrc_path):
                with open(yarnrc_path, "r", encoding="utf-8") as f:
                    content = f.read()
                content = re.sub(r'^yarnPath:.*$\n?', '', content, flags=re.MULTILINE)
                with open(yarnrc_path, "w", encoding="utf-8") as f:
                    f.write(content)
            run_shell_command(["npm", "install", "-g", "corepack"], timeout=120)
            run_shell_command(["corepack", "disable"])
            run_shell_command(["corepack", "enable"])
            run_shell_command(
                ["corepack", "prepare", "yarn@stable", "--activate"], timeout=120)
            return json.dumps({"action": "redownload_yarn", "reason": error_type})

        if error_type in ("missing_yarn_releases", "missing_yarn_binary", "yarn_not_functional"):
            print(f"[YarnRepairNode] → redownload_yarn ({error_type})")
            releases_dir = os.path.join(output_dir, ".yarn", "releases")
            if os.path.exists(releases_dir):
                shutil.rmtree(releases_dir)
            return json.dumps({"action": "redownload_yarn", "reason": error_type})

        if error_type == "yarn_add_failed":
            failed_pkg = prep_res["last_error"].get("failed_package", "unknown")
            if failed_pkg and failed_pkg != "unknown":
                print(f"[YarnRepairNode] → skip_package ({failed_pkg})")
                return json.dumps({
                    "action": "skip_package", "failed_package": failed_pkg,
                    "reason": f"Skipping '{failed_pkg}'",
                })

        if error_type == "pnp_not_ready":
            print("[YarnRepairNode] → clear_cache (pnp_not_ready)")
            for s in ("yarn.lock", ".pnp.cjs", ".pnp.loader.mjs"):
                p = os.path.join(output_dir, s)
                if os.path.exists(p):
                    os.remove(p)
            return json.dumps({"action": "clear_cache", "reason": "PnP incomplete"})

        # ═══════════════════════════════════════════════════════════
        # CATCH-ALL: yarn_install_failed or anything unmatched
        # Delete ALL state — .yarn/, .pnp, lockfile, cache
        # If this doesn't work, the error is environmental
        # ═══════════════════════════════════════════════════════════
        print(f"[YarnRepairNode] → catch-all: nuke everything")

        yarn_dir = os.path.join(output_dir, ".yarn")
        if os.path.exists(yarn_dir):
            shutil.rmtree(yarn_dir)
        for s in ("yarn.lock", ".pnp.cjs", ".pnp.loader.mjs"):
            p = os.path.join(output_dir, s)
            if os.path.exists(p):
                os.remove(p)
        for cache_dir in (
            os.path.expanduser("~/.cache/node/corepack"),
            os.path.expanduser("~/Library/Caches/node/corepack"),
        ):
            if os.path.exists(cache_dir):
                shutil.rmtree(cache_dir)

        run_shell_command(["npm", "install", "-g", "corepack"], timeout=120)
        run_shell_command(["corepack", "disable"])
        run_shell_command(["corepack", "enable"])
        run_shell_command(
            ["corepack", "prepare", "yarn@stable", "--activate"], timeout=120)

        return json.dumps({
            "action": "delete_lockfile",
            "reason": f"Catch-all: nuked all state for {error_type}",
        })

    def post(self, shared, prep_res, exec_res):
        shared["_yarn_repair_attempt"] = prep_res["attempt"] + 1

        if shared["_yarn_repair_attempt"] > self.MAX_REPAIR_ATTEMPTS:
            shared["yarn_install_status"] = "failed_permanently"
            shared["_yarn_repair_attempt"] = 0
            shared["_yarn_install_attempt"] = 0
            shared["errors"] = [
                f"PERMANENT FAILURE: Yarn repair exhausted after "
                f"{self.MAX_REPAIR_ATTEMPTS} attempts"
            ]
            return "repaired"

        action_result = None
        if isinstance(exec_res, str):
            try:
                action_result = json.loads(exec_res)
            except (json.JSONDecodeError, TypeError):
                action_result = parse_llm_json(exec_res)
        elif isinstance(exec_res, dict):
            action_result = exec_res

        if not isinstance(action_result, dict):
            shared["errors"] = shared.get("errors", []) + [
                f"Yarn repair invalid format (attempt {prep_res['attempt']+1})"
            ]
            return "error"

        action = action_result.get("action", "")

        # manual_fix with permanent=True → break loop
        if action == "manual_fix":
            if action_result.get("permanent"):
                shared["yarn_install_status"] = "failed_permanently"
                shared["_yarn_install_attempt"] = 0
                shared["errors"] = [
                    f"PERMANENT FAILURE: {action_result.get('reason', '')}"
                ]
                return "repaired"
            shared["errors"] = shared.get("errors", []) + [
                f"MANUAL FIX: {action_result.get('reason', '')}"
            ]
            return "error"

        # LLM returned fixed package.json content
        if action not in (
            "redownload_yarn", "network_retry", "delete_lockfile",
            "clear_cache", "fix_nodelinker", "corepack_refresh", "skip_package",
        ) and "content" in action_result:
            write_file("package.json", action_result["content"],
                       base_dir=prep_res["output_dir"])

        if action == "skip_package":
            failed_pkg = action_result.get("failed_package")
            if failed_pkg:
                if "_skipped_packages" not in shared:
                    shared["_skipped_packages"] = set()
                shared["_skipped_packages"].add(failed_pkg)

        if action == "network_retry":
            shared["_yarn_extra_args"] = action_result.get("extra_args", [])

        shared["_yarn_install_attempt"] = 0
        shared["yarn_install_status"] = ""
        shared["errors"] = []
        return "repaired"


class SetupFinalizerNode(Node):
    """Verify all files exist AND yarn install succeeded."""

    def prep(self, shared):
        print("SetupFinalizerNode")
        print("*" * 70)
        return {
            "output_dir": shared.get("workdir", "."),
            "setup_files": shared.get("_setup_files_done", []),
            "yarn_install_status": shared.get("yarn_install_status", "unknown"),
        }

    def exec(self, prep_res):
        output_dir = prep_res["output_dir"]

        # 1. Check yarn install actually succeeded
        status = prep_res["yarn_install_status"]
        if status not in ("success", "skipped"):
            return json.dumps({
                "all_present": False,
                "missing_files": [],
                "yarn_failed": True,
                "reason": f"yarn_install_status is '{status}', not 'success'",
            })

        # 2. Check deps are populated in package.json
        if status == "success":
            pkg_path = os.path.join(output_dir, "package.json")
            if os.path.exists(pkg_path):
                try:
                    with open(pkg_path, "r", encoding="utf-8") as f:
                        pkg = json.load(f)
                    deps = pkg.get("dependencies", {})
                    dev_deps = pkg.get("devDependencies", {})
                    if not deps and not dev_deps:
                        return json.dumps({
                            "all_present": False,
                            "missing_files": [],
                            "yarn_failed": True,
                            "reason": "yarn install reported success but "
                                      "dependencies are empty",
                        })
                except (json.JSONDecodeError, IOError):
                    pass

        # 3. Check setup files exist
        missing = [f for f in prep_res["setup_files"]
                   if not os.path.exists(os.path.join(output_dir, f))]
        return json.dumps({
            "all_present": len(missing) == 0,
            "missing_files": missing,
        })

    def post(self, shared, prep_res, exec_res):
        result = safe_json_loads(exec_res, {})
        if not result.get("all_present"):
            reason = result.get("reason", f"Missing files: {result.get('missing_files', [])}")
            shared["errors"] = shared.get("errors", []) + [f"Setup failed: {reason}"]
            return "error"
        shared["setup_finalized"] = True
        return "next_flow"