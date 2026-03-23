"""Pineapple Pipeline v2 -- Full Path E2E Test (ALL 10 stages + Gemini)

Runs: intake -> strategic_review -> architecture -> plan -> setup ->
      build -> verify -> review -> ship -> evolve
No interrupt_before gates -- runs non-stop with pre-populated human_approvals.
"""
import json, sys, traceback, uuid, time, os
from datetime import datetime
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from pineapple.agents.intake import intake_node
from pineapple.agents.strategic_review import strategic_review_node
from pineapple.agents.architecture import architecture_node
from pineapple.agents.planner import plan_node
from pineapple.agents.setup import setup_node
from pineapple.agents.builder import builder_node
from pineapple.agents.verifier import verifier_node
from pineapple.agents.reviewer import reviewer_node
from pineapple.agents.shipper import ship_node
from pineapple.agents.evolver import evolve_node
from pineapple.gates import route_by_path
from pineapple.state import PipelineStage, PipelineState


def review_gate_fixed(state):
    ac = state.get("attempt_counts", {})
    ba = ac.get("build", 0)
    if ba >= 3:
        print(f"  [Gate] Circuit breaker: {ba} build attempts, forcing pass")
        return "pass"
    if state.get("cost_total_usd", 0.0) > 200.0:
        return "fail"
    rr = state.get("review_result")
    if rr:
        if rr.get("critical_issues", []):
            return "retry"
    return "pass"


def builder_with_counter(state):
    result = builder_node(state)
    ac = dict(state.get("attempt_counts", {}))
    ac["build"] = ac.get("build", 0) + 1
    result["attempt_counts"] = ac
    return result


STAGE_ARTIFACTS = {
    "intake": "context_bundle",
    "strategic_review": "strategic_brief",
    "architecture": "design_spec",
    "plan": "task_plan",
    "setup": "workspace_info",
    "build": "build_results",
    "verify": "verify_record",
    "review": "review_result",
    "ship": "ship_result",
    "evolve": "evolve_report",
}

STAGE_ORDER = [
    "intake", "strategic_review", "architecture", "plan",
    "setup", "build", "verify", "review", "ship", "evolve",
]


def _dg(d, k, default=None):
    if d is None:
        return default
    return d.get(k, default)


def _artifact_details(g, log):
    cb = g("context_bundle")
    if cb:
        log("[0] context_bundle:")
        log("    project_type: " + str(_dg(cb, "project_type")))
        log("    classification: " + str(_dg(cb, "classification")))
        log("    context_files: " + str(len(_dg(cb, "context_files", []))))
    else:
        log("[0] context_bundle: MISSING")
    log()

    sb = g("strategic_brief")
    if sb:
        log("[1] strategic_brief:")
        log("    what: " + str(_dg(sb, "what", ""))[:120])
        log("    why: " + str(_dg(sb, "why", ""))[:120])
        log("    not_building: " + str(_dg(sb, "not_building", [])))
        log("    assumptions: " + str(len(_dg(sb, "assumptions", []))) + " items")
        log("    open_questions: " + str(len(_dg(sb, "open_questions", []))) + " items")
    else:
        log("[1] strategic_brief: MISSING")
    log()

    ds = g("design_spec")
    if ds:
        log("[2] design_spec:")
        log("    title: " + str(_dg(ds, "title", ""))[:120])
        log("    summary: " + str(_dg(ds, "summary", ""))[:120] + "...")
        comps = _dg(ds, "components", [])
        log("    components: " + str(len(comps)))
        for comp in comps[:5]:
            log("      - " + str(_dg(comp, "name")) + ": " + str(_dg(comp, "description", ""))[:80])
        log("    technology_choices: " + str(_dg(ds, "technology_choices", {})))
    else:
        log("[2] design_spec: MISSING")
    log()

    tp = g("task_plan")
    if tp:
        tasks = _dg(tp, "tasks", [])
        log("[3] task_plan:")
        log("    tasks: " + str(len(tasks)))
        for t in tasks[:8]:
            log("      - " + str(_dg(t, "id")) + ": " + str(_dg(t, "description", ""))[:80] + " [" + str(_dg(t, "complexity")) + "]")
        est = _dg(tp, "total_estimated_cost_usd", 0)
        log(f"    total_estimated_cost: ${est:.2f}")
    else:
        log("[3] task_plan: MISSING")
    log()

    wi = g("workspace_info")
    if wi:
        log("[4] workspace_info:")
        log("    project_name: " + str(_dg(wi, "project_name")))
        log("    branch: " + str(_dg(wi, "branch")))
        log("    tools: " + str(_dg(wi, "tools_available", {})))
        log("    setup_complete: " + str(_dg(wi, "setup_complete")))
    else:
        log("[4] workspace_info: MISSING")
    log()

    br = g("build_results") or []
    log("[5] build_results: " + str(len(br)) + " task(s)")
    for i, r in enumerate(br[:8]):
        log(f"    [{i}] task_id={_dg(r, 'task_id')} status={_dg(r, 'status')} commits={len(_dg(r, 'commits', []))} errors={_dg(r, 'errors', [])}")
    log()

    vr = g("verify_record")
    if vr:
        log("[6] verify_record:")
        log("    all_green: " + str(_dg(vr, "all_green")))
        for la in _dg(vr, "layers", []):
            log("    L" + str(_dg(la, "layer")) + ": " + str(_dg(la, "name")) + " = " + str(_dg(la, "status")))
    else:
        log("[6] verify_record: MISSING")
    log()

    rr = g("review_result")
    if rr:
        log("[7] review_result:")
        log("    verdict: " + str(_dg(rr, "verdict")))
        log("    critical_issues: " + str(_dg(rr, "critical_issues", [])))
        log("    important_issues: " + str(_dg(rr, "important_issues", [])))
        log("    minor_issues: " + str(_dg(rr, "minor_issues", [])))
    else:
        log("[7] review_result: MISSING")
    log()

    sr = g("ship_result")
    if sr:
        log("[8] ship_result:")
        log("    action: " + str(_dg(sr, "action")))
        log("    pr_url: " + str(_dg(sr, "pr_url")))
    else:
        log("[8] ship_result: MISSING")
    log()

    er = g("evolve_report")
    if er:
        log("[9] evolve_report:")
        log("    session_handoff_path: " + str(_dg(er, "session_handoff_path")))
        log("    bible_updated: " + str(_dg(er, "bible_updated")))
        log("    decisions_logged: " + str(_dg(er, "decisions_logged", [])))
        log("    memory_extractions: " + str(_dg(er, "memory_extractions", [])))
    else:
        log("[9] evolve_report: MISSING")
    log()


def main():
    output_lines = []

    def log(msg=""):
        print(msg)
        output_lines.append(msg)

    log("=" * 70)
    log("PINEAPPLE PIPELINE v2 -- FULL PATH E2E TEST (ALL 10 STAGES + GEMINI)")
    log(f"Timestamp: {datetime.now().isoformat()}")
    log("=" * 70)
    log()

    log("[SETUP] Building full-path graph WITHOUT interrupt_before gates...")
    graph = StateGraph(PipelineState)

    graph.add_node("intake", intake_node)
    graph.add_node("strategic_review", strategic_review_node)
    graph.add_node("architecture", architecture_node)
    graph.add_node("plan", plan_node)
    graph.add_node("setup", setup_node)
    graph.add_node("build", builder_with_counter)
    graph.add_node("verify", verifier_node)
    graph.add_node("review", reviewer_node)
    graph.add_node("ship", ship_node)
    graph.add_node("evolve", evolve_node)

    graph.set_entry_point("intake")

    graph.add_conditional_edges("intake", route_by_path, {
        "strategic_review": "strategic_review",
        "plan": "plan",
        "build": "build",
    })

    graph.add_edge("strategic_review", "architecture")
    graph.add_edge("architecture", "plan")
    graph.add_edge("plan", "setup")
    graph.add_edge("setup", "build")
    graph.add_edge("build", "verify")
    graph.add_edge("verify", "review")

    graph.add_conditional_edges("review", review_gate_fixed, {
        "pass": "ship",
        "retry": "build",
        "fail": "ship",
    })

    graph.add_edge("ship", "evolve")
    graph.add_edge("evolve", END)

    pipeline = graph.compile(checkpointer=MemorySaver())
    log("[SETUP] Graph compiled successfully (no interrupt gates)")
    log()

    run_id = str(uuid.uuid4())
    initial_state = {
        "run_id": run_id,
        "request": "Build a CLI tool that converts markdown files to styled HTML with syntax highlighting and TOC",
        "project_name": "",
        "branch": "",
        "path": "full",
        "current_stage": PipelineStage.INTAKE.value,
        "context_bundle": None,
        "strategic_brief": None,
        "design_spec": None,
        "task_plan": None,
        "workspace_info": None,
        "build_results": [],
        "verify_record": None,
        "review_result": None,
        "ship_result": None,
        "evolve_report": None,
        "attempt_counts": {},
        "human_approvals": {
            "strategic_review": True,
            "architecture": True,
            "plan": True,
            "ship": True,
        },
        "cost_total_usd": 0.0,
        "errors": [],
        "messages": [],
    }

    config = {"configurable": {"thread_id": run_id}}

    log(f"[RUN] Run ID: {run_id}")
    log("[RUN] Path: full (all 10 stages)")
    log("[RUN] Request: Build a CLI tool that converts markdown to styled HTML...")
    log("[RUN] Human approvals pre-populated: strategic_review, architecture, plan, ship")
    log()

    start_time = time.time()
    try:
        result = pipeline.invoke(initial_state, config)
        elapsed = time.time() - start_time

        log()
        log("=" * 70)
        log("PIPELINE COMPLETED")
        log("=" * 70)

        def g(k):
            return result.get(k)

        log()
        log("Final stage: " + str(g("current_stage")))
        log("Path: " + str(g("path")))
        log("Project name: " + str(g("project_name")))
        cost = g("cost_total_usd") or 0
        log(f"Cost: ${cost:.4f}")
        errs = g("errors") or []
        log("Errors: " + str(len(errs)))
        ac = g("attempt_counts") or {}
        log("Build attempts: " + str(ac.get("build", 0)))
        log(f"Elapsed: {elapsed:.1f}s")
        log()

        log("=" * 70)
        log("STAGE-BY-STAGE REPORT")
        log("=" * 70)

        pass_count = 0
        fail_count = 0

        for stage in STAGE_ORDER:
            artifact_key = STAGE_ARTIFACTS[stage]
            artifact = g(artifact_key)
            if artifact_key == "build_results":
                has_artifact = isinstance(artifact, list) and len(artifact) > 0
            else:
                has_artifact = artifact is not None
            status = "PASS" if has_artifact else "FAIL"
            if has_artifact:
                pass_count += 1
            else:
                fail_count += 1
            stage_num = STAGE_ORDER.index(stage)
            log(f"  Stage {stage_num}: {stage:20s} -> {artifact_key:20s} = {status}")

        log()
        log(f"PASSED: {pass_count}/10")
        log(f"FAILED: {fail_count}/10")
        log()

        log("=" * 70)
        log("ARTIFACT DETAILS")
        log("=" * 70)
        log()
        _artifact_details(g, log)

        errs = g("errors") or []
        if errs:
            log("=" * 70)
            log("ERRORS (" + str(len(errs)) + "):")
            log("=" * 70)
            for e in errs:
                log("  [" + str(_dg(e, "stage")) + "] " + str(_dg(e, "message", ""))[:120])
                log("    recoverable: " + str(_dg(e, "recoverable")))
            log()

        log("=" * 70)
        all_10 = pass_count == 10
        yn = "YES" if all_10 else "NO"
        vd = "PASS" if all_10 else "PARTIAL"
        log(f"ALL 10 STAGES PRODUCED ARTIFACTS: {yn} ({pass_count}/10)")
        log("FINAL STAGE: " + str(g("current_stage")))
        log(f"ELAPSED: {elapsed:.1f}s")
        log(f"TOTAL COST: ${cost:.4f}")
        log(f"VERDICT: {vd}")
        log("=" * 70)

    except Exception:
        elapsed = time.time() - start_time
        log()
        log("=" * 70)
        log("PIPELINE FAILED")
        log("=" * 70)
        tb = traceback.format_exc()
        log(tb)

    evidence_path = "docs/E2E_TEST_FULL_EVIDENCE.md"
    try:
        os.makedirs("docs", exist_ok=True)
        with open(evidence_path, "w", encoding="utf-8") as f:
            f.write("# Pineapple Pipeline v2 -- Full Path E2E Test Evidence\n\n")
            ts = datetime.now().isoformat()
            f.write("Generated: " + ts + "\n\n")
            f.write("```\n")
            for line in output_lines:
                f.write(line + "\n")
            f.write("```\n")
        print("\n[EVIDENCE] Saved to " + evidence_path)
    except Exception as e:
        print("\n[EVIDENCE] Failed to save: " + str(e))


if __name__ == "__main__":
    main()
