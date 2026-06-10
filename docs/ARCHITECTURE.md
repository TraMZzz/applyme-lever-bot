# Architecture & Design Spec

> Canonical design for the Lever auto-apply bot. Status: **design complete — ready to implement** (hardened after a five-lens adversarial review). Self-contained; Lever form/endpoint/captcha facts were verified by direct inspection of the live apply page; library versions verified on PyPI 2026-06.

## Contents
- [1. Design goals & locked decisions](#1-design-goals--locked-decisions)
- [2. High-level flow](#2-high-level-flow)
- [3. Component breakdown](#3-component-breakdown)
- [4. Data models](#4-data-models)
- [5. Detailed design by layer](#5-detailed-design-by-layer)
- [6. Outcome taxonomy & result model](#6-outcome-taxonomy--result-model)
- [7. Error handling & resilience](#7-error-handling--resilience)
- [8. Tech stack & tooling](#8-tech-stack--tooling)
- [9. Testing strategy](#9-testing-strategy)
- [10. Project layout](#10-project-layout)
- [11. Production & X1000-scale roadmap](#11-production--x1000-scale-roadmap)
- [12. Open risks — resolve on leverdemo](#12-open-risks--resolve-on-leverdemo)

---

## 1. Design goals & locked decisions

**Goal:** a clean, modular Python script that applies to 5 `jobs.lever.co` postings from a candidate profile + resume, handles invisible/Enterprise hCaptcha + Cloudflare anti-bot, simulates human behavior, records a truthful per-vacancy outcome, and ships with a short report.

**Locked decisions:**
1. **Approach** — stealth **browser automation** of the real `/apply` form (not raw requests).
2. **Submit mode** — `SUBMIT_MODE` defaults to **`dry-run`** (fill+solve, stop before POST). The **committed 5-attempt evidence is generated against Lever's `leverdemo` sandbox** (proves the pipeline without polluting real ATS); `real` runs into the 5 live postings only on explicit flag + confirmation. Real/sandbox/dry-run share one code path, three terminal behaviors.
3. **Scope** — clean single-process MVP CLI; **Chrome pre-flight is built**; the Enterprise `rqdata`-capture path is **designed and flagged** (not proven — silent-pass means the solver rarely fires). X1000-scale is roadmap only.
4. **Captcha/proxy** — silent-pass first; real CapSolver key wired as fallback; clean local IP, no proxies.
5. **Profile data** — used as-is, placeholder email swapped for a real mailbox (confirmation-email evidence only).
6. **Vacancy list** — config-driven; take the first `MAX_APPLIES` (default 5) from the provided list, log any dropped extras (the inputs list a 6th).

**Principles:** one responsibility per module + tested interfaces; **honest outcomes** — never claim `SUCCESS` without a `/thanks` confirmation, and **a crash is never a terminal state for a vacancy** (every divergence maps to a named status); implement the anti-bot/human-sim requirements for real; YAGNI on infra.

---

## 2. High-level flow

```text
pre-flight: locate Chrome + version check ; validate Settings (fail-fast)
load CandidateProfile + first MAX_APPLIES Vacancies
for each vacancy (sequential, log-normal inter-apply delay):
   fresh browser context ; warmup(session)         # homepage → dwell/scroll → posting (Cloudflare __cf_bm)
   open /apply ; FormSpec = parse(form + ALL cards JSON + rqdata?)
   upload resume → AWAIT /parseResume + settle → override standard fields → verify each .value
       fill cards (answers = rules.map(profile,cards) (+ llm fallback)) ; pre-submit completeness gate
   trigger invisible hCaptcha: silent-pass → proceed
        challenge renders → solve (≤90s, CapSolver→2Captcha) → inject h-captcha-response → proceed
   SUBMIT_MODE: dry-run → stop (DRY_RUN_READY) | sandbox → leverdemo | real → POST
   submit (human click; wait on /apply response; single-flight) ; classify outcome
   verify.py: best-effort mailbox poll (host-checked link) ; evidence.capture (redacted HTML + screenshots + HAR)
   append ApplyResult (+ result_string)
write output/results.json
```

The only per-posting variation is the cards JSON + which fields are `required`; standard fields/endpoints/captcha are constant across Lever tenants.

---

## 3. Component breakdown

| Module | Responsibility | Key deps |
|---|---|---|
| `cli` / `config` | argparse entry; `Settings` (pydantic-settings, `SecretStr`, fail-fast); **Chrome pre-flight** | pydantic-settings |
| `errors` | exception hierarchy (`RetryableError` vs `PermanentError`) for the retry filter | — |
| `models` | `CandidateProfile`, `WorkExperience`, `Vacancy`, `Card`/`CardField`, `FieldRef`, `FormSpec`, `ApplyResult` | pydantic |
| `profile_loader` | `data/profile.json` + resume PDF → `CandidateProfile` (egress-guarded) | models |
| `browser/engine` | zendriver launch (real Chrome, headful), stealth, `webdriver` guard; patchright auto-fallback | zendriver |
| `browser/human` | Bézier mouse via raw CDP; log-normal delays; typing cadence | stdlib `random`/`math` |
| `browser/warmup` | Warm the Cloudflare session before `/apply` | engine, human |
| `lever/form` | Parse standard fields + ALL `cards[…][baseTemplate]` + capture `rqdata` → `FormSpec` | selectolax |
| `lever/fill` | Resume upload → **parseResume barrier** → override + verify → fill cards → completeness gate | human, answers |
| `lever/locations` | Set `selectedLocation` JSON directly (skip gated autocomplete) | engine |
| `lever/submit` | Trigger hCaptcha, single-flight submit, classify (`/thanks` vs 400), honor `SUBMIT_MODE` | captcha |
| `lever/verify` | Best-effort: poll mailbox for Lever confirmation (host-checked link); NOT a gate | imap-tools |
| `captcha/base` | `Solver` protocol + `NoopSolver`; normalizes the two vendor token shapes | — |
| `captcha/capsolver` | CapSolver via async REST (`httpx`), ≤90s deadline | httpx |
| `captcha/twocaptcha` | 2Captcha fallback (`AsyncTwoCaptcha`) | 2captcha-python |
| `answers/rules` | Deterministic profile → card-answer mapping (option text) | models |
| `answers/llm` | Optional LLM fallback for unmapped required questions, output ∈ options | anthropic |
| `evidence` | Redacted HTML snapshot + screenshots + HAR + telemetry per attempt | engine |
| `runner` | Orchestrate vacancies, inter-apply pacing, owns the shared `httpx.AsyncClient`, writes results | all above |

---

## 4. Data models

`pydantic v2`; value models use `ConfigDict(extra="forbid", frozen=True)`; mutable defaults via `Field(default_factory=...)`.

```python
class SubmitMode(StrEnum): DRY_RUN="dry-run"; SANDBOX="sandbox"; REAL="real"   # default DRY_RUN

class WorkExperience(BaseModel):
    company: str | None; title: str | None; start: str | None; end: str | None; description: str | None = None

class CandidateProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")          # webhook_url etc. are NOT silently carried
    full_name: str; email: EmailStr; phone: str
    location: str; city: str; state: str; country: str
    links: dict[str, str] = Field(default_factory=dict)
    work_authorized: bool; requires_sponsorship: bool; willing_to_relocate: bool
    expected_salary: int | None = None; expected_salary_currency: str = "USD"
    total_experience_years: int | None = None
    work_experience: list[WorkExperience] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    resume_path: Path                                  # local; remote fetch is egress-guarded (§5)

class Vacancy(BaseModel):
    company: str; posting_id: str; url: HttpUrl
    @property
    def apply_url(self) -> str: return f"{str(self.url).rstrip('/')}/apply"

class FieldRef(BaseModel):                              # a standard form input
    input_name: str; field_type: str; required: bool
    selector: str | None = None; current_value: str | None = None   # autofilled value to override

class CardField(BaseModel):
    field_index: int
    field_type: Literal["multiple-choice","multiple-select","dropdown","text","textarea"]
    text: str; required: bool; options: list[str] = Field(default_factory=list)   # option TEXT
    input_name: str                                    # cards[<cardId>][fieldN]

class Card(BaseModel): card_id: str; fields: list[CardField]

class FormSpec(BaseModel):
    standard_fields: dict[str, FieldRef]
    cards: list[Card]                                  # ALL cards on the posting, not "the card"
    sitekey: str; account_id: str; posting_id: str
    rqdata: str | None = None                          # captured if Enterprise emits it

class ApplyResult(BaseModel):
    posting_url: str; company: str; posting_id: str
    status: Literal["SUCCESS","FAILED","CAPTCHA_BLOCKED","DRY_RUN_READY","DUPLICATE_SUSPECTED","RETRYABLE_ERROR"]
    reason: str = ""                                   # e.g. MISSING_REQUIRED_FIELD:phone, AUTOFILL_CONFLICT,
                                                       #      UNSUPPORTED_FIELD_TYPE:<t>, FORM_SCHEMA_UNMAPPED:<f>, RQDATA_UNAVAILABLE
    final_url: str | None=None; http_status: int | None=None; flagged_fields: list[str]=Field(default_factory=list)
    solver_used: Literal["none","capsolver","twocaptcha"]="none"; solve_ms: int | None=None
    rng_seed: int; cf_ray: str | None=None; attempts: int=1
    confirmation_email_url: str | None=None
    screenshot_paths: list[str]=Field(default_factory=list); html_snapshot_path: str | None=None; har_path: str | None=None
    started_at: datetime; finished_at: datetime
    @property
    def result_string(self) -> str: ...                # brief-literal form (see §6)
```

---

## 5. Detailed design by layer

### 5.1 Browser & stealth (`browser/`)
- **Pre-flight (config import / engine):** locate Chrome (platform paths + `CHROME_PATH` override), assert it exists, `--version` within a supported major range; on failure emit an actionable error and **exit non-zero before touching any vacancy**. Document the version contract.
- **`engine.py`** — `zendriver>=0.15.3`, real system Chrome, **headful**, `zd.Config(headless=False, browser_executable_path=…, lang="en-US", disable_webrtc=True)`; WebGL left real; **`browser_args` minimal, UA not spoofed** (the genuine fingerprint is the stealth — `navigator.webdriver` is genuinely false). **Startup guard:** `evaluate("navigator.webdriver")` must be falsy → else abort `WEBDRIVER_LEAK` (do **not** spoof, do **not** add `--disable-blink-features=AutomationControlled` — real Chrome lacks it; adding it is itself a tell). zendriver's default `--disable-infobars` already suppresses the automation infobar. **`evaluate()` boundary:** JS-set only genuinely hidden inputs (`accountId`, `resumeStorageId`, `selectedLocation`, `h-captcha-response`); visible fields are click+type. **patchright>=1.60** is wired as an **automatic fallback on zendriver launch/connect failure** (`launch_persistent_context(channel="chrome", headless=False, no_viewport=True)`, no custom UA/headers).
- **`human.py`** *(our code — engine `mouse_move` is straight-line; `python-ghost-cursor` is dead)* — Bézier path (stdlib `math`; smoothstep + perpendicular bow) dispatched per-waypoint via `cdp.input_.dispatch_mouse_event("mouseMoved", …)`, tracking cursor `(x,y)` ourselves, overshoot+correct on long moves, sub-pixel jitter + log-normal button-hold on click. **Log-normal per-action delays** (`random.lognormvariate(log(median), σ)`, classes keystroke/field-think/read/pre-submit, clamped), **real `asyncio.sleep`**. Char-by-char typing + rare typo+backspace on textareas. **Seed `random.Random(seed)` per run; log the seed.**
- **`warmup.py`** — never hit `/apply` cold: land on `jobs.lever.co/<company>`, run JS, **event-driven scroll** + dwell, then open the posting → Apply. One cookie jar + same fingerprint through the flow; finish before the ~30-min `__cf_bm` idle expiry; wait on `expect_request`/`expect_response`, not sleeps.

### 5.2 Lever interaction (`lever/`)
- **`form.py`** — parse via `selectolax`; read hidden values (`accountId`, `posting_id`, sitekey); decode **every** `cards[<id>][baseTemplate]` (+ EEO `surveysResponses`) JSON → `Card`/`CardField`; **capture `rqdata`** (§5.3). Returns `FormSpec`.
- **`fill.py`** — **parseResume barrier (not code-order):** `send_file` the resume → **await the `/parseResume` response** (`expect_response(r".*/parseResume.*")` under `asyncio.timeout`) → **settle-poll** until autofilled inputs stop changing → then override standard fields → **re-read each `.value` and assert it equals the canonical profile value** (write-verify-rewrite); persistent mismatch → `FAILED:AUTOFILL_CONFLICT` (do not submit). `/parseResume` timeout → proceed with manual fill + flag (`resumeStorageId` may stay empty). Then fill cards (radio/checkbox → Bézier-click the option whose **text** matches; `<select>` → by visible text; text/textarea → human-typed). **Fail-closed schema coverage:** unknown field-type → `FAILED:UNSUPPORTED_FIELD_TYPE:<t>`; required+unmapped+no-LLM → `FAILED:FORM_SCHEMA_UNMAPPED:<f>`. **Pre-submit completeness gate:** assert every required `FormSpec` field has a non-empty value before Submit.
- **`locations.py`** — set `location` text + hidden `selectedLocation` = `JSON.stringify(locationObject)`. The **exact schema is captured from a real `/searchLocations` response on leverdemo** and the injected blob validated against it pre-submit. Blank for single-location postings; if a 400 flags `location`, the fallback mints a **separate** hCaptcha token for `/searchLocations` (independent of the submit token) rather than deadlocking on the silent-pass path.
- **`submit.py`** — **silent-pass first**: click Submit (human), let invisible hCaptcha run, detect a challenge iframe; if it renders → solve → inject `h-captcha-response` → resubmit. **Single-flight guard** so our resubmit and Lever's own `hcaptchaTokenExpired` re-click can't both POST. Submit wrapped in `expect_response(r".*/apply.*")` under `asyncio.timeout`. **Classify:** `/thanks` → `SUCCESS`; 400 re-render with `p.error-message` → re-scrape `#application-form`: flagged required fields → `FAILED:MISSING_REQUIRED_FIELD:<f>`; none flagged + captcha unverified → `CAPTCHA_BLOCKED`. `SUBMIT_MODE` gates the terminal step.
- **`verify.py`** — best-effort: after `SUCCESS`, poll the mailbox (`imap-tools` in `asyncio.to_thread`, bounded ~120s/5s, `seen=False`, no mark-seen) for a `@hire.lever.co` message; **before visiting any link, assert host `== lever.co` / `*.lever.co`, https, no userinfo**; store `confirmation_email_url`. "No email" is expected — never fails the apply.

### 5.3 Captcha (`captcha/`)
- **`base.py`** — `Solver` protocol `async solve_hcaptcha(page_url, sitekey, ua, is_invisible=True, rqdata=None, proxy=None) -> str`; `NoopSolver` for silent-pass-only. Token-shape normalization lives **inside each implementer**.
- **`capsolver.py` (primary)** — async REST via the shared `httpx.AsyncClient` (the official SDK is stale: no async + 60s cap): `createTask` (`HCaptchaTaskProxyless`, `websiteKey`, `isInvisible=True`, `userAgent`, `enterprisePayload.rqdata` if present) → poll `getTaskResult` → `solution.gRecaptchaResponse`.
- **`twocaptcha.py` (fallback)** — `AsyncTwoCaptcha.hcaptcha(...)` → `result["code"]`.
- **Timing arithmetic (token TTL ~120s, single-use):** the solver must **return within ~90s** and `solve+POST < 120s`; treat a token older than ~100s as stale → re-solve. `MAX_CAPTCHA_RETRIES = 2` solve→submit cycles, then `CAPTCHA_BLOCKED`; the tenacity solve-loop `stop_after_delay < 120s`. `balance()` check at startup; `report(False)` on rejection.
- **`rqdata` interception (designed, flagged):** a document-start CDP hook (`Page.addScriptToEvaluateOnNewDocument`) wraps `window.hcaptcha.execute/render` to capture their first-arg object (carrying `rqdata`) into a JS global, read back via `evaluate` → `FormSpec.rqdata`. If Enterprise is detected but `rqdata` can't be captured → `CAPTCHA_BLOCKED:RQDATA_UNAVAILABLE` (don't waste a solver call). **Whether `rqdata` is emitted is resolved empirically on leverdemo** (§12).

### 5.4 Answers (`answers/`)
- **`rules.py` (primary)** — normalize each card question, match to intent, emit an answer **constrained to that card's option text** (work-auth→Yes, sponsorship→No, salary→`expected_salary`, state→from location, relocate→No, EEO→profile/"decline", agreements→checked); returns answer + `unmapped`.
- **`llm.py` (optional)** — unmapped *required* questions only: Anthropic SDK, **Claude Haiku 4.5** (Sonnet 4.6 upgrade), profile + question + allowed options → answer **validated ∈ options**; low-confidence flagged. Enabled only if `LLM_API_KEY` set.

### 5.5 Orchestration, evidence, CLI
- **`runner.py`** — first `MAX_APPLIES` vacancies, **sequential with a log-normal inter-apply delay** (minutes-scale; **fresh context/cookie jar per apply**); each under `asyncio.timeout`, wrapped so a failure becomes a result (never aborts the batch); owns one **`httpx.AsyncClient`** (DI-injected into solvers, closed in `finally`); `structlog.bind_contextvars(job_url, attempt)`; writes `results.json` (incl. `result_string`).
- **`evidence.py`** — full-page screenshots (filled + post-submit), **HTML snapshot with PII + the live `h-captcha-response` token REDACTED** (or snapshot the PII-free `/thanks` page), a **CDP Network → `network.har`** capture across warm→fill→solve→submit (so REPORT §3 cites observed requests), telemetry. **Capture failures are non-fatal**; pre-flight that `output/` is writable at startup. Under `output/screenshots/<company>/`.
- **`cli.py`/`config.py`** — argparse single command; `Settings(BaseSettings)` with `SecretStr` + `frozen=True` + fail-fast import; secrets via `.env` only.

---

## 6. Outcome taxonomy & result model

`status ∈ {SUCCESS, FAILED, CAPTCHA_BLOCKED, DRY_RUN_READY, DUPLICATE_SUSPECTED, RETRYABLE_ERROR}`. **Invariant:** every code path (including exceptions) maps to one of these — enforced by an exception-injection test. `reason` carries the detail (`MISSING_REQUIRED_FIELD:<f>`, `AUTOFILL_CONFLICT`, `UNSUPPORTED_FIELD_TYPE:<t>`, `FORM_SCHEMA_UNMAPPED:<f>`, `RQDATA_UNAVAILABLE`, `PAYLOAD_TOO_LARGE`, …).

**Mapping to the brief's literals (`result_string`):**

| status | result_string |
|---|---|
| SUCCESS | `success` |
| FAILED | `failed:<reason>` (e.g. `failed:missing_required_field:phone`) |
| CAPTCHA_BLOCKED | `captcha blocked` |
| DRY_RUN_READY | `dry_run_ready` (the "etc" tail) |
| DUPLICATE_SUSPECTED | `duplicate` |
| RETRYABLE_ERROR | `error:<reason>` |

Detection: `SUCCESS` = redirect to `…/thanks` (or 200 body with thanks markup); failure = HTTP 400 re-render. `DUPLICATE_SUSPECTED` is from our own ledger (Lever merges silently and still returns `/thanks`).

---

## 7. Error handling & resilience

- **`errors.py` hierarchy:** `ApplyError` → `RetryableError` {`NetworkError`, `CloudflareChallenge`, `SolverTimeout`} vs `PermanentError` {`SolverAuthError`, `SchemaUnmappedError`, `PayloadTooLargeError`, `AutofillConflict`}. `tenacity`'s `retry_if_exception_type(RetryableError)` — never retry a bad API key.
- **Classify, don't crash** — each vacancy wrapped; exceptions → a result, never abort the batch.
- **Retries (`tenacity`)** — bounded (`stop_after_attempt`/`_delay`), jittered (`wait_exponential_jitter`), `reraise=True`; `before_sleep` bridged to the `structlog` logger so retry lines carry `job_url`/`attempt`. `AsyncRetrying` for the captcha/mailbox poll loops.
- **Concurrency** — single `asyncio.run`; `asyncio.timeout` per fragile step; browser/page teardown in `finally`; **never swallow `CancelledError`**.
- **Idempotency** — own dedupe ledger keyed by `(normalized_email, company, posting_id)`.

---

## 8. Tech stack & tooling

### Why browser automation, not raw requests
The submit POST is double-gated: Cloudflare fingerprints TLS/JA4 + HTTP/2 *before JS runs*, and Lever's invisible/passive hCaptcha scores a *live browser environment* a headless HTTP client can't emit. A real browser fixes both + the `__cf_bm` cookie + the JS cards.

### Engine — why zendriver
Direct-CDP, no Playwright/Selenium shim → no `webdriver`/`Runtime.enable` leak; **the best performer in an independent 2026 headed/residential-IP benchmark — 28 OK / 3 gated / 0 hard-blocked across 31 Cloudflare targets** (note: that measures page *access*, not invisible-hCaptcha pass rate, which we measure ourselves on leverdemo). Rejected: undetected-chromedriver (unmaintained), playwright-stealth (loses to Cloudflare's protocol layer), Camoufox (no CDP, heavier), rebrowser/selenium-driverless (≈ vanilla). **patchright** is the permissive fallback (zendriver is AGPL-3.0).

### Dependencies (verified PyPI, 2026-06)

| Tier | Package | Floor | Role |
|---|---|---|---|
| core | `zendriver` | `>=0.15.3` | browser engine |
| core | `httpx` | `>=0.28` | async CapSolver REST + resume fetch |
| core | `pydantic[email]` | `>=2.12` | models + `EmailStr` (needs `email-validator`) |
| core | `pydantic-settings` | `>=2.14` | typed config from `.env` |
| core | `selectolax` | latest | fast, safe HTML parsing (form + 400 re-render + fixtures) |
| quality | `tenacity` | `>=9.1` | bounded/jittered/filtered async retries |
| quality | `structlog` | `>=26.1` | per-apply contextvars tracing |
| feature | `patchright` | `>=1.60` | fallback engine |
| feature | `2captcha-python` | `>=2.0.7` | fallback solver (`from twocaptcha import AsyncTwoCaptcha`) |
| feature | `imap-tools` | `>=1.13` | confirmation-email evidence |
| feature | `anthropic` | latest | optional LLM card-answer fallback |

**Dropped:** `numpy` (stdlib `math`/`random`), CapSolver SDK (REST via httpx), `curl_cffi` (raw-requests path rejected).

### Dev tooling
**uv** (with `pip install -e .` fallback so the grader isn't forced into uv); **ruff** (`>=0.15,<0.16`, `ASYNC` rules on); **basedpyright** (`>=1.39.7`, strict on our code, `reportMissingTypeStubs=false`); **pytest** (`>=8.4`) + **pytest-asyncio** (`>=1.4`, `asyncio_mode="auto"`) + pytest-cov (optional). Python 3.12+, standard GIL build, single package + `src/` layout.

---

## 9. Testing strategy

- **Unit (pure, fast):** card `baseTemplate` JSON → `Card` parsing; `answers.rules` mapping; the success/failure classifier + form parser written as **pure functions over `tests/fixtures/*.html`** (captured `/thanks` + `400` + apply-page bodies); solver token-shape normalization + failover with stubbed vendors; the **exception-injection test** proving every failure maps to a named status.
- **Integration (mocked network):** `form`/`fill`/`submit` against fixtures.
- **Smoke (live, opt-in, leverdemo):** end-to-end; **first metric = the in-browser silent-pass rate** (the load-bearing KPI), plus whether `rqdata` is emitted and the real `selectedLocation` schema.

---

## 10. Project layout

```text
src/applyme/
├── __main__.py · cli.py · config.py · errors.py · models.py · profile_loader.py
├── browser/ engine.py · human.py · warmup.py
├── lever/   form.py · fill.py · locations.py · submit.py · verify.py
├── captcha/ base.py · capsolver.py · twocaptcha.py
├── answers/ rules.py · llm.py
├── evidence.py · runner.py
data/    profile.json · resume.pdf            (git-ignored)
output/  results.json · screenshots/<co>/{*.png,snapshot.html,network.har}   (git-ignored)
tests/   unit/ · integration/ · fixtures/
docs/    ARCHITECTURE.md · REPORT.md
```

---

## 11. Production & X1000-scale roadmap

(Report material; not built.) `(candidate, posting)` queue + worker pool, one isolated profile each, throttled per IP/ASN. **Write-ahead resumability ledger** (INTENT_TO_SUBMIT before POST, OUTCOME after; `UNKNOWN_INFLIGHT` on crash → manual confirm, mailbox poll as tiebreaker — over-engineering for a supervised 5-apply run, essential at scale). Residential proxy pool (geo-matched, sticky-per-app), mobile reserve. Answer-mapping cache + LLM fallback with human-review queue. **Warmup parameter tuning** (dwell medians, scroll depth, warmth gate) correlated to silent-pass rate. Solver economics: silent-pass ⇒ most applies $0; at challenge-rate `c`, 1000 applies ≈ `c·1000·$0.0008`; proxy GB dominates. Logfire span-tree; dedupe ledger; quarantine bad identities/proxies. Confirm authorization + respect Lever/hCaptcha ToS before scaling.

---

## 12. Open risks — resolve on leverdemo

1. **Is `rqdata` actually emitted?** Capture live `hcaptcha.render/execute` args via the §5.3 hook. If yes, proxyless solving is off and each solve must forward a fresh blob; if the captured token is rejected → the solver path is unreliable, lean on silent-pass.
2. **Silent-pass rate** for clean-IP + headful + warmed sessions — the load-bearing KPI; measured as the first smoke metric.
3. **`selectedLocation` exact schema** — capture from a real `/searchLocations` response; validate the injected blob.
4. **Does the authenticated POST re-fingerprint harder than the GET?** Test a `leverdemo` submission.
5. **Card-schema variance** across the 5 real postings (only Aledade inspected) — fail-closed handling makes this a clean classified outcome, not a crash, but sizes the rules-vs-LLM split.
6. **Token timing** — confirm ~120s TTL/single-use empirically; keep solve≤90s.
7. **Chrome version drift** — the pre-flight's supported range vs an auto-updated Chrome; patchright fallback covers a launch failure.
8. **Legal/ToS** — authorization before any real-mode/scaled use.
