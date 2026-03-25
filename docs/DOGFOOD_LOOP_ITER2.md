# Pineapple Pipeline Dogfood -- Iteration 2 Report

**Date**: 2026-03-24
**Run ID**: 3fb77ba4-cd74-42ba-9fc4-ad22b39edc7d
**Target**: KFS Manifest System on `d:\Claude local\kinetic-forge-studio`
**Provider**: Gemini
**Path**: Full (10 stages)

## Fixes Applied in Iteration 2

| Fix | Description | Status |
|-----|-------------|--------|
| A: Disable scaffolding when target_dir set | `setup.py` skips `_scaffold_files()` when `target_dir` exists | Applied |
| B: Builder overwrites stubs | `_write_files_to_disk()` detects stubs (<200 bytes with TODO/stub markers) and overwrites | Applied |
| C: Path enforcement in builder prompt | Added PATH ENFORCEMENT directive to `_SYSTEM_PROMPT` | Applied |
| D: Prior context includes actual code | Already working from iteration 1 (fw.content[:2000] in prior_context) | Verified |
| E: _MAX_TOKENS bumped to 8192 | Changed from 4096 to 8192 | Applied |

## Results

### Files Written (First Build Pass) -- 10 files

| File | Size (bytes) | Notes |
|------|-------------|-------|
| `src/kfs_manifest/models.py` | 8,977 | Core Pydantic models -- OVERWRITE STUB worked |
| `src/kfs_manifest/yaml_loader.py` | 1,240 | YAML loader utility |
| `src/kfs_manifest/parser.py` | 1,395 | Parser/validator |
| `src/kfs_manifest/cli.py` | 1,382 | CLI entry point -- OVERWRITE STUB worked |
| `tests/test_kfs_manifest_models.py` | 9,044 | Unit tests |
| `tests/test_kfs_manifest_yaml_loader.py` | 3,568 | YAML loader tests |
| `tests/test_kfs_manifest_parser.py` | 7,853 | Parser integration tests |
| `tests/test_kfs_manifest_cli.py` | 5,953 | CLI integration tests |
| `docs/manifest_spec.md` | 7,013 | Schema documentation |
| `examples/simple_sculpture.kfs.yaml` | 1,008 | Example manifest |
| `tests/test_documentation.py` | 5,738 | Documentation tests |

### What Improved vs Iteration 1

1. **Fix A worked**: No stubs were scaffolded (line 162: "Scaffolding: skipped (no task plan)"). Note: the "no task plan" message fired because scaffolding was skipped before task_plan was checked -- but the net effect is correct: no stubs blocking real code.
2. **Fix B worked**: `[OVERWRITE STUB]` messages appeared for `models.py` (77 bytes) and `cli.py` (74 bytes), confirming stub detection and overwrite.
3. **Fix C worked (partially)**: No files written to wrong paths (no `kfs_core/` directory invented). All files went to `src/kfs_manifest/` as planned.
4. **Fix E worked**: Files are substantially larger (models.py = 8,977 bytes vs would-be truncated). LLM had room to generate complete implementations.
5. **10/10 tasks completed** in the first build pass (0 failures).
6. **All files written to correct paths** -- no path invention.

### Reviewer Verdict

- **Pass 1**: `retry` (3 critical, 3 important, 4 minor)
- **Pass 2**: `fail` (4 critical, 0 important, 0 minor)
- **Pass 3**: `fail` (4 critical, 2 important, 2 minor) -> Human Intervention

### Critical Issues Found by Reviewer

1. **Schema inconsistency**: `models.py` uses `definitions`/`elements` structure but docs/examples use a different `spec` structure with inline `geometry`/`materials`/`motion`/`simulation` sections.
2. **Mocked tests**: Tests define their own mock Pydantic models instead of importing from `src/kfs_manifest/models.py`. Actual code is effectively untested.
3. **Test collection errors**: pytest reports 14 errors during collection -- tests can't even import.
4. **Documentation/schema mismatch**: `test_documentation.py` asserts top-level keys (`sculpture`, `materials`) that don't exist in the actual model.

### NEW Issue: Retry loop cannot fix code

The build-review-retry loop ran 3 times but **wrote 0 files on retry passes 2 and 3**. All files were `[SKIP]`ed because they were >200 bytes (real implementations from pass 1). The builder generates new code via LLM but `_write_files_to_disk()` refuses to overwrite.

**Root cause**: The stub detection (Fix B) only protects against <200 byte stubs. On retry, the files from the first pass are 1,000-9,000 bytes -- they're not stubs, but they need to be overwritten with improved versions.

## Issues for Iteration 3

### Issue 1: Retry must overwrite its own files (CRITICAL)
The builder needs a `force_overwrite` mode for retry passes. When the reviewer sends back to build, the builder should overwrite files it previously wrote in this run.

**Fix**: Track `cumulative_files` paths. On retry, if a file was written by a previous build pass in the SAME run, overwrite it.

Alternative: Pass a `is_retry=True` flag to `_write_files_to_disk()` that allows overwriting any file listed in the task plan's `files_to_create`/`files_to_modify`.

### Issue 2: Architecture stage failed silently (MODERATE)
Line 50: `[Architecture] ERROR: LLM call failed after retries: RetryError`. The design_spec was empty, meaning the builder had no architecture context. This likely contributed to schema inconsistency.

**Fix**: If architecture fails, either retry or halt the pipeline (don't continue with empty design_spec).

### Issue 3: Tests mock instead of importing real code (MODERATE)
Gemini generated tests that define their own mock models instead of `from kfs_manifest.models import ...`. This is a prompt engineering issue.

**Fix**: Add to builder prompt: "Tests MUST import from the actual source modules listed in files_to_create. Do NOT define mock/duplicate classes. Use the actual code from prior_context."

### Issue 4: Worktree creation failed (LOW)
Long filenames in the parent repo caused worktree checkout to fail. Pipeline fell back to writing directly to target_dir, which works but loses branch isolation.

**Fix**: Use `git worktree add --no-checkout` then sparse-checkout only the relevant paths. Or accept the fallback.

## Cost Summary

- Strategic Review: $0.0005
- Architecture: $0 (failed)
- Plan: $0.0010
- Build Pass 1: $0.0168 (10 tasks)
- Build Pass 2: $0.0101 (10 tasks, all SKIP)
- Build Pass 3: $0.0117 (10 tasks, all SKIP)
- Review x3: $0.0096
- **Total: ~$0.05**

## Comparison: Iteration 1 vs 2

| Metric | Iter 1 | Iter 2 |
|--------|--------|--------|
| Files written (pass 1) | ? (stubs blocked) | 10 (all planned) |
| Path invention | Yes (kfs_core/) | No |
| Stub overwrite | No | Yes (2 stubs overwritten) |
| Retry effectiveness | N/A | 0 files (blocked by SKIP) |
| Final verdict | ? | fail (but much closer) |
| Architecture stage | ? | Failed (empty design_spec) |

## Priority for Iteration 3

1. **MUST**: Enable retry overwrite (track written files per run, allow re-overwrite)
2. **MUST**: Add test import enforcement to builder prompt
3. **SHOULD**: Handle architecture stage failure (retry or halt)
4. **NICE**: Fix worktree long-filename issue
