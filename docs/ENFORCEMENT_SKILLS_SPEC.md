# Pineapple Pipeline Enforcement Skills System -- Technical Specification

> **Version:** 1.0.0
> **Date:** 2026-03-23
> **Status:** Spec complete, implementation pending
> **Trigger:** 2026-03-23 brutal honesty audit found 33 issues in v2 pipeline despite having 7 feedback memories, 16 hookify rules, and 10 dogfood lessons -- all advisory, all ignored

---

## Executive Summary

We have a compliance problem, not a knowledge problem. Claude knows the rules (10 dogfood lessons, 7 feedback memories), reads them into context, and ignores them. The 2026-03-23 audit found the builder does not write code to disk, the verifier checks the wrong directory, the shipper reads a field nobody sets, 288 "passing" tests test v1 dead code, and cost tracking reports $0 for all Gemini calls. Every single one of these was predicted by existing feedback.

Memory is advisory. Hookify is brittle (stale cache, wrong fields, Windows path issues). Neither enforces.

This spec defines 6 enforcement skills that are loaded into context and define enforceable processes with evidence requirements. Skills are the primary enforcement layer. Hookify becomes a thin safety net (3 rules). Memory becomes context-only (user profile, project state, tool preferences).

---

## Architecture

```
+------------------------------------------------------------------+
|                     ENFORCEMENT STACK                             |
|                                                                   |
|  Layer 1: SKILLS (primary enforcement)                            |
|  +---------------------------------------------------------+     |
|  | /verify-done        -- feature completion evidence       |     |
|  | /verify-outputs     -- file existence after build        |     |
|  | /verify-state-flow  -- field read/write consistency      |     |
|  | /verify-tests       -- honest test coverage reporting    |     |
|  | /verify-cost        -- real cost tracking verification   |     |
|  | /honest-status      -- evidence-backed progress reports  |     |
|  +---------------------------------------------------------+     |
|       |                                                           |
|       | Skills produce evidence files at                          |
|       | .pineapple/evidence/<feature>.json                        |
|       v                                                           |
|  Layer 2: HOOKIFY STOP RULES (thin safety net, 3 rules)          |
|  +---------------------------------------------------------+     |
|  | STOP commit without evidence file                        |     |
|  | STOP task completion without evidence                    |     |
|  | STOP push/PR without /verify-done run                   |     |
|  +---------------------------------------------------------+     |
|       |                                                           |
|       | Hookify checks for evidence existence only.               |
|       | It does NOT evaluate quality -- skills do that.            |
|       v                                                           |
|  Layer 3: MEMORY (context only)                                   |
|  +---------------------------------------------------------+     |
|  | MEMORY.md       -- user profile, project state           |     |
|  | decisions.md    -- locked choices                         |     |
|  | projects/*.yaml -- per-project state                     |     |
|  | hardware.md     -- tool paths, env vars                  |     |
|  +---------------------------------------------------------+     |
+------------------------------------------------------------------+
```

### Why This Ordering

| Layer | Failure mode | Mitigation |
|-------|-------------|------------|
| Memory alone | Claude reads rules, ignores them | Happens every session |
| Hookify alone | Stale cache, wrong field names, Windows path bugs | Broke 3 times in 2 weeks |
| Skills alone | Claude could skip invoking the skill | Hookify blocks if no evidence |
| Skills + Hookify | Skills define process, hookify blocks without evidence | Two independent enforcement points |

### Evidence File Format

All skills produce evidence files at `.pineapple/evidence/<feature-slug>.json`. This is the single source of truth that hookify checks for.

```json
{
  "feature": "builder-writes-to-disk",
  "skill": "/verify-done",
  "timestamp": "2026-03-23T14:32:00Z",
  "status": "WORKING",
  "evidence": [
    {
      "check": "file_exists",
      "target": "src/pineapple/agents/builder.py",
      "result": true,
      "detail": "File exists, 247 lines, last modified 2026-03-23T14:30:00Z"
    },
    {
      "check": "behavior_test",
      "command": "python -c \"from pineapple.agents.builder import BuilderAgent; ...\"",
      "exit_code": 0,
      "stdout_snippet": "Wrote 3 files to /tmp/test-workspace/...",
      "result": true
    }
  ],
  "documents_cross_referenced": [
    "docs/PINEAPPLE_V2_SPEC.md",
    "docs/superpowers/skills/pineapple/SKILL.md"
  ],
  "verdict": "WORKING -- builder creates real files on disk with correct content"
}
```

### Status Vocabulary (Mandatory)

| Status | Definition | Can proceed? |
|--------|-----------|-------------|
| **WORKING** | Runs with real inputs, produces correct output, tested end-to-end | Yes |
| **WIRED** | Library imported and called, but behavior not verified end-to-end | No |
| **STUBBED** | Function exists but does not do what its name says | No |
| **FAKE** | Returns hardcoded/placeholder data that looks real but is not | No |

The word "MET" is banned. Every feature is one of these four statuses.

---

## Skill 1: /verify-done

### Purpose

Before marking ANY feature, task, criterion, or gap as complete, run this skill to produce evidence that the feature actually works.

### When to Invoke

- Before marking a success criterion as met
- Before closing a gap in a project bible
- Before claiming "feature X is done" to the user
- Before advancing past Stage 6 (Verify) in the pipeline

### Inputs

| Input | Type | Required | Description |
|-------|------|----------|-------------|
| `feature` | string | Yes | Human-readable name of the feature being verified |
| `expected_behavior` | string | Yes | What the feature should do when working correctly |
| `test_command` | string | No | Specific command to run (skill infers if omitted) |
| `spec_file` | path | No | Path to spec/plan that defines expected behavior |

### Process

```
1. LOAD CONTEXT
   - Read the spec/plan file (if provided)
   - Identify which source files implement the feature
   - Identify which documents define the expected behavior

2. VERIFY FILE EXISTENCE
   - Check that all implementation files exist on disk
   - Check that files are non-empty
   - Check that files contain the expected functions/classes (not just imports)

3. RUN WITH REAL INPUTS
   - Construct a test command that exercises the feature
   - Run it in a subprocess
   - Capture stdout, stderr, exit code
   - If it requires infrastructure (DB, API key), note what is missing

4. CHECK REAL OUTPUTS
   - After running: do output files exist? Do DB records exist?
   - Are outputs correct? (not just non-empty, but structurally valid)
   - Does the output match what the spec says it should produce?

5. CROSS-REFERENCE DOCUMENTS
   - Read ALL relevant docs (spec, plan, handoff, dogfood report)
   - Check: does the code do what the spec says?
   - Check: does the spec match the user's intent?
   - List every document checked in the evidence file

6. RATE AND RECORD
   - Assign status: WORKING / WIRED / STUBBED / FAKE
   - Write evidence file to .pineapple/evidence/<feature-slug>.json
   - If STUBBED or FAKE: BLOCK proceeding, list what is missing
```

### Outputs

- Evidence file at `.pineapple/evidence/<feature-slug>.json`
- Console report with status and evidence summary
- BLOCK signal if status is STUBBED or FAKE

### What It Prevents

| Audit Issue | How |
|-------------|-----|
| C-1: Fake circuit breaker (PyBreaker imported, never triggers) | Runs PyBreaker with 3+ failures, checks if circuit opens |
| H-1: Fake cost tracking ($0 for Gemini) | Makes a real API call, checks cost > $0 |
| H-3: Builder does not build (metadata only, no files on disk) | Runs builder, checks files exist on disk |
| All "MET but not working" claims | Forces real execution before any completion claim |

### Evidence Format

See common evidence format above. `/verify-done` evidence includes:
- `evidence[].check`: one of `file_exists`, `behavior_test`, `output_check`, `doc_cross_ref`
- `evidence[].command`: the exact command run (reproducible)
- `evidence[].result`: boolean
- `evidence[].detail`: human-readable explanation

---

## Skill 2: /verify-outputs

### Purpose

After ANY build or implementation agent completes, verify that the files it claims to have created or modified actually exist on disk with the expected content.

### When to Invoke

- Immediately after a Stage 5 (Build) coder agent reports completion
- After any subagent claims to have written files
- After scaffold/template generation
- Before advancing from Stage 5 to Stage 6

### Inputs

| Input | Type | Required | Description |
|-------|------|----------|-------------|
| `claimed_files` | list[path] | Yes | Files the agent claims to have created/modified |
| `workspace` | path | No | Root directory to check (defaults to current worktree) |
| `agent_name` | string | No | Which agent made the claims (for reporting) |

### Process

```
1. CHECK FILE EXISTENCE
   For each claimed file:
   - Does the file exist at the claimed path?
   - Is the file non-empty? (>0 bytes)
   - What is the file size and line count?

2. CHECK FILE CONTENT
   For each existing file:
   - Does it contain the expected imports/classes/functions?
   - Is it a real implementation or just a stub with `pass`/`...`/`raise NotImplementedError`?
   - Does it match the task description? (e.g., "implement builder" -> file has a build() method)

3. CHECK GIT STATUS
   - Run `git status` and `git diff --stat`
   - Verify that claimed files appear in the diff
   - Flag any files that are claimed but not in git (not staged, not modified)

4. CHECK FOR PHANTOM FILES
   - If agent claimed to create a directory: does the directory exist?
   - If agent claimed to modify a config: is the config actually changed?
   - Look for the workspace path itself -- does it exist? (prevents phantom workspace bug)

5. RECORD
   - Write evidence file with per-file results
   - BLOCK if any claimed file does not exist or is a stub
```

### Outputs

- Evidence file at `.pineapple/evidence/outputs-<agent-name>-<timestamp>.json`
- Per-file existence/content report
- BLOCK signal if claimed outputs do not match reality

### What It Prevents

| Audit Issue | How |
|-------------|-----|
| H-3: Builder metadata only, no files on disk | Checks files exist after builder runs |
| H-6: Scaffold creates nothing | Checks directory and files exist after scaffold |
| C-2: Phantom workspace | Checks workspace path exists on the filesystem |

### Evidence Format

```json
{
  "skill": "/verify-outputs",
  "agent": "builder-stage5",
  "timestamp": "2026-03-23T14:35:00Z",
  "claimed_files": [
    {
      "path": "src/pineapple/agents/builder.py",
      "exists": true,
      "size_bytes": 8432,
      "line_count": 247,
      "is_stub": false,
      "has_expected_symbols": ["BuilderAgent", "build", "write_files"]
    },
    {
      "path": "src/pineapple/agents/verifier.py",
      "exists": false,
      "claimed_but_missing": true
    }
  ],
  "git_status": {
    "modified": ["src/pineapple/agents/builder.py"],
    "untracked": [],
    "claimed_but_not_in_git": ["src/pineapple/agents/verifier.py"]
  },
  "verdict": "PARTIAL -- 1/2 claimed files exist, 1 missing"
}
```

---

## Skill 3: /verify-state-flow

### Purpose

After implementing multi-stage pipelines or any system with state passed between stages, verify that every state field that is READ by a stage is WRITTEN by an upstream stage.

### When to Invoke

- After implementing a multi-stage pipeline (like Pineapple itself)
- Before running E2E tests on a stateful system
- When debugging "field X is None" errors at runtime

### Inputs

| Input | Type | Required | Description |
|-------|------|----------|-------------|
| `state_file` | path | Yes | Path to the state schema (e.g., `state.py`) |
| `stage_files` | list[path] | Yes | Paths to all stage/agent implementation files |
| `state_class` | string | No | Name of the state class (defaults to searching for TypedDict/BaseModel) |

### Process

```
1. PARSE STATE SCHEMA
   - Read the state file
   - Extract all field names and types from the state class
   - Build a field inventory: {field_name: type}

2. ANALYZE EACH STAGE
   For each stage file:
   - Find all state field READS: state["field"], state.field, state.get("field")
   - Find all state field WRITES: state["field"] = ..., state.update({"field": ...})
   - Record: {stage: {reads: [fields], writes: [fields]}}

3. BUILD FLOW MATRIX
   Create a matrix showing which stage writes each field and which stages read it:

   | Field | Written by | Read by | Status |
   |-------|-----------|---------|--------|
   | strategic_brief | Stage 1 | Stage 2, 3 | OK |
   | branch | (nobody) | Stage 8 | ORPHAN READ |
   | temp_data | Stage 3 | (nobody) | ORPHAN WRITE |

4. VERIFY CONSISTENCY
   - Every READ must have a WRITE from an earlier stage (or initial state)
   - Field names must match exactly (no typos: "branch" vs "branch_name")
   - Types must be compatible
   - Flag: orphan reads (read but never written), orphan writes (written but never read)

5. RECORD
   - Write evidence file with full flow matrix
   - BLOCK if any orphan reads found (will cause runtime NoneType errors)
```

### Outputs

- Evidence file at `.pineapple/evidence/state-flow-<system>.json`
- Flow matrix (human-readable table)
- List of orphan reads, orphan writes, and type mismatches
- BLOCK signal if orphan reads exist

### What It Prevents

| Audit Issue | How |
|-------------|-----|
| H-4: Ship reads `branch` but nobody sets it | Detects orphan read |
| H-5: Verifier checks wrong directory | Detects field name mismatch |
| H-6: Wrong field name between stages | Detects typos in field references |
| L-7: `tools_available` written but never used | Detects orphan write |

### Evidence Format

```json
{
  "skill": "/verify-state-flow",
  "state_class": "PipelineState",
  "state_file": "src/pineapple/state.py",
  "field_count": 14,
  "flow_matrix": [
    {
      "field": "strategic_brief",
      "written_by": ["strategic_review.py"],
      "read_by": ["architecture.py", "planner.py"],
      "status": "OK"
    },
    {
      "field": "branch",
      "written_by": [],
      "read_by": ["shipper.py"],
      "status": "ORPHAN_READ"
    }
  ],
  "orphan_reads": ["branch"],
  "orphan_writes": ["tools_available"],
  "type_mismatches": [],
  "verdict": "FAIL -- 1 orphan read (branch), will crash at runtime"
}
```

---

## Skill 4: /verify-tests

### Purpose

Before claiming "X tests pass" or "test coverage is good," honestly report what the tests actually test.

### When to Invoke

- Before claiming test results in any status report
- During Stage 6 (Verify) of the pipeline
- When migrating from v1 to v2 of any system (to catch v1-testing-v2-claims)

### Inputs

| Input | Type | Required | Description |
|-------|------|----------|-------------|
| `test_dir` | path | Yes | Directory containing test files |
| `source_dir` | path | Yes | Directory containing source code being tested |
| `v2_modules` | list[string] | No | Module paths that are "v2" (current), everything else is legacy |

### Process

```
1. INVENTORY TEST FILES
   - Find all test files (test_*.py, *_test.py)
   - For each: extract imports, count test functions, identify what module they test

2. CLASSIFY IMPORTS
   - For each test file, check what it imports
   - Classify as v2 (current code) or v1 (legacy/dead code)
   - Flag tests that import from modules that no longer exist
   - Flag tests that import from v1 modules when v2 modules exist

3. MAP COVERAGE
   - For each v2 source module: which test files import and test it?
   - For each v2 function/class: is there at least one test that calls it?
   - Produce: {module: {functions_tested: N, functions_total: M, coverage_pct: N/M}}

4. RUN TESTS (both sets)
   - Run v2 tests: `pytest <v2-test-files> -v`
   - Run v1 tests separately: `pytest <v1-test-files> -v`
   - Report both results clearly separated

5. HONEST REPORT
   - "X v2 tests exist, Y pass, Z fail"
   - "A v1 tests exist (testing dead code), B pass"
   - "These v2 functions have ZERO test coverage: [list]"
   - BLOCK if claimed test count does not match actual v2 test count
```

### Outputs

- Evidence file at `.pineapple/evidence/tests-<project>.json`
- Honest test report with v1/v2 separation
- Coverage gaps list
- BLOCK if test claims are inflated (e.g., claiming 288 tests when only 53 test v2)

### What It Prevents

| Audit Issue | How |
|-------------|-----|
| C-3: 288 tests test v1 dead code, claimed as v2 coverage | Separates v1/v2 test counts |
| M-6/M-7/M-8: Zero coverage on agent functions | Lists functions with 0 tests |
| L-3: v1 stage names in tests | Flags imports from legacy modules |

### Evidence Format

```json
{
  "skill": "/verify-tests",
  "test_dir": "tests/",
  "source_dir": "src/pineapple/",
  "summary": {
    "v2_test_files": 12,
    "v2_test_functions": 53,
    "v2_tests_passing": 48,
    "v2_tests_failing": 5,
    "v1_test_files": 45,
    "v1_test_functions": 288,
    "v1_tests_passing": 285,
    "v1_tests_note": "These test production-pipeline/ (v1 dead code), NOT src/pineapple/ (v2)"
  },
  "v2_coverage": [
    {
      "module": "src/pineapple/agents/builder.py",
      "functions_total": 8,
      "functions_tested": 3,
      "untested": ["write_files", "validate_output", "rollback", "cleanup", "report_progress"]
    }
  ],
  "zero_coverage_modules": [
    "src/pineapple/agents/evolver.py",
    "src/pineapple/middleware/observability.py"
  ],
  "verdict": "53 v2 tests (48 pass, 5 fail), 288 v1 tests (irrelevant). 4 modules with zero v2 coverage."
}
```

---

## Skill 5: /verify-cost

### Purpose

After wiring any cost tracking, billing, or observability system, verify it actually records real costs.

### When to Invoke

- After integrating LangFuse, LangSmith, or any cost tracking
- After claiming "cost tracking works"
- Before marking cost-related success criteria as complete

### Inputs

| Input | Type | Required | Description |
|-------|------|----------|-------------|
| `provider` | string | Yes | Which LLM provider to test (e.g., "gemini", "claude", "openai") |
| `tracking_system` | string | Yes | Which tracking system (e.g., "langfuse", "langsmith", "custom") |
| `test_prompt` | string | No | Prompt to send (defaults to "Say hello in exactly 3 words") |

### Process

```
1. MAKE A REAL API CALL
   - Send a minimal prompt to the specified provider
   - Use the production code path (not a test harness)
   - Record: response received, latency, token counts

2. CHECK COST RECORDING
   - Query the tracking system for the trace
   - Verify: trace exists, cost field is populated, cost > $0 for paid providers
   - For Gemini specifically: verify cost is NOT $0.00 (common bug: Gemini reports
     usage differently than Claude, wrappers often miss it)

3. CHECK FLUSH
   - Verify flush_traces() or equivalent is called
   - Check that the trace actually appears in the dashboard/DB (not just queued)
   - If LangFuse: query the API for the trace ID

4. CHECK CALLBACK CHAIN
   - Trace the code path from LLM call to cost recording
   - Verify every intermediate step passes cost data through
   - Flag any step that swallows or zeroes the cost

5. RECORD
   - Write evidence with actual cost recorded, trace ID, dashboard link
   - BLOCK if cost is $0.00 for a paid provider
```

### Outputs

- Evidence file at `.pineapple/evidence/cost-<provider>-<tracking>.json`
- Trace ID and dashboard link
- BLOCK if cost tracking returns $0.00 for paid API calls

### What It Prevents

| Audit Issue | How |
|-------------|-----|
| H-1: Fake cost tracking ($0 for all calls) | Makes real call, checks cost > $0 |
| H-2: Missing flush_traces() | Verifies trace appears in dashboard |
| L-9: Gemini = $0 (usage format mismatch) | Specifically tests Gemini cost path |

### Evidence Format

```json
{
  "skill": "/verify-cost",
  "provider": "gemini",
  "tracking_system": "langfuse",
  "test_call": {
    "prompt": "Say hello in exactly 3 words",
    "response": "Hello there, friend!",
    "input_tokens": 12,
    "output_tokens": 5,
    "latency_ms": 340
  },
  "cost_recorded": {
    "trace_id": "abc-123-def",
    "cost_usd": 0.00012,
    "dashboard_url": "https://langfuse.example.com/trace/abc-123-def",
    "appears_in_dashboard": true
  },
  "flush_verified": true,
  "verdict": "WORKING -- Gemini cost tracked at $0.00012 per call, trace visible in LangFuse"
}
```

---

## Skill 6: /honest-status

### Purpose

Before reporting progress to the user (status updates, scorecards, "X/10 criteria met"), force honest reporting backed by evidence.

### When to Invoke

- Before any progress report to the user
- Before writing session handoffs
- Before updating project bibles
- Before claiming "done" on any milestone

### Inputs

| Input | Type | Required | Description |
|-------|------|----------|-------------|
| `features` | list[string] | Yes | List of features/criteria to report on |
| `evidence_dir` | path | No | Path to evidence files (defaults to `.pineapple/evidence/`) |

### Process

```
1. COLLECT EVIDENCE
   For each feature in the list:
   - Look for evidence file at .pineapple/evidence/<feature-slug>.json
   - If no evidence file exists: status = "UNVERIFIED" (not WORKING, not STUBBED -- unknown)

2. COMPILE REPORT
   For each feature:
   - If evidence exists: use the status from the evidence file (WORKING/WIRED/STUBBED/FAKE)
   - If no evidence: mark UNVERIFIED
   - CANNOT claim WORKING without a /verify-done evidence file
   - CANNOT use the word "MET"

3. GENERATE REPORT CARD
   Format:

   ## Status Report: <project> (<date>)

   | # | Feature | Status | Evidence | Last Verified |
   |---|---------|--------|----------|---------------|
   | 1 | Builder writes files | WORKING | evidence/builder.json | 2026-03-23 |
   | 2 | Verifier runs tests | STUBBED | evidence/verifier.json | 2026-03-23 |
   | 3 | Cost tracking | UNVERIFIED | (none) | never |

   Summary: 4 WORKING, 2 WIRED, 3 STUBBED, 1 UNVERIFIED

   NOT READY TO SHIP. 6/10 features are not working.

4. BLOCK FALSE CONFIDENCE
   - Cannot say "X/10 met" or "all criteria met"
   - Must say "X WORKING, Y WIRED, Z STUBBED, W UNVERIFIED"
   - If any feature is not WORKING, the summary must say "NOT READY"
   - If the user asks "is it done?" the answer is the report card, not "yes"
```

### Outputs

- Report card (markdown, printed to console)
- Evidence summary file at `.pineapple/evidence/status-report-<date>.json`
- BLOCK any "all done" claim if any feature is not WORKING

### What It Prevents

| Audit Issue | How |
|-------------|-----|
| The entire "10/10 MET" false confidence pattern | Cannot say MET, must show evidence |
| Session handoffs that overstate progress | Requires evidence files for every claim |
| Bible updates that close gaps without verification | UNVERIFIED status for gaps without evidence |

---

## Hookify Integration (Thin Safety Net)

Three STOP rules. These are the last line of defense -- they physically block actions when skills were skipped.

### Rule 1: No Commit Without Evidence

```
trigger: pre-commit hook (or hookify pretooluse on git commit)
condition: No file in .pineapple/evidence/ modified in the last 10 minutes
action: STOP with message "Run /verify-done or /verify-outputs before committing.
         No evidence file found modified in the last 10 minutes."
exception: Commits to docs/ or .md files only (documentation commits are exempt)
```

### Rule 2: No Task Completion Without Evidence

```
trigger: hookify pretooluse on TodoWrite with status:completed
condition: No evidence file exists matching the task name
action: STOP with message "Run /verify-done for this task before marking it complete.
         No evidence file found for: <task-name>"
exception: Tasks tagged as "meta" or "planning" (non-code tasks)
```

### Rule 3: No Push/PR Without Verification

```
trigger: hookify pretooluse on git push or gh pr create
condition: /verify-done has not been run in this session (no evidence files with
           today's date exist)
action: STOP with message "Run /verify-done before pushing or creating a PR.
         No verification evidence found for today's session."
exception: None. Every push must have evidence.
```

### Why Only 3 Rules

Hookify has been brittle (stale cache, wrong field names, Windows path issues). More rules = more breakage surface. These 3 rules cover the highest-impact failure modes:

1. **Commit without evidence** catches building without verifying
2. **Task completion without evidence** catches false progress claims
3. **Push/PR without verification** catches shipping unverified code

Everything else is handled by skills in context. Skills are more reliable than hookify because they run as part of the conversation, not as an external hook system.

---

## Memory Migration Plan

These 7 feedback files should be ABSORBED into skills. The feedback files are not deleted -- they are annotated with which skill now enforces them.

| Feedback File | Absorbed Into | Annotation to Add |
|---------------|--------------|-------------------|
| `feedback_no_premature_execution.md` | Pipeline Stage 0 intake + Stage 1 strategic review | "Enforced by Pineapple SKILL.md stages 0-1: context loaded before any work begins" |
| `feedback_orchestrator_rule.md` | **Stays as-is** (meta-process, not feature-specific) | No change |
| `feedback_visual_verification.md` | `/verify-outputs` (file existence) + `/verify-done` (behavior) | "Enforced by /verify-outputs and /verify-done skills" |
| `feedback_pineapple_dogfood_lessons.md` | Distributed across all 6 skills (see mapping below) | "Enforced by enforcement skills system: ENFORCEMENT_SKILLS_SPEC.md" |
| `feedback_e2e_verification_against_docs.md` | `/verify-done` (document cross-reference step) | "Enforced by /verify-done step 5: cross-reference documents" |
| `feedback_verification_means_running.md` | `/verify-done` + `/verify-outputs` | "Enforced by /verify-done (run with real inputs) and /verify-outputs (check real files)" |
| `feedback_no_media_upload.md` | **Stays as-is** (not feature-specific) | No change |

### Dogfood Lesson Distribution

| Lesson | Skill |
|--------|-------|
| 1. Read user's world before designing | Pipeline Stage 0 (already in SKILL.md) |
| 2. Verify at every level | `/verify-done` (three-level verification) |
| 3. Running code > reading code | `/verify-done` + `/verify-outputs` (both run, not grep) |
| 4. User pushback is always signal | Not enforceable by skill (human judgment) |
| 5. False confidence is worse than none | `/honest-status` (evidence-backed reporting) |
| 6. Approval does not equal correctness | `/verify-done` (cross-reference docs post-approval) |
| 7. Spec is the most dangerous artifact | `/verify-state-flow` (validate spec against implementation) |
| 8. Don't reinvent chosen libraries | Pipeline Stage 2 (already in SKILL.md) |
| 9. Executor is never the verifier | Pipeline Stage 5/6/7 separation (already in SKILL.md) |
| 10. Cross-session context is everything | Pipeline Stage 0 (already in SKILL.md) |

---

## Implementation Order

Build the skills in this order. Each skill is independently useful and builds on the previous.

### Phase 1: Foundation (build first)

**1. /verify-outputs** (simplest, highest immediate value)
- Pure file system checks -- no API calls, no complex logic
- Immediately catches "builder didn't build" (the most common failure)
- Establishes the `.pineapple/evidence/` directory pattern
- Estimated effort: 2-3 hours

**2. /verify-done** (core skill, everything depends on it)
- Builds on /verify-outputs (includes file checks + behavior checks)
- Establishes the evidence file format all other skills use
- Establishes the status vocabulary (WORKING/WIRED/STUBBED/FAKE)
- Estimated effort: 4-6 hours

### Phase 2: Honesty Layer

**3. /honest-status** (prevents false progress reports)
- Reads evidence files produced by /verify-done
- Pure reporting -- no new verification logic
- Immediately changes how we report progress
- Estimated effort: 2-3 hours

**4. /verify-tests** (prevents inflated test claims)
- Static analysis of test files -- no runtime needed
- Catches the 288-tests-test-v1 problem immediately
- Estimated effort: 3-4 hours

### Phase 3: Deep Verification

**5. /verify-state-flow** (prevents runtime crashes from missing fields)
- Static analysis of state schema and stage files
- More complex -- needs AST parsing or regex for field access patterns
- Estimated effort: 4-6 hours

**6. /verify-cost** (prevents fake cost tracking)
- Requires real API calls and tracking system access
- Most infrastructure-dependent -- build last
- Estimated effort: 3-4 hours

### Phase 4: Hookify Rules (after all skills work)

**7. Wire 3 hookify STOP rules**
- Only after skills produce evidence files reliably
- Test each rule manually before enabling
- Estimated effort: 2-3 hours

### Total Estimated Effort: 20-29 hours across 4 phases

---

## Skill File Format

Each skill is a markdown file at `docs/superpowers/skills/pineapple/<skill-name>.md` following the existing Pineapple skill format:

```markdown
---
name: verify-done
description: "Verify a feature works by running it with real inputs and checking real
  outputs. Produces evidence file. BLOCKS if feature is STUBBED or FAKE."
---

# /verify-done

[Process steps, inputs, outputs, evidence format as defined in this spec]
```

Skills are loaded into Claude's context when the user invokes them (e.g., `/verify-done builder-writes-to-disk`). They define a strict process that Claude follows, producing evidence files that hookify can check for.

---

## Success Criteria for the Skill System Itself

How do we know the enforcement skill system is working?

### Quantitative

1. **Zero false "WORKING" claims.** If /verify-done says WORKING, the feature actually works when a human runs it manually. Measured by: user spot-checks 3 random WORKING claims per session.

2. **Evidence file coverage > 80%.** For any session that claims N features done, at least 0.8*N evidence files exist in `.pineapple/evidence/`. Measured by: count evidence files vs claims in session handoff.

3. **Zero "MET" vocabulary in any output.** Grep all session handoffs, project bibles, and status reports for the word "MET" (case-insensitive, excluding "meeting" and "method"). Count should be 0.

4. **Hookify blocks > 0 per session.** If hookify never fires, either the rules are broken or Claude is perfectly compliant (unlikely in early sessions). At least 1 block per session in the first 5 sessions indicates the safety net is working.

### Qualitative

5. **User trust increases.** The user stops needing to say "ARE YOU SURE?" and "did you ACTUALLY run it?" because evidence files answer those questions.

6. **Audit findings decrease.** A repeat of the 2026-03-23 audit methodology on a session that used enforcement skills should find < 5 issues (down from 33).

7. **Session handoffs are honest.** Session handoffs list actual statuses (3 WORKING, 2 WIRED, 1 STUBBED) instead of "8/10 criteria met."

### Meta-Verification

8. **The skill system verifies itself.** Run `/verify-done "enforcement skills system"` with expected behavior: "6 skills exist as .md files, hookify rules are registered, evidence directory is created." If this fails, the system is not ready.

---

## Appendix A: Full Audit Issue Mapping

Every issue from the 2026-03-23 audit mapped to which skill prevents it.

| ID | Issue | Skill | How |
|----|-------|-------|-----|
| C-1 | Fake circuit breaker | /verify-done | Run PyBreaker with failures, check circuit opens |
| C-2 | Phantom workspace | /verify-outputs | Check workspace path exists on filesystem |
| C-3 | 288 tests test v1 | /verify-tests | Separate v1/v2 test counts |
| H-1 | Fake cost tracking | /verify-cost | Make real call, check cost > $0 |
| H-2 | Missing flush_traces | /verify-cost | Check trace appears in dashboard |
| H-3 | Builder doesn't build | /verify-outputs | Check files exist after builder runs |
| H-4 | Ship reads unset field | /verify-state-flow | Detect orphan read |
| H-5 | Verifier checks wrong dir | /verify-state-flow | Detect field name mismatch |
| H-6 | Scaffold creates nothing | /verify-outputs | Check files exist after scaffold |
| H-6 | Wrong field name | /verify-state-flow | Detect field name typos |
| L-3 | v1 stage names in tests | /verify-tests | Flag imports from legacy modules |
| L-7 | tools_available never used | /verify-state-flow | Detect orphan write |
| L-9 | Gemini = $0 | /verify-cost | Test Gemini cost path specifically |
| M-6 | Zero coverage on agents | /verify-tests | List functions with 0 tests |
| M-7 | Zero coverage on agents | /verify-tests | List functions with 0 tests |
| M-8 | Zero coverage on agents | /verify-tests | List functions with 0 tests |
| ALL | "10/10 MET" false confidence | /honest-status | Evidence-backed reporting, MET banned |

---

## Appendix B: Interaction with Pineapple Pipeline Stages

Where enforcement skills integrate with the existing 10-stage pipeline:

```
Stage 0 (Intake)     -- no enforcement skill needed (context loading only)
Stage 1 (Strategic)  -- no enforcement skill needed (human-in-the-loop)
Stage 2 (Architecture) -- no enforcement skill needed (human-in-the-loop)
Stage 3 (Plan)       -- no enforcement skill needed (human-in-the-loop)
Stage 4 (Setup)      -- /verify-outputs (did scaffold create files?)
Stage 5 (Build)      -- /verify-outputs (after each coder agent)
                     -- /verify-state-flow (if building stateful system)
Stage 5->6 gate      -- /verify-outputs MUST pass before advancing
Stage 6 (Verify)     -- /verify-done (for each feature)
                     -- /verify-tests (honest test reporting)
                     -- /verify-cost (if cost tracking was implemented)
Stage 6->7 gate      -- /verify-done evidence files MUST exist
Stage 7 (Review)     -- /honest-status (reviewer sees real statuses)
Stage 8 (Ship)       -- Hookify Rule 3 blocks push without evidence
Stage 9 (Evolve)     -- /honest-status (session handoff uses real statuses)
```

---

## Appendix C: What This Spec Does NOT Cover

1. **How to build the skills as executable code.** This spec defines what each skill does. Implementation could be: (a) markdown files loaded as context (like current SKILL.md), (b) Python scripts that produce evidence files, or (c) MCP tools. The implementation approach is a separate decision.

2. **Integration with LangGraph v2 graph.** The v2 spec defines the graph. This spec defines enforcement that runs alongside the graph. How they connect (gate functions call skills? skills are graph nodes?) is an implementation decision.

3. **Hookify implementation details.** The 3 STOP rules are defined conceptually. The actual `.claude/hookify.*.local.md` syntax, field names, and Windows path handling are implementation details covered by the Hookify Rule Authoring section in MEMORY.md.

4. **Existing hookify rule migration.** The current 16 hookify rules should be audited. Most should be removed (absorbed into skills). The 3 STOP rules defined here replace whatever subset of the 16 is still relevant. This audit is a separate task.
