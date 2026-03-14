# Pineapple Pipeline -- Threat Model

> **Version:** 1.0.0
> **Last updated:** 2026-03-15
> **Scope:** Single-developer, single-machine local development environment.
> **Review cadence:** Re-evaluate at each Phase transition (currently Phase 4 -> Phase 5).

---

## 1. Assets

Assets are ranked by sensitivity. Compromise of higher-ranked assets has greater impact.

| Rank | Asset | Location | Sensitivity | Impact if Compromised |
|------|-------|----------|-------------|----------------------|
| 1 | LLM API keys (Claude, Gemini, Groq, Grok) | `.env` files, shell profile env vars | HIGH | Financial loss (unbounded API charges), service impersonation |
| 2 | Project source code | Git repositories on local disk | HIGH | IP theft, competitive advantage loss |
| 3 | User data in databases | SQLite files (current), PostgreSQL (planned) | MEDIUM | Data breach, privacy violation |
| 4 | Pipeline state and configuration | `.pineapple/` directory, `~/.pineapple/config.yaml` | MEDIUM | Pipeline manipulation, stage bypass, false verification |
| 5 | Bible / decisions / session state | `projects/*.yaml`, `decisions.md`, `sessions/*.md` | LOW | Loss of institutional knowledge, rework |
| 6 | Docker container contents | Docker volumes, image layers | MEDIUM | Data exfiltration, service compromise |
| 7 | Hookify rule files | `~/.claude/hookify.*.local.md` | MEDIUM | Enforcement bypass, gate circumvention |

---

## 2. Adversary Model

### 2.1 Prompt Injection Attacker

**Profile:** External or untrusted user submitting crafted input through the application's chat interface or any endpoint that forwards input to an LLM.

**Capability:**
- Craft malicious prompts that attempt to override system instructions
- Chain injection techniques (role-play, encoding, payload splitting)
- Target both direct injection (user input) and indirect injection (data fetched from external sources)

**Motivation:**
- Extract API keys or system prompts embedded in LLM context
- Override system behavior to produce harmful, biased, or unauthorized output
- Exfiltrate data from the application's database or file system through LLM tool use
- Cause the LLM to execute unintended tool calls (file writes, code execution)

**Threat level:** HIGH. This is the most likely and most impactful attack vector for AI-enabled applications.

### 2.2 Dependency Supply Chain

**Profile:** Malicious or compromised packages in PyPI, npm, or Docker Hub registries.

**Capability:**
- Arbitrary code execution during `pip install` (setup.py hooks) or `npm install` (postinstall scripts)
- Replace legitimate packages with typosquatted versions
- Inject backdoors into Docker base images
- Compromise transitive dependencies (dependencies of dependencies)

**Motivation:**
- Cryptomining using local GPU/CPU resources
- Data theft (API keys, source code, database contents)
- Establish persistence for future exploitation
- Supply chain pivot to downstream users if code is published

**Threat level:** MEDIUM. Mitigated by pinned versions and local-only deployment, but not eliminated.

### 2.3 Local Privilege Escalation

**Profile:** Another process running on the same Windows machine that accesses pipeline state, configuration, or secrets.

**Capability:**
- Read/write any file accessible to the current user
- Read environment variables from the process environment
- Access Docker socket and container contents
- Monitor network traffic on localhost (service-to-service communication)

**Motivation:**
- Steal API keys from `.env` files or environment variables
- Modify pipeline behavior by editing state files or hookify rules
- Forge verification records to bypass pipeline gates
- Access database contents (SQLite files are regular files)

**Threat level:** LOW. Single-developer machine with no shared access. Becomes MEDIUM if machine is shared or remote-accessible.

---

## 3. Attack Surface Analysis

| Surface | Entry Point | Current Mitigation | Residual Risk |
|---------|-------------|-------------------|---------------|
| FastAPI `/chat` endpoint | User message body | `input_guardrails.py` (67 compiled regex patterns), rate limiting (10/min) | Fail-open on middleware error: if guardrails middleware throws an exception, the request passes through unfiltered. To be fixed: fail-closed (Tier 4). |
| FastAPI `/generate` endpoint | Code generation prompt | Subprocess sandbox with AST whitelist for CadQuery execution | Path traversal if sandbox is bypassed. AST whitelist may not cover all dangerous constructs. |
| FastAPI `/export` endpoint | Export format and parameters | Input validation, file path sanitization | Directory traversal via crafted export paths if sanitization is incomplete. |
| MCP tool inputs | External AI agent calls via MCP protocol | JSON schema validation on tool parameters | Tool injection if schema validation is insufficient. Malicious agent could craft parameters that pass schema but exploit tool implementation. |
| Template placeholders | `apply_pipeline.py` config dict | Only known keys from `detect_project()` are replaced | Unknown placeholders pass through unprocessed as literal `{{PLACEHOLDER}}` strings. Not a security risk, but a correctness issue. |
| Docker images | `FROM` directives in Dockerfiles | Hardcoded base images with version tags (e.g., `python:3.12-slim`) | No SHA digest pinning. A compromised Docker Hub image at the same tag would be pulled. To be fixed: SHA pinning (Phase 5). |
| Environment variables | Shell profile, `.env` files | `.gitignore` prevents `.env` from being committed to git | Plain text storage, no encryption, no rotation policy. Any local process can read env vars. |
| Hookify rules | `~/.claude/hookify.*.local.md` files | File system permissions (user-level) | No integrity checking. Rules can be silently modified. Plugin updates overwrite patches without warning. |
| Pipeline state files | `.pineapple/runs/<uuid>/state.json` | File system permissions (user-level) | No encryption, no signing. Any local process can read or modify state. A modified state file could trick the pipeline into skipping stages. |
| Verification records | `.pineapple/verify/<branch>.json` | Integrity hash (SHA256 of evidence + metadata) | Hash is computed locally -- a process with file access could recompute a valid hash for forged evidence. True signing (asymmetric keys) is not implemented. |
| SQLite database | `*.db` files on local disk | File system permissions | No encryption at rest. Any local process can read the database. No row-level access control. |
| Shared service ports | localhost:3000 (LangFuse), :8080 (Mem0), :7474/:7687 (Neo4j) | Bound to localhost only | No authentication on service APIs by default. Any local process can query LangFuse traces or Mem0 memories. |
| Git repositories | Local `.git/` directories | Standard git permissions | Sensitive data in git history if `.env` was ever committed. `git filter-branch` or BFG needed to remove. |

---

## 4. Mitigations In Place

### 4.1 Input Sanitization

**Component:** `input_guardrails.py` (template: `templates/input_guardrails.py`)

**Coverage:** 67 compiled regex patterns covering:
- Direct instruction override ("ignore previous instructions")
- Role-play injection ("you are now DAN")
- Encoding-based bypass attempts (base64, hex, unicode escapes)
- Payload splitting and delimiter injection
- System prompt extraction attempts
- Tool/function call manipulation
- Data exfiltration via output formatting

**Limitation:** Pattern-based detection has inherent false negatives. Novel injection techniques not covered by existing patterns will pass through. Defense-in-depth requires additional layers (output validation, model-level guardrails).

### 4.2 Rate Limiting

**Component:** `rate_limiter.py` (template: `templates/rate_limiter.py`)

**Coverage:** Per-route slowapi limits:
- Default: 60 requests/minute
- Chat endpoints: 10 requests/minute
- Export endpoints: 5 requests/minute

**Limitation:** IP-based limiting. Does not prevent distributed attacks (not relevant for single-machine deployment).

### 4.3 Resilience

**Component:** `resilience.py` (template: `templates/resilience.py`)

**Coverage:**
- Circuit breaker: opens after N consecutive failures, prevents cascade
- Retry with exponential backoff: handles transient failures
- Fallback: graceful degradation when primary service is unavailable

**Limitation:** Circuit breaker state is in-memory. Resets on process restart.

### 4.4 CORS

**Component:** FastAPI CORS middleware (per-project configuration)

**Coverage:** Restricted allowed origins. Only the frontend origin is permitted for cross-origin requests.

**Limitation:** CORS is a browser-enforced policy. Does not protect against non-browser clients (curl, scripts).

### 4.5 Subprocess Sandbox

**Component:** CadQuery execution engine in KFS

**Coverage:**
- AST whitelist: only approved Python constructs are allowed in generated code
- Path traversal prevention: generated code cannot access files outside the project directory
- Timeout: subprocess killed after configurable timeout

**Limitation:** AST whitelist may have gaps. New Python syntax or constructs added in future versions may not be covered.

### 4.6 Secret Exclusion

**Component:** `.gitignore` entries

**Coverage:**
- `.env` files excluded from git
- `apply_pipeline.py` automatically adds `.env` to `.gitignore` during scaffolding

**Limitation:** Only prevents accidental commits. Does not prevent secrets from being read by other local processes. No encryption. No rotation policy.

### 4.7 Hookify Enforcement

**Component:** 11 KFS-specific rules + 5 pipeline gate rules (planned)

**Coverage:**
- KFS rules: prevent dangerous file operations, enforce coding standards
- Pipeline gates (planned): block code without spec, block merge without verification

**Enforcement levels:**
- BLOCK (`action: stop`): physically prevents the action
- WARN (`action: warn`): logs warning but allows the action

**Limitation:** Rules are file-based with no integrity checking. A malicious process could modify rules to disable enforcement. Plugin updates overwrite patches.

### 4.8 Cost Tracking

**Component:** `observability.py` (template: `templates/observability.py`)

**Coverage:**
- Per-request cost calculation using model cost tables
- Cost ceiling alert at $200 per session
- Token usage tracking per model

**Limitation:** `_cost_log` grows unbounded in long sessions. Cost data is in-memory only (lost on restart unless LangFuse is connected).

---

## 5. Mitigations Planned

### 5.1 Fail-Closed Guardrails (Tier 4)

**Current state:** `input_guardrails.py` is fail-open. If the middleware raises an exception during pattern matching, the request passes through unfiltered.

**Planned fix:** Change the exception handler to return HTTP 500 instead of passing through. Any error in the guardrails layer blocks the request.

**Implementation:**
```python
# Current (fail-open):
except Exception as e:
    logger.error(f"Guardrail error: {e}")
    # Request continues to handler

# Planned (fail-closed):
except Exception as e:
    logger.error(f"Guardrail error: {e}")
    return JSONResponse(status_code=500, content={"error": "Request blocked: guardrail error"})
```

**Timeline:** Tier 4 (Production Hardening)

### 5.2 Signed Verification Records (Tier 2)

**Current state:** Verification records use SHA256 integrity hash, but the hash is computed locally. Any process with file access can forge a valid hash.

**Planned fix:** Use HMAC-SHA256 with a secret key stored outside the project directory (in `~/.pineapple/signing_key`). The key is generated once during bootstrap and never committed to git.

**Limitation:** This is not true asymmetric signing. A process that can read `~/.pineapple/signing_key` can still forge records. True PKI is out of scope for single-developer deployment.

**Timeline:** Tier 2 (Core Pipeline State)

### 5.3 Docker SHA Pinning (Phase 5)

**Current state:** Dockerfiles use version tags (e.g., `python:3.12-slim`, `node:20-alpine`).

**Planned fix:** Pin exact image digests:
```dockerfile
# Current:
FROM python:3.12-slim

# Planned:
FROM python:3.12-slim@sha256:<digest>
```

**Timeline:** Phase 5 (Smart). Acceptable risk for local development until then.

### 5.4 Secret Rotation and Keyring (Phase 6)

**Current state:** API keys stored as plain text in `.env` files and environment variables.

**Planned fix:**
- Store secrets in the OS keyring (Windows Credential Manager) via the `keyring` Python package
- Rotate API keys on a schedule (monthly for non-critical, weekly for billing-sensitive)
- Remove secrets from `.env` files entirely; load from keyring at runtime

**Timeline:** Phase 6 (Multi-User). Current risk is accepted for single-developer deployment.

### 5.5 RBAC (Phase 6)

**Current state:** No authentication or authorization. All endpoints are publicly accessible on localhost.

**Planned fix:**
- OAuth2 or API key authentication middleware
- Per-user database isolation
- Role-based access control (admin, user, readonly)

**Timeline:** Phase 6 (Multi-User). Not needed for single-developer deployment.

### 5.6 Service Authentication (Phase 5)

**Current state:** Shared services (LangFuse, Mem0, Neo4j) have no authentication on localhost.

**Planned fix:**
- Enable authentication on Neo4j (username/password)
- Configure LangFuse API keys
- Configure Mem0 authentication

**Timeline:** Phase 5 (Smart). Low risk while services are localhost-only.

---

## 6. Risk Acceptance

Risks are accepted when the cost of mitigation exceeds the expected impact, given the current deployment model (single developer, single machine, local only).

| Risk | Severity | Accepted? | Rationale |
|------|----------|-----------|-----------|
| No RBAC / no authentication | Medium | Yes (until Phase 6) | Single-developer, single-machine environment. No external users. Adding auth now would add complexity without security benefit. |
| Secrets in plain-text env vars | Medium | Yes (until Phase 6) | Local machine only, not deployed to any server. OS-level access controls are the defense layer. Keyring migration planned for Phase 6. |
| No Docker SHA pinning | Low | Yes (until Phase 5) | Version tags are sufficient for local development. Risk of compromised Docker Hub image at exact version tag is low. Pin before any production deployment. |
| Hookify rules can be bypassed | Medium | No -- fixing in Tier 3 | Switching to BLOCK mode (`action: stop`) with audit trail. Adding 5 pipeline gate rules. However, a local process can still modify rule files (accepted as local privilege escalation risk). |
| Pipeline state readable by local processes | Low | Yes | Local machine, no shared access. Encrypting state files would add complexity without meaningful security benefit in a single-user environment. |
| Fail-open guardrails | High | No -- fixing in Tier 4 | Middleware errors should block requests, not pass them through. This is a correctness bug, not a design tradeoff. |
| No service authentication on localhost | Low | Yes (until Phase 5) | Services bound to localhost only. No external network access. Any local process can already read files directly. |
| Verification records forgeable by local process | Low | Yes | HMAC signing (Tier 2) raises the bar. True PKI is disproportionate for single-developer setup. |
| Cost log unbounded memory growth | Low | Yes (short-term) | Acceptable for sessions under ~500 LLM calls. Fix by adding `deque(maxlen=1000)` to `observability.py`. |
| AST whitelist gaps in subprocess sandbox | Medium | Yes (with monitoring) | Whitelist is conservative. Monitor for new Python constructs that bypass the whitelist. Add constructs to the blocklist as discovered. |

---

## 7. Trust Boundaries

```
+------------------------------------------------------------------+
|  TRUSTED ZONE: Local Machine (User Account)                      |
|                                                                  |
|  +----------------------------+  +----------------------------+  |
|  | Developer Tools            |  | Pipeline State             |  |
|  | - Claude Code CLI          |  | - .pineapple/runs/         |  |
|  | - Git                      |  | - .pineapple/verify/       |  |
|  | - Python 3.12              |  | - ~/.pineapple/config.yaml |  |
|  | - Hookify rules            |  | - projects/*-bible.yaml    |  |
|  +----------------------------+  +----------------------------+  |
|                                                                  |
|  +----------------------------+  +----------------------------+  |
|  | Docker Services            |  | Application Under Dev      |  |
|  | - LangFuse (:3000)         |  | - FastAPI backend (:8000)  |  |
|  | - Mem0 (:8080)             |  | - Vite frontend (:3000)    |  |
|  | - Neo4j (:7474/:7687)      |  | - SQLite databases         |  |
|  +----------------------------+  +----------------------------+  |
|                                                                  |
+-------------------------------+----------------------------------+
                                |
          TRUST BOUNDARY        | (network, external services)
                                |
+-------------------------------+----------------------------------+
|  UNTRUSTED ZONE                                                  |
|                                                                  |
|  +----------------------------+  +----------------------------+  |
|  | External APIs              |  | Package Registries         |  |
|  | - Anthropic (Claude)       |  | - PyPI                     |  |
|  | - Google (Gemini)          |  | - npm                      |  |
|  | - Groq                     |  | - Docker Hub               |  |
|  | - xAI (Grok)              |  |                            |  |
|  +----------------------------+  +----------------------------+  |
|                                                                  |
|  +----------------------------+  +----------------------------+  |
|  | User Input                 |  | External Data              |  |
|  | - Chat messages            |  | - Fetched URLs             |  |
|  | - File uploads             |  | - MCP tool responses       |  |
|  | - API requests             |  | - Imported STEP/STL files  |  |
|  +----------------------------+  +----------------------------+  |
|                                                                  |
+------------------------------------------------------------------+
```

**Key trust boundary crossings:**

1. **User input -> FastAPI endpoints:** Crosses from untrusted to trusted. Mitigated by input_guardrails.py, rate limiting, and input validation.

2. **LLM API responses -> Application logic:** External API response is semi-trusted. LLM output should be validated before being used as code or tool calls.

3. **Package install -> Local execution:** Untrusted code from registries executes locally. Mitigated by pinned versions, but not by signature verification.

4. **MCP tool calls -> Application state:** External agent's tool calls modify trusted state. Mitigated by JSON schema validation on tool parameters.

5. **Generated code -> Subprocess execution:** LLM-generated code runs in a sandboxed subprocess. The sandbox (AST whitelist) is the trust boundary.

---

## 8. Incident Response Checklist

If a security incident is suspected:

1. **Contain:** Stop the affected service (`docker stop <name>`, kill FastAPI process)
2. **Rotate:** Immediately rotate all API keys (Anthropic, Google, Groq, xAI dashboards)
3. **Audit:** Check LangFuse traces for unusual patterns (if LangFuse is running)
4. **Check files:**
   - `git status` for unexpected file modifications
   - `git diff` for injected code
   - Check `.env` files for unauthorized changes
   - Check hookify rules for modifications: `git diff ~/.claude/hookify.*.local.md`
5. **Check Docker:** `docker ps -a` for unexpected containers, `docker logs <name>` for anomalies
6. **Check network:** `netstat -ano` for unexpected outbound connections
7. **Restore:** `git checkout` to a known-good commit if code was modified
8. **Document:** Write an incident report in `sessions/` with timeline, impact, and remediation
9. **Improve:** Add new guardrail patterns, tighten sandbox, update this threat model

---

## 9. Compliance Notes

This threat model is for a local development environment and does not address production deployment compliance requirements. Before deploying any application built with Pineapple Pipeline to production:

- Conduct a deployment-specific threat model
- Enable Docker SHA pinning (Phase 5 mitigation)
- Implement authentication and RBAC (Phase 6 mitigation)
- Enable encryption at rest for databases
- Enable TLS for all service-to-service communication
- Implement secret rotation (Phase 6 mitigation)
- Set up log aggregation and alerting
- Perform penetration testing on the deployed application
