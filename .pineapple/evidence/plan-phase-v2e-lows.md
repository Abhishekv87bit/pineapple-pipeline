# Plan: Phase v2e — 7 LOW fixes

## What will be built/fixed

7 code quality issues remaining from the brutal honesty audit.

| # | Issue | File | Fix |
|---|-------|------|-----|
| L-1 | MCP silently swallows exceptions | mcp_server.py | Add logging to except block |
| L-2 | Claude/Gemini code paths identical | llm.py | Deduplicate create() branches |
| L-4 | Hardcoded $200 cost ceiling | gates.py | Read from env var PINEAPPLE_COST_CEILING |
| L-5 | Hardcoded model names | llm.py | Read from env vars PINEAPPLE_MODEL_GEMINI/CLAUDE |
| L-6 | integrity_hash always empty | verifier.py | Compute SHA256 of verification results |
| L-7 | tools_available never checked | builder.py | Warn if git unavailable before git commit |
| L-8 | _append_pydantic.py undocumented | tests/_append_pydantic.py | Delete if unused, document if used |

## Expected outputs

| File | Action | Description |
|------|--------|-------------|
| src/pineapple/mcp_server.py | MODIFY | Add print/logging in except block |
| src/pineapple/llm.py | MODIFY | Env var overrides for models, deduplicate create() |
| src/pineapple/gates.py | MODIFY | Env var for cost ceiling |
| src/pineapple/agents/verifier.py | MODIFY | Compute integrity_hash |
| src/pineapple/agents/builder.py | MODIFY | Check tools_available before git ops |
| tests/_append_pydantic.py | DELETE or DOCUMENT | Clean up |

## Verification commands

| Command | Expected Result |
|---------|----------------|
| python -c "from pineapple.gates import review_gate; import os; os.environ['PINEAPPLE_COST_CEILING']='100'; ..." | Cost ceiling reads from env |
| python -c "from pineapple.llm import _get_model_name; import os; os.environ['PINEAPPLE_MODEL_GEMINI']='gemini-2.0-flash'; ..." | Model name reads from env |
| python -c "from pineapple.agents.verifier import verifier_node; ..." | integrity_hash is non-empty SHA256 |
| pytest tests/test_agents.py tests/test_integrations.py tests/test_cli.py tests/test_mcp.py --tb=short | All pass, 0 failures |

## Acceptance criteria
- All 7 fixes applied
- No hardcoded values where env vars should be
- integrity_hash produces real SHA256
- All 114+ tests still pass
- Each fix verified with real inputs
