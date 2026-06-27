from pocketflow import Flow, Node
from business_nodes import InputParserNode, ResearcherNode, PMAgentNode, UXAgentNode, BAAgentNode, ReviewAgentNode, CompilerNode
from system_nodes import BusinessSpecLoaderNode, ArchitectNode, DomainModelNode, APIDesignNode, DataDesignNode, IntegrationNode, SecurityNode, InfrastructureNode, ImplementationNode, TechReviewNode, SystemCompilerNode
from task_nodes import SystemSpecLoaderNode, TaskGeneratorNode, TaskPrioritizerNode, CriticalPathNode, TaskCompilerNode
from recheck_repair_nodes import RecheckNode, RepairJSONNode, RepairConsistencyNode, RepairDependenciesNode, RepairCriticalPathNode
from setup_nodes import SetupPlannerNode, SetupFileGeneratorNode, SetupRepairNode, YarnInstallNode, YarnRepairNode, SetupFinalizerNode
from code_gen_nodes import TaskLoaderNode, CodeGeneratorNode, TestGeneratorNode, TestExecutorNode, CodeRepairNode, TestRepairNode, TaskFinalizerNode, DeploymentGeneratorNode, CodeGenFinalizerNode


class EndNode(Node):
    """Terminal node that returns 'next_flow' to signal next workflow."""
    def prep(self, shared):
        return None
    def exec(self, prep_res):
        return None
    def post(self, shared, prep_res, exec_res):
        return "next_flow"


def business_spec_workflow():
    input_parser = InputParserNode()
    researcher = ResearcherNode()
    pm_agent = PMAgentNode()
    ux_agent = UXAgentNode()
    ba_agent = BAAgentNode()
    review_agent = ReviewAgentNode()
    compiler = CompilerNode()

    recheck = RecheckNode()
    repair_json = RepairJSONNode()
    repair_consistency = RepairConsistencyNode()
    end = EndNode()

    # ── Main flow: Max attempt errors bounced directly back to creator nodes ──
    input_parser - "next" >> recheck
    recheck - "seed_valid" >> researcher
    recheck - "seed_repair_json" >> repair_json
    recheck - "seed_repair_consistency" >> repair_consistency
    recheck - "seed_max_attempt_error" >> input_parser

    repair_json - "done" >> recheck
    repair_json - "error" >> recheck
    repair_consistency - "done" >> recheck
    repair_consistency - "error" >> recheck

    researcher - "next" >> recheck
    recheck - "research_valid" >> pm_agent
    recheck - "research_repair_json" >> repair_json
    recheck - "research_repair_consistency" >> repair_consistency
    recheck - "research_max_attempt_error" >> researcher

    pm_agent - "next" >> recheck
    recheck - "pm_section_valid" >> ux_agent
    recheck - "pm_section_repair_json" >> repair_json
    recheck - "pm_section_repair_consistency" >> repair_consistency
    recheck - "pm_section_max_attempt_error" >> pm_agent

    ux_agent - "next" >> recheck
    recheck - "ux_section_valid" >> ba_agent
    recheck - "ux_section_repair_json" >> repair_json
    recheck - "ux_section_repair_consistency" >> repair_consistency
    recheck - "ux_section_max_attempt_error" >> ux_agent

    ba_agent - "next" >> recheck
    recheck - "ba_section_valid" >> review_agent
    recheck - "ba_section_repair_json" >> repair_json
    recheck - "ba_section_repair_consistency" >> repair_consistency
    recheck - "ba_section_max_attempt_error" >> ba_agent

    # ── Feedback loops from Review ──
    review_agent - "pm" >> pm_agent
    review_agent - "ux" >> ux_agent
    review_agent - "ba" >> ba_agent
    review_agent - "pass" >> compiler

    input_parser - "next_flow" >> end
    compiler - "next_flow" >> end

    return Flow(start=input_parser)


def system_spec_workflow():
    loader = BusinessSpecLoaderNode()
    architect = ArchitectNode()
    domain_model = DomainModelNode()
    api_design = APIDesignNode()
    data_design = DataDesignNode()
    integration = IntegrationNode()
    security = SecurityNode()
    infrastructure = InfrastructureNode()
    implementation = ImplementationNode()
    tech_review = TechReviewNode()
    compiler = SystemCompilerNode()

    recheck = RecheckNode()
    repair_json = RepairJSONNode()
    repair_consistency = RepairConsistencyNode()
    end = EndNode()

    # ── Main flow: Max attempt errors bounced directly back to creator nodes ──
    loader - "next" >> recheck
    recheck - "business_spec_valid" >> architect
    recheck - "business_spec_repair_json" >> repair_json
    recheck - "business_spec_repair_consistency" >> repair_consistency

    repair_json - "done" >> recheck
    repair_json - "error" >> recheck
    repair_consistency - "done" >> recheck
    repair_consistency - "error" >> recheck

    architect - "next" >> recheck
    recheck - "architecture_section_valid" >> domain_model
    recheck - "architecture_section_repair_json" >> repair_json
    recheck - "architecture_section_repair_consistency" >> repair_consistency
    recheck - "architecture_section_max_attempt_error" >> architect

    domain_model - "next" >> recheck
    recheck - "domain_model_section_valid" >> api_design
    recheck - "domain_model_section_repair_json" >> repair_json
    recheck - "domain_model_section_repair_consistency" >> repair_consistency
    recheck - "domain_model_section_max_attempt_error" >> domain_model

    api_design - "next" >> recheck
    recheck - "api_design_section_valid" >> data_design
    recheck - "api_design_section_repair_json" >> repair_json
    recheck - "api_design_section_repair_consistency" >> repair_consistency
    recheck - "api_design_section_max_attempt_error" >> api_design

    data_design - "next" >> recheck
    recheck - "data_design_section_valid" >> integration
    recheck - "data_design_section_repair_json" >> repair_json
    recheck - "data_design_section_repair_consistency" >> repair_consistency
    recheck - "data_design_section_max_attempt_error" >> data_design

    integration - "next" >> recheck
    recheck - "integration_section_valid" >> security
    recheck - "integration_section_repair_json" >> repair_json
    recheck - "integration_section_repair_consistency" >> repair_consistency
    recheck - "integration_section_max_attempt_error" >> integration

    security - "next" >> recheck
    recheck - "security_section_valid" >> infrastructure
    recheck - "security_section_repair_json" >> repair_json
    recheck - "security_section_repair_consistency" >> repair_consistency
    recheck - "security_section_max_attempt_error" >> security

    infrastructure - "next" >> recheck
    recheck - "infrastructure_section_valid" >> implementation
    recheck - "infrastructure_section_repair_json" >> repair_json
    recheck - "infrastructure_section_repair_consistency" >> repair_consistency
    recheck - "infrastructure_section_max_attempt_error" >> infrastructure

    implementation - "next" >> recheck
    recheck - "implementation_section_valid" >> tech_review
    recheck - "implementation_section_repair_json" >> repair_json
    recheck - "implementation_section_repair_consistency" >> repair_consistency
    recheck - "implementation_section_max_attempt_error" >> implementation

    # ── Feedback loops from TechReview ──
    tech_review - "architecture" >> architect
    tech_review - "domain_model" >> domain_model
    tech_review - "api_design" >> api_design
    tech_review - "data_design" >> data_design
    tech_review - "integration" >> integration
    tech_review - "security" >> security
    tech_review - "infrastructure" >> infrastructure
    tech_review - "implementation" >> implementation
    tech_review - "pass" >> compiler

    loader - "next_flow" >> end
    compiler - "next_flow" >> end

    return Flow(start=loader)


def tasks_implementation_workflow():
    loader = SystemSpecLoaderNode()
    generator = TaskGeneratorNode()
    prioritizer = TaskPrioritizerNode()
    critical_path = CriticalPathNode()
    compiler = TaskCompilerNode()
    end = EndNode()

    recheck = RecheckNode()
    repair_json = RepairJSONNode()
    repair_deps = RepairDependenciesNode()
    repair_cp = RepairCriticalPathNode()

    # ── Stage 1: Load system spec ──
    loader >> recheck
    recheck - "tasks_loader_valid" >> generator
    recheck - "tasks_loader_repair_json" >> repair_json
    recheck - "tasks_loader_repair_dependencies" >> repair_deps

    repair_json - "done" >> recheck
    repair_json - "error" >> recheck
    repair_deps >> recheck

    # ── Stage 2: Generate tasks ──
    generator >> recheck
    recheck - "tasks_generator_valid" >> prioritizer
    recheck - "tasks_generator_repair_json" >> repair_json
    recheck - "tasks_generator_repair_dependencies" >> repair_deps
    recheck - "tasks_generator_error" >> generator
    recheck - "tasks_generator_max_attempt_error" >> generator

    # ── Stage 3: Prioritize tasks ──
    prioritizer >> recheck
    recheck - "tasks_prioritizer_valid" >> critical_path
    recheck - "tasks_prioritizer_repair_json" >> repair_json
    recheck - "tasks_prioritizer_repair_dependencies" >> repair_deps
    recheck - "tasks_prioritizer_error" >> prioritizer
    recheck - "tasks_prioritizer_max_attempt_error" >> prioritizer

    # ── Stage 4: Critical path analysis ──
    critical_path >> recheck
    recheck - "critical_path_valid" >> compiler
    recheck - "critical_path_repair_json" >> repair_json
    recheck - "critical_path_repair_critical_path" >> repair_cp
    recheck - "critical_path_error" >> critical_path
    recheck - "critical_path_max_attempt_error" >> critical_path
    repair_cp >> recheck

    # ── Stage 5: Compile ──
    compiler >> recheck
    recheck - "implementation_plan_valid" >> end
    recheck - "implementation_plan_repair_json" >> repair_json
    recheck - "implementation_plan_error" >> compiler
    recheck - "implementation_plan_max_attempt_error" >> compiler

    loader - "next_flow" >> end
    compiler - "next_flow" >> end

    return Flow(start=loader)


def setup_workflow():
    planner = SetupPlannerNode()
    file_gen = SetupFileGeneratorNode()
    repair = SetupRepairNode()
    yarn_install = YarnInstallNode()
    yarn_repair = YarnRepairNode()
    finalizer = SetupFinalizerNode()

    # ── Planner ──
    planner - "next" >> file_gen
    planner - "skip" >> finalizer
    planner - "error" >> repair

    # ── File Generator ──
    file_gen - "next" >> file_gen      # Loop for multiple files
    file_gen - "all_done" >> yarn_install
    file_gen - "error" >> repair

    # ── Setup Repair (for planner/file_gen failures) ──
    repair - "planner_repaired" >> planner
    repair - "filegen_repaired" >> file_gen
    repair - "error" >> planner       # Restart from scratch

    # ── Yarn Install ──
    # "next" = success → finalizer
    # "error" = any failure → yarn_repair (NO self-retry)
    yarn_install - "next" >> finalizer
    yarn_install - "error" >> yarn_repair

    # ── Yarn Repair ──
    # "repaired" = fixed something → retry install (infinite loop until success)
    # "error" = couldn't fix → retry install anyway (user wants NO bypass)
    yarn_repair - "repaired" >> yarn_install
    yarn_repair - "error" >> yarn_install  # NEVER bypass, always retry

    return Flow(start=planner)


def code_gen_workflow():
    """
    Code Generation Workflow — FAIL-STOP DESIGN.

    When any node fails, the flow HALTS at EndNode.
    All diagnostic state is preserved in shared for inspection.
    Never skips to next task on failure.

    Flow:
    TaskLoader → CodeGenerator → TestGenerator → TestExecutor
    → [if compilation error] CodeRepair → re-test
    → [if assertion failures] CodeRepair → re-test (with escalation)
    → [if code repair stuck] TestRepair → re-test
    → [if tests correct, source wrong] back to CodeRepair (reset state)
    → [if all exhausted] HALT at EndNode
    → [all tests pass] TaskFinalizer → loop back to TaskLoader
    → [all tasks complete] DeploymentGenerator → CodeGenFinalizer → EndNode
    """
    loader = TaskLoaderNode()
    generator = CodeGeneratorNode()
    test_generator = TestGeneratorNode()
    test_executor = TestExecutorNode()
    code_repair = CodeRepairNode()
    test_repair = TestRepairNode()
    task_finalizer = TaskFinalizerNode()
    deployment = DeploymentGeneratorNode()
    finalizer = CodeGenFinalizerNode()
    end = EndNode()

    # ── Task selection ──
    loader - "default" >> generator
    loader - "all_complete" >> deployment
    loader - "error" >> end  # HALT: cannot proceed without tasks

    # ── Code generation ──
    generator - "default" >> test_generator
    generator - "error" >> end  # HALT: can't generate code

    # ── Test generation ──
    test_generator - "default" >> test_executor
    test_generator - "error" >> end  # HALT: can't generate tests

    # ── Test execution ──
    # "all_passed" → finalize task
    # "has_failures" → assertion failures → try code repair
    # "error" → compilation/import errors → definitely code repair
    test_executor - "all_passed" >> task_finalizer
    test_executor - "has_failures" >> code_repair
    test_executor - "error" >> code_repair

    # ── Code repair with adaptive escalation ──
    # "repaired" → fixed something → re-test
    # "stuck" → no progress → try test repair (maybe tests are wrong)
    # "exhausted" → all strategies failed → HALT
    # "error" → unexpected failure → HALT
    code_repair - "repaired" >> test_executor
    code_repair - "stuck" >> test_repair
    code_repair - "exhausted" >> end
    code_repair - "error" >> end

    # ── Test repair ──
    # "repaired" → fixed tests → re-test
    # "source_fix_needed" → tests are correct, source is wrong → back to code repair (fresh state)
    # "exhausted" → both directions failed → HALT
    # "error" → unexpected failure → HALT
    test_repair - "repaired" >> test_executor
    test_repair - "source_fix_needed" >> code_repair
    test_repair - "exhausted" >> end
    test_repair - "error" >> end

    # ── Task completion → next task ──
    task_finalizer - "default" >> loader

    # ── Deployment (when all tasks complete) ──
    deployment - "default" >> finalizer
    deployment - "error" >> end  # HALT: deployment failed

    # ── Finalizer ──
    finalizer - "done" >> end

    return Flow(start=loader)
