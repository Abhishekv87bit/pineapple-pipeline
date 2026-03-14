# Pineapple Pipeline -- Operational Runbook

> **Version:** 1.0.0
> **Last updated:** 2026-03-15
> **Scope:** Single-developer, single-machine environment running 1-3 concurrent features.

This runbook covers diagnosis and recovery for every component of the Pineapple Pipeline. Each section follows Symptom -> Diagnostic -> Resolution -> Verify.

---

## 1. Service Health Checks

Run `py -3.12 production-pipeline/tools/pineapple_doctor.py` for a full automated sweep. The sections below cover manual diagnosis when doctor output is insufficient.

### 1.1 LangFuse (Observability)

**Symptom:** `observe_llm_call()` raises `ConnectionRefusedError`, or traces are missing from `http://localhost:3000`.

**Diagnostic:**
```bash
curl http://localhost:3000/api/public/health
docker ps | grep langfuse
docker logs langfuse --tail 50
```

**Resolution:**
1. If container is not running: `docker start langfuse`
2. If container does not exist: `docker run -d --name langfuse -p 3000:3000 langfuse/langfuse:2.95.1`
3. If container is running but health check fails: `docker restart langfuse` and wait 15 seconds
4. If port conflict: `netstat -ano | findstr :3000` to identify the conflicting process

**Verify:** `curl http://localhost:3000/api/public/health` returns HTTP 200.

### 1.2 Mem0 (Memory Service)

**Symptom:** Memory extraction in Stage 8 (Evolve) fails. `pineapple_evolve.py` reports `mem0_extraction: FAIL`.

**Diagnostic:**
```bash
curl http://localhost:8080/health
docker ps | grep mem0
docker logs mem0 --tail 50
```

**Resolution:**
1. If container is not running: `docker start mem0`
2. If container does not exist: `docker run -d --name mem0 -p 8080:8080 mem0/mem0:latest`
3. If Mem0 API responds but extraction fails: check `MEM0_URL` env var matches actual port
4. Mem0 is currently `[PLANNED]` (Phase 5). If not yet deployed, this check returns SKIP -- not a failure.

**Verify:** `curl http://localhost:8080/health` returns HTTP 200, or SKIP is acceptable if Phase 5 is not yet active.

### 1.3 Neo4j (Graph Memory)

**Symptom:** Graph updates in Stage 8 fail. Component relationship queries return empty results.

**Diagnostic:**
```bash
curl http://localhost:7474
docker ps | grep neo4j
docker logs neo4j --tail 50
```

**Resolution:**
1. If container is not running: `docker start neo4j`
2. If container does not exist: `docker run -d --name neo4j -p 7474:7474 -p 7687:7687 neo4j:community-5.26.0`
3. If Bolt connection fails on port 7687 but HTTP works on 7474: check firewall rules
4. Neo4j is currently `[PLANNED]` (Phase 6). SKIP is acceptable if Phase 6 is not yet active.

**Verify:** `curl http://localhost:7474` returns the Neo4j browser page (HTTP 200).

### 1.4 DeepEval (LLM Evaluation)

**Symptom:** Stage 5 Layer 4 (LLM evals) reports `ImportError: No module named 'deepeval'`.

**Diagnostic:**
```bash
py -3.12 -c "import deepeval; print(deepeval.__version__)"
```

**Resolution:**
1. If not installed: `py -3.12 -m pip install deepeval==1.5.5`
2. If wrong version: `py -3.12 -m pip install deepeval==1.5.5 --force-reinstall`
3. If import succeeds but evals fail: check `DEEPEVAL_TELEMETRY=NO` is set in environment
4. If eval benchmarks timeout: increase timeout in `pineapple_verify.py` (default 300s)

**Verify:** `py -3.12 -c "import deepeval; print(deepeval.__version__)"` prints `1.5.5`.

### 1.5 DSPy (Prompt Optimization)

**Symptom:** `pineapple_evolve.py` reports `dspy_optimization: NOT_IMPLEMENTED` or `ImportError`.

**Diagnostic:**
```bash
py -3.12 -c "import dspy; print(dspy.__version__)"
```

**Resolution:**
1. If not installed: `py -3.12 -m pip install dspy-ai==2.6.1`
2. DSPy optimization is currently `[PLANNED]` (Phase 7). `NOT_IMPLEMENTED` is expected, not a failure.

**Verify:** `py -3.12 -c "import dspy; print(dspy.__version__)"` prints `2.6.1`.

### 1.6 Hookify (Enforcement)

**Symptom:** Pipeline gate rules are not blocking stage-skipping. Code is written without a spec file.

**Diagnostic:**
```bash
ls ~/.claude/hookify.*.local.md | wc -l
# Expect 16 (11 KFS rules + 5 pipeline gates)
py -3.12 tools/validate_hookify.py
```

**Resolution:**
1. If rule count is 0: rules were wiped by a plugin update. Restore from git: `git checkout -- ~/.claude/hookify.*.local.md`
2. If rule count is 11 (KFS only, no pipeline gates): pipeline gate rules have not been created yet (`[PLANNED]`)
3. If `validate_hookify.py` reports errors: fix the syntax in the failing rule file. Common issues:
   - Non-ASCII characters (use ASCII only)
   - `[/\\]` in regex (use `.` instead)
   - `$` anchors (hookify uses `re.search()` without `re.MULTILINE`)
4. If hooks are not executing: check `~/.claude/settings.json` has the hookify wrapper scripts registered (not plugin hooks.json -- plugin hooks do not execute on Windows)

**Verify:** `py -3.12 tools/validate_hookify.py` exits 0 with no errors.

### 1.7 Docker (Container Runtime)

**Symptom:** `docker-compose up` fails. Container builds fail. Services unreachable.

**Diagnostic:**
```bash
docker info
docker ps
docker-compose version
```

**Resolution:**
1. If `docker info` fails: Docker Desktop is not running. Start it from the system tray or Start menu.
2. If `docker ps` shows exited containers: `docker start <name>` or `docker-compose up -d`
3. If build fails with disk space error: `docker system prune -a` (removes all unused images, WARNING: destructive)
4. If port conflicts: `docker ps --format "{{.Names}}: {{.Ports}}"` to see all port bindings

**Verify:** `docker info` succeeds and `docker ps` shows expected containers.

---

## 2. Pipeline Stage Failures

### Stage 0: INTAKE

**Common failures:**
- Context files missing (CLAUDE.md, MEMORY.md, bible YAML)
- Request classification ambiguous

**Diagnosis:** Check for required files:
```bash
ls CLAUDE.md memory/MEMORY.md projects/*-bible.yaml
```

**Recovery:**
- If CLAUDE.md missing: `py -3.12 production-pipeline/tools/apply_pipeline.py . --stack fastapi-vite` regenerates it
- If bible YAML missing: same scaffolding command creates it
- If classification unclear: explicitly tell the agent the request type (new feature, bug fix, improvement)

### Stage 1: BRAINSTORM

**Common failures:**
- User declines all proposed approaches
- Spec file not written after approval

**Diagnosis:** Check for spec file:
```bash
ls docs/superpowers/specs/
```

**Recovery:**
- If no spec exists after brainstorming: write one manually or re-run brainstorming
- If user declines all approaches: narrow scope or split into smaller features
- No retry limit -- this stage is user-driven

### Stage 2: PLAN

**Common failures:**
- Plan too vague (no file map, no verification commands)
- Plan reviewer rejects 3 times

**Diagnosis:** Read the plan file:
```bash
cat docs/superpowers/plans/*.md
```

**Recovery:**
- If reviewer rejects 3 times: surface to user for guidance on scope reduction
- If plan has no verification commands: add `pytest -v` and any domain-specific validators
- Rewrite plan from scratch if fundamentally misaligned with spec

### Stage 3: SETUP

**Common failures:**
- Worktree creation fails ("branch already exists", "path already registered")
- Template stamping fails (permission error, disk full)
- Dependency installation fails

**Diagnosis:**
```bash
git worktree list
git branch -a | grep <feature-name>
ls .pineapple/
```

**Recovery:**
- "Branch already exists": `git worktree list` to find stale worktrees, then `git worktree remove <path>`
- "Path already registered as worktree": the directory exists but is stale. `git worktree remove <path>` then retry.
- Template stamping fails: check disk space (`df -h` or `wmic logicaldisk get size,freespace`), fix permissions
- Dependency fails: check Python version (`py -3.12 --version`), clear pip cache (`py -3.12 -m pip cache purge`)
- Max 2 retries. After 2 failures, ask user to check environment.

### Stage 4: BUILD

**Common failures:**
- Coder agent produces code that fails tests
- Merge conflicts between parallel tasks
- Agent exceeds cost ceiling ($200)

**Diagnosis:**
```bash
cat .pineapple/runs/<uuid>/state.json
git log --oneline -10
py -3.12 -m pytest -v --tb=short
```

**Recovery:**
- Failed tests: resume from the failed task (do not restart from task 1). Max 3 retries per task.
- If task fails 3 times: skip it, note in session handoff, proceed to next task
- Merge conflicts: `git revert` the conflicting task's commits (never `git reset --hard`), then re-implement
- Cost ceiling: pause and present options to user (continue, simplify, abandon)
- Circuit breaker: max 3 full Build-Verify-Review cycles. After 3, stop and present to user.

### Stage 5: VERIFY

**Common failures:**
- Layer 1 (unit tests) fails
- Layer 3 (security tests) fails on new attack patterns
- Layer 4 (LLM evals) times out
- Layer 5 (domain validation / VLAD) reports structural issues

**Diagnosis:**
```bash
py -3.12 production-pipeline/tools/pineapple_verify.py . --layers 1,2,3,4,5
cat .pineapple/last_verify.json
```

**Recovery:**
- Fix the failing layer, then re-run ONLY that layer: `--layers <N>`
- If Layer 4 times out: increase timeout or reduce test count. Check DeepEval is installed.
- If VLAD fails: run `py -3.12 tools/vlad.py <module>` directly for detailed output
- Max 3 retries per layer. After 3 failures, surface to user with evidence.
- On any failure, `last_verify.json` is deleted -- the hookify gate blocks merge until all layers pass.

### Stage 6: REVIEW

**Common failures:**
- Reviewer finds Critical issues
- Review cycles exceed limit (3)

**Diagnosis:** Read the review output in the conversation or session log.

**Recovery:**
- Critical issues: fix immediately, create fixup commit, return to Stage 5 to verify the fix
- Important issues: fix before proceeding to Stage 7
- Minor issues: note for later, do not block
- After 3 review cycles: stop and present to user with options (merge with known issues, redesign, abandon)

### Stage 7: SHIP

**Common failures:**
- Merge conflicts when integrating feature branch
- Docker build fails in CI
- Worktree cleanup fails

**Diagnosis:**
```bash
git diff main...HEAD --stat
docker-compose build
git worktree list
```

**Recovery:**
- Merge conflicts: attempt auto-resolve. If >3 conflicting files, escalate to user. Never force-push.
- Docker build failure: check Dockerfile syntax, base image availability, dependency versions
- Worktree cleanup: `git worktree remove <path>` (use `--force` if directory is gone)
- Max 2 retries. After 2, ask user for manual merge.

### Stage 8: EVOLVE

**Common failures:**
- Session handoff not written
- Bible update forgotten
- Mem0/Neo4j/DSPy services unavailable

**Diagnosis:**
```bash
ls sessions/
cat projects/*-bible.yaml | head -20
py -3.12 production-pipeline/tools/pineapple_evolve.py
```

**Recovery:**
- Handoff not written: write it now. Check git log for what was done this session.
- Bible not updated: update now. Cross-reference with session handoff.
- Services unavailable: these are `[PLANNED]` services. `NOT_IMPLEMENTED` and `SKIP` are expected. No action needed until Phase 5-7.
- Max 1 retry. If handoff fails again, skip and note in next session.

---

## 3. Pipeline State Recovery

### Reading Pipeline State

Each pipeline run creates a state file at `.pineapple/runs/<uuid>/state.json`:

```bash
# List all runs
ls .pineapple/runs/

# Read a specific run
cat .pineapple/runs/<uuid>/state.json
```

The state file contains:
- `run_id`: UUID for this run
- `branch`: feature branch name
- `current_stage`: integer 0-8
- `stage_history`: array of stage transitions with timestamps
- `started_at`: ISO timestamp
- `last_updated`: ISO timestamp

### Identifying Stuck Runs

A run is considered stuck if `last_updated` is older than the wall-clock timeout (4 hours):

```bash
# On Windows (PowerShell):
Get-ChildItem .pineapple/runs/*/state.json | ForEach-Object {
    $json = Get-Content $_ | ConvertFrom-Json
    Write-Output "$($json.run_id) | Stage $($json.current_stage) | $($json.last_updated)"
}

# On bash:
for f in .pineapple/runs/*/state.json; do
    echo "$(python -c "import json; d=json.load(open('$f')); print(d.get('run_id','?'), '|', 'Stage', d.get('current_stage','?'), '|', d.get('last_updated','?'))")"
done
```

Any run with `last_updated` more than 4 hours ago is likely stuck.

### Manually Advancing a Stuck Run

Edit the state file directly (last resort):

```bash
py -3.12 -c "
import json
from pathlib import Path
from datetime import datetime, timezone

state_file = Path('.pineapple/runs/<uuid>/state.json')
state = json.loads(state_file.read_text())
state['current_stage'] = <target_stage>
state['last_updated'] = datetime.now(timezone.utc).isoformat()
state['stage_history'].append({
    'stage': <target_stage>,
    'action': 'manual_advance',
    'timestamp': state['last_updated'],
    'reason': 'stuck run recovery'
})
state_file.write_text(json.dumps(state, indent=2))
print('Advanced to stage', <target_stage>)
"
```

### Manually Failing a Stuck Run

```bash
py -3.12 -c "
import json
from pathlib import Path
from datetime import datetime, timezone

state_file = Path('.pineapple/runs/<uuid>/state.json')
state = json.loads(state_file.read_text())
state['status'] = 'FAILED'
state['last_updated'] = datetime.now(timezone.utc).isoformat()
state['stage_history'].append({
    'stage': state['current_stage'],
    'action': 'manual_fail',
    'timestamp': state['last_updated'],
    'reason': 'stuck run recovery'
})
state_file.write_text(json.dumps(state, indent=2))
print('Marked run as FAILED')
"
```

### Recovering From Corrupted State

If `state.json` is corrupted (invalid JSON, missing fields):

1. Check git for the last good version: `git log --oneline -- .pineapple/runs/<uuid>/state.json`
2. If in git: `git show <commit>:.pineapple/runs/<uuid>/state.json > .pineapple/runs/<uuid>/state.json`
3. If not in git: delete the state file and start a fresh pipeline run. Partial progress is preserved in git commits.
4. The plan file (`docs/superpowers/plans/*.md`) with checkboxes provides a secondary record of progress, but the state machine is authoritative.

---

## 4. Verification Record Troubleshooting

### Checking a Verification Record

```bash
cat .pineapple/verify/<branch-name>.json
```

Expected fields:
- `version`: schema version (currently `1.0.0`)
- `run_id`: UUID of the verify run
- `branch`: branch name (must match the file name)
- `timestamp`: ISO timestamp of when verification completed
- `layers_passed`: array of layer numbers that passed
- `layers_failed`: array of layer numbers that failed
- `test_count`: total number of tests run across all layers
- `all_green`: boolean, true only if all layers passed
- `evidence_hash`: SHA256 of concatenated test output
- `integrity_hash`: SHA256 of `(evidence_hash + run_id + branch + timestamp)`

### Checking Freshness

The hookify gate rule rejects verification records older than 2 hours:

```bash
py -3.12 -c "
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

record = json.loads(Path('.pineapple/verify/<branch>.json').read_text())
ts = datetime.fromisoformat(record['timestamp'])
age = datetime.now(timezone.utc) - ts
stale = age > timedelta(hours=2)
print(f'Age: {age}')
print(f'Stale: {stale}')
if stale:
    print('ACTION: Re-run pineapple verify to refresh')
"
```

### Validating Integrity

Re-compute the integrity hash and compare:

```bash
py -3.12 -c "
import hashlib, json
from pathlib import Path

record = json.loads(Path('.pineapple/verify/<branch>.json').read_text())
payload = record['evidence_hash'] + record['run_id'] + record['branch'] + record['timestamp']
expected = hashlib.sha256(payload.encode()).hexdigest()
actual = record['integrity_hash']
match = expected == actual
print(f'Expected: {expected}')
print(f'Actual:   {actual}')
print(f'Valid:    {match}')
if not match:
    print('ACTION: Integrity check failed. Re-run pineapple verify.')
"
```

### Stale Record (>2 hours)

If the verification record is stale:
1. Re-run verification: `py -3.12 production-pipeline/tools/pineapple_verify.py .`
2. This generates a fresh record with a current timestamp
3. The hookify gate will accept the new record

### Integrity Check Failure

If the integrity hash does not match:
1. The record may have been tampered with or corrupted
2. Delete the record: `rm .pineapple/verify/<branch>.json`
3. Re-run verification: `py -3.12 production-pipeline/tools/pineapple_verify.py .`
4. If integrity checks keep failing, check for clock skew or encoding issues

---

## 5. Hookify Rule Troubleshooting

### Checking Rules Are Loaded

```bash
ls ~/.claude/hookify.*.local.md | wc -l
```

Expected counts:
- 11 = KFS rules only (pipeline gate rules not yet added)
- 16 = KFS rules (11) + pipeline gate rules (5)

### After Plugin Update

Hookify plugin updates can overwrite cached files. Symptoms:
- Rules stop firing
- `validate_hookify.py` reports missing files
- `config_loader.py` and `pretooluse.py` patches are reverted

Recovery:
1. Restore rule files from git: `git checkout -- ~/.claude/hookify.*.local.md`
2. Re-apply patches to the hookify plugin cache:
   - `config_loader.py`: patch to search BOTH CWD and `~/.claude/` dirs
   - `pretooluse.py`: patch to skip non-Bash/non-file tools
   - Cache location: `~/.claude/plugins/cache/claude-plugins-official/hookify/*/`
3. Validate: `py -3.12 tools/validate_hookify.py`

### Switching From BLOCK to WARN (Debugging)

When a BLOCK rule is preventing work during debugging:

1. Open the rule file: `~/.claude/hookify.<rule-name>.local.md`
2. Change `action: stop` to `action: warn`
3. Save and continue work
4. IMPORTANT: Change back to `action: stop` when debugging is complete

Alternatively, pass `--prototype` to `pineapple scaffold` to generate WARN-level rules for exploratory work.

### Validating Rules

```bash
py -3.12 tools/validate_hookify.py
```

Common validation failures:
- **Non-ASCII characters**: emojis, em dashes, curly quotes. Use ASCII only.
- **`[/\\]` in regex**: hookify does not handle character classes for path separators. Use `.` (dot) instead.
- **`$` anchors**: hookify uses `re.search()` without `re.MULTILINE`, so `$` does not match end-of-line as expected.
- **Wrong field name**: stop rules need `field: reason`, not `pattern:` (which auto-assigns `content`).
- **Missing `field: content`**: file event text matching requires explicit `field: content`.

End-to-end test:
```bash
cd %USERPROFILE% && py -3.12 "D:\Claude local\tools\test_hookify_e2e.py"
```

---

## 6. Template Issues

### Unreplaced Placeholders

**Symptom:** Stamped files contain literal `{{PLACEHOLDER}}` strings.

**Diagnosis:**
```bash
grep -r "{{" backend/app/middleware/ frontend/ docker-compose.yml .github/
```

**Resolution:**
1. Re-run scaffolding with `--force` to overwrite: `py -3.12 production-pipeline/tools/apply_pipeline.py . --stack fastapi-vite --force`
2. If specific placeholders are unknown to `detect_project()`, add them to the config dict in `apply_pipeline.py`
3. Known placeholders: `PROJECT_NAME`, `PYTHON_VERSION`, `NODE_VERSION`, `BACKEND_DIR`, `FRONTEND_DIR`, `BACKEND_PORT`, `FRONTEND_PORT`, `APP_MODULE`, `DB_PATH`, `ENV_FILE`, `TEST_COMMAND`, `API_URL`, `DEFAULT_LIMIT`, `CHAT_LIMIT`, `EXPORT_LIMIT`, `EXTRA_SYSTEM_DEPS`, `EXTRA_PIP_DEPS`

### Template Version Mismatch

**Symptom:** Template header says `v0.9.0` but current pipeline is `v1.0.0`.

**Diagnosis:** Check the header in any stamped template:
```bash
head -1 backend/app/middleware/input_guardrails.py
# Expected: # Generated by Pineapple Pipeline v1.0.0
```

**Resolution:**
1. Re-run scaffolding with `--force`: `py -3.12 production-pipeline/tools/apply_pipeline.py . --force`
2. Diff before overwriting: `diff backend/app/middleware/input_guardrails.py production-pipeline/templates/input_guardrails.py`
3. Future: `pineapple upgrade` (`[PLANNED]`, Tier 4) will do version comparison and diff-based upgrades

### Template Upgrade (Planned)

When `pineapple_upgrade.py` is implemented (Tier 4):
```bash
py -3.12 production-pipeline/tools/pineapple_upgrade.py .
# Compares template versions, shows diffs, applies updates
```

Until then, manually diff and re-apply with `--force`.

---

## 7. Data Recovery

### Bible YAML

**Location:** `projects/<name>-bible.yaml` in the project, and `~/.claude/projects/d--Claude-local/memory/projects/` for the memory copy.

**If missing:**
1. Check git: `git log --oneline -- projects/*-bible.yaml`
2. Restore from git: `git checkout <commit> -- projects/<name>-bible.yaml`
3. If not in git: re-scaffold with `apply_pipeline.py` to create a blank bible, then manually re-enter gaps

**If corrupted (invalid YAML):**
1. Check git for last good version
2. Validate YAML syntax: `py -3.12 -c "import yaml; yaml.safe_load(open('projects/<name>-bible.yaml'))"`
3. If beyond repair: restore from git or recreate

### Session Handoffs

**Location:** `sessions/YYYY-MM-DD.md`

**If missing:**
1. Check git log for the session date: `git log --after="2026-03-14" --before="2026-03-16" --oneline`
2. Reconstruct from git diff: `git diff <start-commit>..<end-commit> --stat`
3. Write a retroactive handoff based on commit messages

### Configuration

**Location:** `~/.pineapple/config.yaml`

**If corrupted or missing:**
1. Delete and recreate from defaults:
```yaml
# ~/.pineapple/config.yaml
langfuse_url: http://localhost:3000
mem0_url: http://localhost:8080
neo4j_url: bolt://localhost:7687
deepeval_telemetry: false
wall_clock_timeout_hours: 4
max_build_verify_review_cycles: 3
cost_ceiling_usd: 200
```
2. Service URLs should match environment variables (`LANGFUSE_URL`, `MEM0_URL`, `NEO4J_HTTP_URL`)

### Verification Records

**Location:** `.pineapple/verify/<branch>.json` and `.pineapple/last_verify.json`

**If missing or corrupted:**
1. Re-run verification: `py -3.12 production-pipeline/tools/pineapple_verify.py .`
2. This regenerates both `last_verify.json` and the per-branch record (when per-branch support is implemented)
3. Do not manually create verification records -- the integrity hash will not validate

### Pipeline State

**Location:** `.pineapple/runs/<uuid>/state.json`

**If corrupted:** See Section 3 (Pipeline State Recovery) above.

---

## 8. Known Issues

### FastMCP v3 API Changes

**Issue:** `add_tool()` is replaced by the `@mcp.tool()` decorator in FastMCP v3.

**Impact:** MCP server templates using `add_tool()` will fail with newer FastMCP versions.

**Workaround:** Update the MCP server code to use the decorator pattern:
```python
# Old (FastMCP v2):
mcp.add_tool(my_function, name="my_tool")

# New (FastMCP v3):
@mcp.tool()
def my_tool():
    ...
```

**Tracking:** Update `mcp_server.py` template when FastMCP v3 is stable.

### Pydantic v2 Deprecations

**Issue:** `BaseModel.json()` is deprecated in Pydantic v2. Use `model_dump_json()` instead.

**Impact:** Deprecation warnings in logs. Will become errors in a future Pydantic release.

**Workaround:** Search and replace in project code:
```bash
grep -rn "\.json()" backend/app/ --include="*.py"
# Replace .json() with .model_dump_json() on Pydantic models
```

### Hookify Patches Overwritten on Plugin Update

**Issue:** Updating the hookify Claude plugin overwrites patches to `config_loader.py` and `pretooluse.py` in the plugin cache.

**Impact:** Rules stop firing. Stop rules do not block. Non-Bash tools get incorrectly blocked.

**Workaround:** After every hookify plugin update:
1. Re-apply `config_loader.py` patch (search BOTH CWD and `~/.claude/`)
2. Re-apply `pretooluse.py` patch (skip non-Bash/non-file tools)
3. Cache location: `~/.claude/plugins/cache/claude-plugins-official/hookify/*/`
4. Validate: `py -3.12 tools/validate_hookify.py`

### Windows Path Separators in Hookify Regex

**Issue:** Using `[/\\]` in hookify regex patterns causes matching failures on Windows.

**Impact:** Rules silently fail to match file paths.

**Workaround:** Use `.` (dot) instead of `[/\\]` for path separator matching in all hookify regex patterns.

### Cost Log Unbounded Growth

**Issue:** `_cost_log` list in `observability.py` grows without eviction during long sessions.

**Impact:** Memory usage increases linearly with session length. In very long sessions (hundreds of LLM calls), this can consume significant RAM.

**Workaround:** Restart the backend process between long sessions. A proper fix would add a maximum size with FIFO eviction (e.g., `collections.deque(maxlen=1000)`).

**Tracking:** Add eviction policy to `observability.py` template.

### Verification Record Per-Branch Support

**Issue:** `pineapple_verify.py` currently writes `last_verify.json` (global) but does not yet write per-branch records at `.pineapple/verify/<branch>.json`.

**Impact:** The hookify "No merge without tests" gate rule cannot yet enforce per-branch verification isolation.

**Workaround:** Only work on one feature at a time, or manually verify before merging each branch.

**Tracking:** Implement per-branch records in Tier 2 (pipeline_state.py).

### Docker Images Not SHA-Pinned

**Issue:** Docker base images use version tags (e.g., `python:3.12-slim`) instead of SHA digests.

**Impact:** A compromised or updated base image could change behavior without warning.

**Workaround:** Acceptable for local development. Pin SHA digests before any production deployment. Use `docker inspect --format='{{index .RepoDigests 0}}' <image>` to get the digest.

**Tracking:** Phase 5 (Docker SHA pinning in threat model mitigations).

---

## Quick Reference: Emergency Commands

```bash
# Full health check
py -3.12 production-pipeline/tools/pineapple_doctor.py

# Run all verification layers
py -3.12 production-pipeline/tools/pineapple_verify.py .

# Run specific verification layer
py -3.12 production-pipeline/tools/pineapple_verify.py . --layers 1,3

# Scaffold a new project
py -3.12 production-pipeline/tools/apply_pipeline.py <path> --stack fastapi-vite

# Dry run scaffolding (no files written)
py -3.12 production-pipeline/tools/apply_pipeline.py <path> --dry-run

# Re-stamp templates (overwrite existing)
py -3.12 production-pipeline/tools/apply_pipeline.py <path> --force

# Validate hookify rules
py -3.12 tools/validate_hookify.py

# End-to-end hookify test
cd %USERPROFILE% && py -3.12 "D:\Claude local\tools\test_hookify_e2e.py"

# Check all Docker containers
docker ps -a

# Restart all shared services
docker restart langfuse mem0 neo4j

# List git worktrees
git worktree list

# Remove stale worktree
git worktree remove <path>
```
