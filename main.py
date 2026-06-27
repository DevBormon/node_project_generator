from flow import business_spec_workflow, system_spec_workflow, tasks_implementation_workflow, setup_workflow, code_gen_workflow
import os, sys
from pocketflow import Flow

dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WORKDIR = os.path.join(dir, "JobPortal_v2")
# print(WORKDIR)
# sys.exit(1)

def main():
    idea = """
    I want to build a global multi-tenant job portal web platform where companies can post jobs, manage hiring teams, and handle applicants in a structured RBAC-based system. The system should support multiple employer roles inside each company including Company Owner, Company Admin, Recruiter, and Hiring Manager with fine-grained permissions and audit logs. Job seekers can apply to jobs with a limit of 3 free applications, after which a subscription is required; once the subscription expires, users are blocked only from applying to new jobs but can still browse and save jobs. The platform should include a full abuse and moderation system with reporting for jobs, companies, promotions, user profiles, and messages, along with optional rule-based or automated fraud/spam detection.

    The system should be designed with a future-ready messaging module where database tables exist in MySQL 8 but messaging features are not exposed in V1. Authentication should use JWT access tokens with HTTP-only refresh cookies and support mandatory TOTP-based 2FA for admins, employers, and business promoters. The platform should be single-tenant (one global system with multiple companies), support multi-currency billing (INR, USD, EUR, GBP) with an admin-controlled default currency and user-based currency preference, and use separate subscription systems per user role (job seeker, employer, promoter) instead of a unified plan.

    The backend should be built using REST APIs with MySQL 8 as the primary database and include a fully normalized schema with proper indexing for scalability. The system should support Razorpay for payments, AWS S3 for file storage, and a clean RBAC permission architecture to ensure enterprise-level security, scalability, and future extensibility for features like messaging and advanced hiring workflows.
    """
    
    shared = {
        "workdir": WORKDIR,
        "input": idea,
        "business_spec": {},
        "system_spec": {},
        "implementation_plan": {},
        "type": '', # business/system/task
        "feedback_history": [],
        "tasks": [],
        "errors": []
    }
    
    
    business_flow = business_spec_workflow()
    
    system_flow = system_spec_workflow()
    task_flow = tasks_implementation_workflow()
    setup_flow = setup_workflow()
    code_gen_flow = code_gen_workflow()
    
    business_flow - "next_flow" >> system_flow
    system_flow - "next_flow" >> task_flow
    
    task_flow - "next_flow" >> setup_flow
    setup_flow - "next_flow" >> code_gen_flow
    
    master_flow = Flow(start=business_flow)
    
    master_flow.run(shared)
    
    # Results
    print("\n\n\n=== Final Business Specification ===")
    # print(shared.get("business_spec_compiled", shared.get("business_spec", "No spec generated")))
    print(f"\n=== Quality Score: {shared.get('quality_score', 0)}/10")
    
    print("\n=== System Specification Generated ===")
    for section in ["architecture_section", "domain_model_section", "api_design_section",
                    "data_design_section", "integration_section", "security_section",
                    "infrastructure_section", "implementation_section"]:
        status = "✓" if shared.get(section) else "✗"
        print(f"  {status} {section}")
    
    print("\n=== Implementation Tasks Generated ===")
    print(f"Total Tasks: {len(shared.get('tasks', []))}")
    
    if shared.get("errors"):
        print(f"\n=== Errors ({len(shared['errors'])}):")
        for err in shared["errors"]:
            print(f"  - {err}")
    

if __name__ == "__main__":
    main()