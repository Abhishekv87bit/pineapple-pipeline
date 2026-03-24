# Dogfood Report: KFS Manifest System (Full Pipeline Stages 5-9)

**Date:** 2026-03-24
**Run ID:** 7dfa5eae
**Duration:** 325.9 seconds (~5.4 minutes)
**Total LLM Cost:** $0.0094
**Provider:** Gemini (via Instructor router)

## What Happened

Ran Pineapple Pipeline stages 4-9 (Setup, Build, Verify, Review, Ship, Evolve) against the KFS Manifest System dry-run plan (6 of 24 tasks: T1-T6).

### Stage 4: Setup
- **BUG FOUND:** The `setup_node` ignored our pre-set `workspace_info` pointing at the KFS repo (`d:\Claude local\kinetic-forge-studio`). It created its own git worktree at `D:\GitHub\pineapple-pipeline\.pineapple\worktrees\7dfa5eae-...\` under the pineapple-pipeline repo instead. This means the generated code was NOT written to the KFS repo.
- Created branch `feat/kfs-manifest-system-7dfa5eae` in its own worktree.
- Scaffolded 7 placeholder files.

### Stage 5: Build
- **All 6 tasks completed** (0 failed). LLM generated real code, not stubs.
- **8 files written to disk** (some tasks wrote 2 files).
- 6 git commits created in the worktree.

#### Files Generated

| File | Quality Assessment |
|------|-------------------|
| `backend/kfs_manifest_types.py` (96 lines) | **Good.** Real Pydantic models: ManifestMetadata, GeometryDefinition (parametric/file/programmatic), MotionDefinition (kinematic/dynamic), KFSManifest with cross-reference validator. Well-structured discriminated unions. |
| `backend/kfs_manifest_parser.py` (104 lines) | **Bad.** LLM reinvented YAML parsing from scratch using regex instead of importing PyYAML. Can only handle flat key:value pairs. Defines its own `KFSManifest` dataclass that conflicts with the Pydantic one in types.py. |
| `backend/kfs_schema_manager.py` (2 lines) | **Empty.** Just `import os` and a blank line. Completely unimplemented. |
| `backend/schemas/manifest_v1.0.yaml` (1 line) | **Empty.** Just a comment header. No schema content. |
| `tests/test_kfs_manifest_types.py` (140 lines) | **Broken.** Redefines its own dataclasses instead of importing from types.py. Has a typo on line 48: `selfassertEqual` (missing dot). Would fail on execution. |
| `tests/test_kfs_manifest_parser.py` (237 lines) | **Disconnected.** Redefines its own parser class wrapping PyYAML, testing against that mock, not the actual parser module. Tests are well-structured but test the wrong thing. |
| `tests/test_kfs_schema_manager.py` (2 lines) | **Empty.** Just `import unittest`. |

### Stage 6: Verify
- **3 passed, 3 failed, 0 skipped**
- pytest: **FAIL** (timed out at 120s -- likely ran existing pineapple-pipeline test suite in the worktree)
- test_files_exist: pass (found 21 test files)
- syntax_check: pass (all 20 files valid syntax)
- security_scan: **FAIL** (flagged eval/exec in verifier.py -- false positive, that's pineapple's own code)
- code_quality: **FAIL** (no details)
- domain_validation: pass

### Stage 7: Review
- **Verdict: RETRY** (not pass, not fail)
- **3 critical issues:**
  1. Parser ignores PyYAML, can only do flat key:value -- makes the Pydantic types unusable
  2. Schema manager is empty
  3. pytest timed out
- **3 important issues:**
  1. Parser tests don't test the actual parser
  2. Code quality layer failed
  3. eval/exec flagged in verifier (false positive)

### Stage 8: Ship
- **Action: KEEP** (code stays on branch, no PR created)
- Correctly recognized that "retry" verdict means code is not ready to ship
- No git push or PR was attempted

### Stage 9: Evolve
- Logged 4 decisions: build success, verify issues, retry verdict, keep action
- Mem0/Neo4j/DSPy all stubbed (Phase 4)

## Key Findings

### Pipeline Bugs
1. **Setup node ignores workspace_info override.** It always creates its own worktree under `.pineapple/worktrees/`. The `workspace_info` we set in the state dict was overwritten by `setup_node`. To use a custom workspace, either (a) skip setup_node entirely and keep our state, or (b) modify setup_node to respect a pre-configured workspace.
2. **Verifier runs against the wrong test suite.** Since the worktree was a clone of pineapple-pipeline, the verifier ran pineapple-pipeline's own tests (which timed out), not the KFS manifest tests that were just generated.
3. **Security scan false positives.** It flagged pineapple's own verifier.py (eval/exec usage), not the generated code.

### LLM Code Quality Assessment
- **T1 (types):** Genuinely good. Proper Pydantic discriminated unions, cross-reference validation. Would work in production.
- **T2 (types tests):** Broken. Doesn't import the actual module, redefines its own types, has a typo.
- **T3 (parser):** Fundamentally wrong. LLM avoided PyYAML (listed as a dependency in the spec) and wrote a regex-based flat-key parser. Ignores the Pydantic models from T1.
- **T4 (parser tests):** Tests a mock parser, not the real one. Well-written tests but disconnected from the implementation.
- **T5 (schema manager):** Empty. LLM returned essentially no content.
- **T6 (schema manager tests):** Empty (just `import unittest`).

**Pattern:** The LLM (Gemini) produced one genuinely good module (T1), one fundamentally flawed one (T3 -- wrong approach), and two effectively empty ones (T5, T6). Tests are disconnected from implementations. This aligns with known Gemini behavior of degrading quality over a long task sequence.

### What the Reviewer Got Right
The reviewer correctly identified all three critical issues. The "retry" verdict was appropriate -- this code needs another build pass. The shipper correctly refused to PR it.

## Files on Disk

All generated files are in:
```
D:\GitHub\pineapple-pipeline\.pineapple\worktrees\7dfa5eae-bbfb-4c83-aa49-893b017ed556\
```

**Nothing was written to the KFS repo** (`d:\Claude local\kinetic-forge-studio`). The KFS repo is unchanged.

## State File
```
D:\GitHub\pineapple-pipeline\.pineapple\dogfood\kfs-manifest-full-7dfa5eae.json
```

## Actionable Next Steps
1. **Fix setup_node** to respect pre-configured `workspace_info` when `target_dir` differs from CWD.
2. **Fix verifier** to run only tests in the newly-generated test files, not the entire repo's test suite.
3. **Consider task-level context passing** -- the builder lost context between tasks (T3 parser didn't know about T1 types, T5/T6 degraded to empty output).
4. **Add retry loop** -- when reviewer says "retry", automatically re-run builder for failed/degraded tasks before giving up.
