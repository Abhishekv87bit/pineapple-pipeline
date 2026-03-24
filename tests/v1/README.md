# Legacy v1 Tests

These tests cover the **v1 tools/** modules (`pipeline_state.py`, `pipeline_tracer.py`,
`pineapple_audit.py`, `pineapple_config.py`, `pineapple_verify.py`, etc.).

They do NOT test the v2 `src/pineapple/` package (LangGraph agents, Pydantic models,
Instructor LLM router, gates, graph).

## Running v1 tests

```bash
PYTHONPATH=tools pytest tests/v1/ -v
```

## Why separated

The v1 tools/ modules are standalone CLI scripts from the original pipeline design.
The v2 architecture (`src/pineapple/`) is a LangGraph-based agent pipeline with
Pydantic models and Instructor-based LLM calls. The two share no code.

Keeping v1 tests in the main `tests/` directory gave false confidence: "288 tests
pass" sounded good but none of them exercised v2 code.

## Status

These tests remain runnable for as long as the `tools/` directory exists.
Once `tools/` is fully deprecated, these tests can be deleted.
