# Dogfood Report: KFS Manifest System v2 (Workspace Design Fix)

**Date:** 2026-03-24
**Run ID:** 7dfa5eae
**Duration:** 722.9 seconds (~12 minutes)
**Total LLM Cost:** $0.0071
**Provider:** Gemini (via Instructor router)
**Script:** `dogfood_kfs_full.py` (v2 -- workspace_info set to None, let setup_node handle it)

## Changes From v1

The v1 dogfood pre-configured `workspace_info` with KFS paths, but setup_node overwrote it with its own worktree. This v2 run:
1. Set `target_dir = KFS repo path` in state
2. Set `workspace_info = None` to let setup_node create workspace
3. Applied fix to setup_node to fall back to `target_dir` when worktree fails

## What Happened

### Stage 4: Setup
- **Target dir correctly resolved** to `D:\Claude local\kinetic-forge-studio`
- **Worktree creation FAILED** -- Windows filename-too-long error on 3 files in the KFS repo (1266-file checkout hit `fatal: Could not reset index file to revision 'HEAD'`)
- The setup_node fix (commit 4001246) was applied AFTER this run started, so `worktree_path` was set to `None`
- Run dir correctly created in KFS: `D:\Claude local\kinetic-forge-studio\.pineapple\runs\7dfa5eae-...`
- 7 files scaffolded (but scaffolded into CWD since worktree_path was None)

### Stage 5: Build
- **BUG: Builder fell back to CWD** -- `workspace_info.get("worktree_path") or os.getcwd()` resolved to the pineapple-pipeline repo, not KFS
- All 6 tasks completed (0 failed)
- 7 files written to disk -- **all in the pipeline repo, not KFS**
- 6 git commits created in the pipeline repo (wrong repo!)
- Cost: $0.0052

#### Files Generated

| File | Task | Lines | Quality |
|------|------|-------|---------|
| `backend/kfs_manifest_types.py` | T1 | ~100 | Good. Pydantic models with discriminated unions. |
| `tests/test_kfs_manifest_types.py` | T2 | ~140 | Broken. Tests Pydantic models but with wrong import paths. |
| `backend/kfs_manifest_parser.py` | T3 | ~100 | Improved from v1. Uses PyYAML this time (inter-task context helped). |
| `tests/test_kfs_manifest_parser.py` | T4 | Empty | No tests implemented. |
| `backend/kfs_schema_manager.py` | T5 | ~80 | Improved from v1. Actual implementation with semver. |
| `backend/schemas/manifest_v1.0.yaml` | T5 | ~30 | Has actual schema content this time. |
| `tests/test_kfs_schema_manager.py` | T6 | ~100 | Real tests this time. |

### Stage 6: Verify
- **3 passed, 3 failed** (same as v1)
- pytest: FAIL (ran pipeline's test suite, not generated KFS tests)
- test_files_exist: pass (22 test files)
- syntax_check: pass (all 20 files valid)
- security_scan: FAIL (flagged eval/exec in verifier.py -- same false positive)
- code_quality: FAIL
- domain_validation: pass

### Stage 7: Review
- **Verdict: RETRY** (6 critical, 5 important)
- Critical issues:
  1. KFSManifest structure differs from design spec
  2. Test types file tests wrong Pydantic models
  3. Parser imports types that don't exist in types module
  4. Parser test file is empty
  5. Schema manager missing semver dependency
  6. pytest collection failures on both test files
- The reviewer caught real issues -- good signal

### Stage 8: Ship
- **Action: KEEP** -- correctly refused to ship
- No PR created, no push attempted

### Stage 9: Evolve
- 4 decisions logged
- Session handoff written

## Comparison: v1 vs v2

| Metric | v1 | v2 | Notes |
|--------|----|----|-------|
| Duration | 325.9s | 722.9s | 2x slower (likely API latency variance) |
| Cost | $0.0094 | $0.0071 | Slightly cheaper |
| Build | 6/6 completed, 8 files | 6/6 completed, 7 files | Both completed all tasks |
| Verify | 3 pass, 3 fail | 3 pass, 3 fail | Same pattern |
| Review verdict | retry (3 critical) | retry (6 critical) | v2 reviewer was more thorough |
| Ship action | keep | keep | Both correctly refused |
| Workspace in KFS? | **No** | **No** | Root cause changed (v1: overwrote, v2: worktree failed) |
| Inter-task context | No | Yes | v2 parser used PyYAML (knew about T1) |

## Key Findings

### 1. Did workspace land in KFS repo?
**Partially.** The run dir and .pineapple structure correctly landed in KFS (`D:\Claude local\kinetic-forge-studio\.pineapple/`). But the worktree creation failed (Windows long filename), so `worktree_path` was None, and builder fell back to `os.getcwd()` which was the pipeline repo.

### 2. Did builder write files in KFS?
**No.** All 7 files and 6 commits landed in `D:\GitHub\pineapple-pipeline`. This is because `builder_node` line 252 falls back to `os.getcwd()` when `worktree_path` is None. **Fixed now** -- builder and verifier both fall back to `target_dir` before `os.getcwd()`.

### 3. Did verifier check KFS code?
**No.** Same CWD fallback issue. Verifier ran against the pipeline repo's own test suite. **Fixed now.**

### 4. Did inter-task context help later tasks?
**Yes -- partial improvement.** The `prior_context` feature (cumulative_files) told the LLM about previously written files. This resulted in:
- T3 parser: Actually used PyYAML this time (v1 used regex). Knew T1 types existed.
- T5 schema manager: Had real implementation (v1 was empty).
- T6 tests: Had real tests (v1 was empty).
- But import paths were still wrong and models didn't fully align.

### 5. What's the review verdict?
**Retry** -- the reviewer found 6 critical issues. The pipeline correctly refused to ship. This is the right behavior.

## Bugs Fixed During This Run

1. **builder_node workspace fallback** (`builder.py:252`): Changed from `worktree_path or os.getcwd()` to `worktree_path or target_dir or os.getcwd()`.
2. **verifier_node workspace fallback** (`verifier.py:420`): Same fix -- uses `target_dir` before falling back.
3. **setup_node worktree fallback** (already fixed in commit 4001246): When worktree creation fails, sets `worktree_path = target_dir` instead of `None`.

## Remaining Issues

1. **Windows long filenames** -- The KFS repo has files with names >260 chars. Git worktree fails on checkout. Either enable long paths in Windows or avoid worktrees for repos with long filenames.
2. **Verifier runs wrong test suite** -- Even with the workspace fix, the verifier will run ALL tests in the target repo, not just the generated ones. Need to filter to test files that were just created.
3. **No retry loop** -- When reviewer says "retry", the pipeline should automatically re-run builder for degraded tasks. Currently it just stops.
4. **KFS commits in pipeline repo** -- The 6 KFS commits (24a3ff8 through 2208b12) were accidentally committed to the pipeline repo. Files cleaned up manually.

## Files

- State: `D:\GitHub\pineapple-pipeline\.pineapple\dogfood\kfs-manifest-full-v2-7dfa5eae.json`
- Script: `D:\GitHub\pineapple-pipeline\dogfood_kfs_full.py`
- KFS run dir: `D:\Claude local\kinetic-forge-studio\.pineapple\runs\7dfa5eae-bbfb-4c83-aa49-893b017ed556\`

## Actionable Next Steps

1. **Commit the builder/verifier workspace fix** -- both now fall back to `target_dir`
2. **Enable Windows long paths** -- `git config --system core.longpaths true` or registry fix
3. **Scope verifier to generated files** -- pass `build_results.files_written` paths to pytest as targets
4. **Add retry loop** -- when review verdict is "retry", re-run build for tasks with critical issues
5. **Re-run dogfood v3** -- with all three workspace fixes in place, verify files land in KFS
