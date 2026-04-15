# Project Review — QBR Portfolio Health Report

> Comprehensive review: security audit, code quality, task alignment, architecture assessment.
> Conducted: 2026-04-15

## Project Stats

| Metric | Value |
|--------|-------|
| Total source lines | ~3,500 |
| Test count | 125 |
| Git commits | 18 (squash-merged) |
| PRs merged | 13 |
| GitHub issues | 14 + 1 follow-up |
| Python modules | 10 (qbr) + 1 (qbr_web) |
| LLM prompts | 3 versioned files |

---

## 1. Security Review

### FIXED (this review cycle)

| # | Severity | Finding | Fix |
|---|----------|---------|-----|
| S1 | **HIGH** | XSS via `\| safe` on LLM output in report.html | Replaced with `markdown` lib + `bleach` HTML sanitizer |
| S2 | **HIGH** | Path traversal in file upload (unsanitized filename) | `Path(filename).name` strips directory components |
| S3 | **MEDIUM** | No upload file size limit (claimed 5MB, not enforced) | Server-side 5MB limit + max 50 files |
| S4 | **MEDIUM** | No temp file cleanup after analysis | `shutil.rmtree()` in finally block |
| S5 | **MEDIUM** | Delimiter escape: email containing `</untrusted_email_content>` | Zero-width space injection in sanitizer |
| S6 | **MEDIUM** | Retry catches non-retryable errors (AuthenticationError) | Now catches only `APIConnectionError`, `RateLimitError`, `InternalServerError` |
| S7 | **MEDIUM** | Error details exposed in web UI | Generic error message; details only in server logs |
| S8 | **MEDIUM** | No rate limiting on `/analyze` endpoint | Max 3 concurrent analyses |
| S9 | **LOW** | Missing CSP and HSTS headers in Caddyfile | Added `Content-Security-Policy` and `Strict-Transport-Security` |

### Acknowledged (documented, acceptable for PoC)

| # | Severity | Finding | Status |
|---|----------|---------|--------|
| S10 | MEDIUM | No authentication on web app | Documented; add Caddy `basicauth` for production |
| S11 | MEDIUM | No CSRF protection | Not exploitable without auth; add when auth implemented |
| S12 | LOW | CDN JS without SRI hashes | Acceptable for PoC; vendor locally for production |
| S13 | LOW | Role-tag regex could be bypassed with Unicode whitespace | Covered by layered defense (spotlighting + grounding) |
| S14 | LOW | Debug mode can log PII | Disabled by default; documented in README |
| S15 | INFO | No dependency vulnerability scanning | No current CVEs; add `pip-audit` for production |

### OWASP LLM Top 10 Coverage

| ID | Vulnerability | Status |
|----|---------------|--------|
| LLM01 | Prompt Injection | **Mitigated** — 3-layer defense: spotlighting, sanitization (incl. delimiter escape), output grounding |
| LLM02 | Insecure Output Handling | **Fixed** — `bleach` sanitizer on all LLM output rendered as HTML |
| LLM06 | Sensitive Info Disclosure | **Low risk** — debug mode off by default, error messages sanitized |

---

## 2. Code Quality Review

### FIXED

| # | Severity | Finding | Fix |
|---|----------|---------|-----|
| Q1 | **HIGH** | Web app passes empty colleagues list (degrades severity scoring) | Now loads `Colleagues.txt` from input directory |
| Q2 | **HIGH** | Markdown rendering broken (naive `replace()` chain) | Proper `markdown` library + Jinja2 filter |

### Acknowledged (acceptable for PoC)

| # | Severity | Finding | Status |
|---|----------|---------|--------|
| Q3 | MEDIUM | Test coverage gaps: `create_hybrid_clients`, SSE, CLI integration | Core pipeline well-tested; gaps are in integration layer |
| Q4 | MEDIUM | Prompt file path assumes source tree layout | Works in Docker due to COPY; would need `importlib.resources` for pip-installed package |
| Q5 | MEDIUM | In-memory job store: no eviction, not multi-worker safe | Acceptable for PoC; noted for production |
| Q6 | LOW | `datetime.now()` timezone-naive in some places | Non-critical; messages are local timestamps |
| Q7 | LOW | Severity/status as `str` instead of `StrEnum` | Technical debt; doesn't affect correctness |
| Q8 | LOW | Dead code: `seed.get_seed_timestamp()` | Minor; no impact |

---

## 3. Task Specification Alignment

All 5 sections from `task/AI_Developer.pdf` are covered:

| Section | PDF Requirement | Blueprint Coverage | Verdict |
|---------|----------------|-------------------|---------|
| 1. Data Ingestion | Describe approach, scalability | Parser design + Mermaid scale-out diagram | **PASS** |
| 2. Analytical Engine | 1-2 flags, multi-step AI, prompts, security | 2 flags justified, 3-stage pipeline, 3 full prompts inline, hallucination mitigation | **PASS** |
| 3. Cost & Robustness | Robustness strategy, cost management | Haiku/Sonnet split, caching, Batch API, provenance-based conflict handling | **PASS** |
| 4. Monitoring & Trust | Key metrics, production trust | 8 metrics table, 4-point trust framework | **PASS** |
| 5. Architectural Risk | Single biggest risk + mitigation | "No ground-truth for flag validation" + 5 mitigations | **PASS** |

**Additional deliverables:**
- README.md with model justification: **PASS**
- Working PoC code: **PASS** (125 tests, CLI + web UI working)
- Prompts presented in Blueprint: **PASS** (all 3 with design rationale)

**Recommendation:** Add a labeled "### Security Considerations" sub-heading in Blueprint Section 2 to make it immediately visible to evaluators scanning headings.

---

## 4. Architecture Review

| Aspect | Assessment |
|--------|-----------|
| Pipeline design (parse → extract → resolve → age → flags → report) | **Sound** — clean separation, single responsibility per stage |
| Dual-LLM quarantine (extraction ↔ synthesis) | **Properly implemented** — synthesis never sees raw email, only structured flags |
| Provenance tracking (end-to-end) | **Complete** — SourceAttribution on every ExtractedItem and AttentionFlag |
| Hybrid provider routing | **Clean** — `create_hybrid_clients()` with auto-upgrade |
| LLM abstraction (`LLMClient` ABC) | **Provider-agnostic** — Anthropic + Ollama same interface |
| Quote-first grounding | **Effective** — fuzzy match filter catches hallucinated quotes |
| Off-topic detection | **Working** — keyword-based filter for social messages |

---

## 5. Summary

**Overall: Strong submission.** All task requirements met, 125 tests passing, security hardened with 9 findings fixed in this review cycle.

**Strengths:**
- Blueprint quality is high — specific, not generic; prompts with design rationale
- 3-layer security defense (spotlighting + sanitization + grounding) goes beyond what was asked
- Provenance tracking is genuinely end-to-end
- Hybrid Ollama/Anthropic routing is a practical cost optimization
- Verbose CLI output shows evaluators exactly what the system does

**Remaining items for production:**
- Authentication (Caddy `basicauth` or app-level auth)
- Persistent job store (SQLite or Redis)
- Dependency vulnerability scanning (`pip-audit`)
- SRI hashes on CDN-loaded JavaScript
