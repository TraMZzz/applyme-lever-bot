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
3. **Scope** — clean single-process MVP CLI; **Chrome pre-flight is built**; an Enterprise `rqdata` slot is **threaded through but not captured** (`FormSpec.rqdata` is always `None` today — it only matters if a still-supporting solver is found, and silent-pass means the solver rarely fires). X1000-scale is roadmap only.
4. **Captcha/proxy** — **silent-pass first** (the load-bearing path); a CapSolver→2Captcha fallback is wired but, tested with live keys, **empirically unreliable for Lever's Enterprise hCaptcha** (CapSolver dropped hCaptcha; 2Captcha proxyless times out — REPORT §4); a non-passable challenge is recorded `CAPTCHA_BLOCKED`. Clean local IP, no proxies.
5. **Profile data** — used as-is, placeholder email swapped for a real mailbox (confirmation-email evidence only).
6. **Vacancy list** — config-driven; take the first `MAX_APPLIES` (default 5) from the provided list, log any dropped extras (the inputs list a 6th).

**Principles:** one responsibility per module + tested interfaces; **honest outcomes** — never claim `SUCCESS` without a `/thanks` confirmation, and **a crash is never a terminal state for a vacancy** (every divergence maps to a named status); implement the anti-bot/human-sim requirements for real; YAGNI on infra.

---

## 2. High-level flow

```text
pre-flight: locate Chrome + version check ; validate Settings (fail-fast)
load CandidateProfile + first MAX_APPLIES Vacancies
for each vacancy (sequential, log-normal inter-apply delay):
   fresh browser context ; _warm(page)             # company page → dwell → posting (Cloudflare __cf_bm)
   open /apply ; FormSpec = parse(form + ALL cards JSON + rqdata?)
   upload resume (set_input_files) → Playwright auto-waits through /parseResume re-render → override standard fields
       fill cards (answers = rules.map(profile,cards) (+ llm fallback))
   trigger invisible hCaptcha: silent-pass → proceed
        challenge renders → solve (≤90s, CapSolver→2Captcha) → inject h-captcha-response → proceed
   SUBMIT_MODE: dry-run → stop (DRY_RUN_READY) | sandbox → leverdemo | real → POST
   submit (human click; settle for silent-pass token; wait_for_load_state) ; classify outcome
   verify.poll_confirmation: best-effort mailbox poll (host-checked link) ; _capture (full-page screenshot + redacted HTML)
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
| `browser/pw_engine` | patchright (stealth Playwright) launch of the system Chrome (real, headful), `webdriver` guard (`assert_no_webdriver_leak`) | patchright |
| `browser/human` | Bézier mouse (dispatched via Playwright `page.mouse`); log-normal delays; typing cadence | stdlib `random`/`math` |
| `app_pw` | Single-vacancy flow: warm Cloudflare → `/apply` → parse → `resolve_answers` → `pw_fill` → dry-run evidence OR submit+captcha+confirmation | pw_engine, pw_fill |
| `lever/form` | Parse standard fields + ALL `cards[…][baseTemplate]` + capture `rqdata` → `FormSpec` | selectolax |
| `lever/pw_fill` | Resume `set_input_files` → human-override standard fields → answer cards, all via Playwright auto-waiting primitives | human, answers |
| `lever/locations` | Set `selectedLocation` JSON directly (skip gated autocomplete) — `build_selected_location` | — |
| `lever/submit` | `classify_outcome` (`/thanks` vs 400 re-render → SUCCESS/FAILED/CAPTCHA_BLOCKED/RETRYABLE_ERROR) | selectolax |
| `lever/verify` | Best-effort: `poll_confirmation` mailbox for Lever confirmation (host-checked link); NOT a gate | imap-tools |
| `captcha/base` | `solve_hcaptcha(...)` CapSolver→2Captcha failover → `(token, vendor)`; normalizes the two token shapes | — |
| `captcha/capsolver` | CapSolver via async REST (`httpx`), ≤90s deadline | httpx |
| `captcha/twocaptcha` | 2Captcha fallback (`AsyncTwoCaptcha`) | 2captcha-python |
| `answers/rules` | Deterministic profile → card-answer mapping (option text); `map_answers` + `is_sensitive` | models |
| `answers/llm` | Optional LLM fallback for unmapped required questions, output ∈ options; `answer_question` + `validate_choice` | anthropic |
| `evidence` | `redact_html(html)` ONLY — blanks PII + the live token; the screenshot/HTML capture lives in `app_pw._capture` | — |
| `runner` | Orchestrate vacancies sequentially, inter-apply pacing, per-vacancy timeout, writes `results.json` incrementally | all above |

---

## 4. Data models

`pydantic v2`; the input models (`CandidateProfile`, `WorkExperience`, `Vacancy`) use `ConfigDict(extra="forbid")` (unknown keys rejected, not silently carried); mutable defaults via `Field(default_factory=...)`.

```python
class SubmitMode(StrEnum): DRY_RUN="dry-run"; SANDBOX="sandbox"; REAL="real"   # default DRY_RUN

class WorkExperience(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company: str | None = None; title: str | None = None; start: str | None = None; end: str | None = None
    description: str | None = None

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
    model_config = ConfigDict(extra="forbid")
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
- **`pw_engine.py`** — `patchright>=1.60.1` (a stealth Playwright fork with anti-detection patches), driving the **real system Chrome** via `executable_path` (no separate browser download), **headful** by default; UA **not spoofed** (the genuine fingerprint is the stealth — `navigator.webdriver` is genuinely false), Chrome's own sandbox disabled only when running as root/CI (`JOOBLE_CHROME_NO_SANDBOX`). **Startup guard:** `assert_no_webdriver_leak` — `page.evaluate("navigator.webdriver")` must be falsy → else abort `WEBDRIVER_LEAK` (do **not** spoof). **Why patchright rather than raw CDP (the parseResume problem):** uploading the résumé triggers Lever's client-side `parseResume` **re-render that destroys the JS execution context**. Patchright tracks the execution-context lifecycle and **auto-waits through the navigation/re-render**, so `fill()`/`check()`/`select_option()` succeed; a raw-CDP driver's `Runtime.evaluate`/`callFunctionOn` instead **hang or crash the renderer** there (see *Engine — why patchright*). The hidden inputs (`selectedLocation`, `h-captcha-response`) are still set via `eval_on_selector`/`evaluate`; visible fields are click+type.
- **`human.py`** *(our code — `python-ghost-cursor` is dead; raw `mouse_move` is straight-line)* — Bézier path (stdlib `math`; smoothstep + perpendicular bow) dispatched per-waypoint via **Playwright `page.mouse.move`**, tracking cursor `(x,y)` ourselves, sub-pixel in-element jitter (`jittered_point`) on click. **Log-normal per-action delays** (`sample_delay` → `rng.lognormvariate(log(median), σ)`, clamped; classes `keystroke`/`field_think`/`read_page`/`pre_submit`/`inter_apply`), **real `asyncio.sleep`**. Char-by-char typing. **Seed `random.Random(seed)` per run; `rng_seed` is recorded on the `ApplyResult`.**
- **Warm-up (`app_pw._warm`)** — never hit `/apply` cold: land on `jobs.lever.co/<company>`, dwell, then open the posting before `/apply`. One context + same fingerprint through the flow; finishes before the ~30-min `__cf_bm` idle expiry.

### 5.2 Lever interaction (`lever/`)
- **`form.py`** — `parse_form_html` via `selectolax`; read hidden values (`accountId`, sitekey via `data-sitekey`, `posting_id` from the URL); decode **every** `…[baseTemplate]` (both `cards[…]` and EEO `surveysResponses[…]`) JSON → `Card`/`CardField` with the prefix preserved in `input_name`. Returns `FormSpec` with `rqdata=None` (the Enterprise `rqdata` slot is reserved but not yet captured — §5.3).
- **`pw_fill.py`** — **résumé first, then override (Playwright auto-wait, not a settle-poll):** `set_input_files('[name="resume"]', …)` → let Lever's `parseResume` re-render run → override standard fields with `_human_fill` (Bézier-move to the field, jittered click, `fill("")` to clear any autofill, then per-character `keyboard.type`). Playwright's `fill()`/`locator.wait_for` **auto-wait through the re-render** that destroys the JS context, so no manual barrier/`evaluate`-settle is needed (raw CDP hangs there — see §5.1 / *Engine — why patchright*). Then answer cards: `multiple-choice`/`multiple-select` → `page.check(value=…)`; `dropdown` → `select_option(label=…)`; text/textarea → `fill(…)` — all auto-waiting, bounded by per-call timeouts. The hidden `selectedLocation` blob is set via `eval_on_selector`.
- **`locations.py`** — `build_selected_location(display)` returns `(visible text, json.dumps({"name": display}))`; `pw_fill` types the visible text and sets hidden `selectedLocation` via `eval_on_selector`, skipping the hCaptcha-gated `/searchLocations` autocomplete entirely. The **exact blob schema is still an open leverdemo item** (§12) — the current `{"name": …}` shape is the minimal guess to validate against a real response; no pre-submit validator or 400-driven separate-token fallback ships yet.
- **`submit.py`** — pure **`classify_outcome(final_url, http_status, body)`**: `/thanks` → `SUCCESS`; otherwise parse the re-rendered body with `selectolax` → flagged required fields → `FAILED:MISSING_REQUIRED_FIELD:<f>`; else a populated `p.error-message` banner (excluding the resume-oversize banner) + no flagged field → `CAPTCHA_BLOCKED:hcaptcha_unverified`; `http_status >= 500` → `RETRYABLE_ERROR`; an unattributable re-render → `FAILED:no_thanks_redirect`. The **submit trigger itself lives in `app_pw._submit_with_captcha`** (silent-pass first): click Submit (human), poll ~3s for `h-captcha-response` to self-fill; only if it stays empty AND a solver key is set → `solve_hcaptcha` → inject the token → re-click. `SUBMIT_MODE` gates whether this terminal step runs at all.
- **`verify.py`** — best-effort: after `SUCCESS`, poll the mailbox (`imap-tools` in `asyncio.to_thread`, bounded ~120s/5s, `seen=False`, no mark-seen) for a `@hire.lever.co` message; **before visiting any link, assert host `== lever.co` / `*.lever.co`, https, no userinfo**; store `confirmation_email_url`. "No email" is expected — never fails the apply.

### 5.3 Captcha (`captcha/`)
- **Empirical reality (TESTED 2026-06 with live keys — see REPORT §4):** the third-party solvers are **unreliable for Lever's invisible Enterprise hCaptcha**. CapSolver has **dropped hCaptcha** (returns "service not supported"); 2Captcha **times out** on a proxyless, no-`rqdata` solve. The CapSolver→2Captcha fallback is **wired but empirically unavailable**; the load-bearing path is the **in-browser silent pass** (Lever's native `hcaptcha.execute()` self-filling `h-captcha-response`). A challenge that neither silent-passes nor solves is recorded `CAPTCHA_BLOCKED`.
- **`base.py`** — module-level `async solve_hcaptcha(page_url, ua, rqdata, capsolver_key, twocaptcha_key) -> tuple[str, str]`: try CapSolver, fall over to 2Captcha on any `RetryableError`/`PermanentError`, and return `(token, vendor)` so the caller can record `solver_used`. Per-vendor token-shape normalization lives **inside each implementer**. `_const.SITEKEY` is re-exported here.
- **`capsolver.py` (primary)** — async REST via a per-call `httpx.AsyncClient` (the official SDK is stale: no async + 60s cap): `createTask` (`HCaptchaTaskProxyless`, `websiteKey`, `isInvisible=True`, `userAgent`, `enterprisePayload.rqdata` if present) → poll `getTaskResult` (≤90s) → `solution.gRecaptchaResponse`; a `createTask` `errorId` raises `SolverAuthError`, the deadline raises `SolverTimeout`.
- **`twocaptcha.py` (fallback)** — `AsyncTwoCaptcha.hcaptcha(...)` → `result["code"]`.
- **Timing (token TTL ~120s, single-use):** the solver caps its poll at ~90s so `solve+POST < 120s`. `rqdata` is threaded through (`FormSpec.rqdata`, currently always `None` — `form.py` does not yet capture it) for the day a still-supporting provider needs it; whether Enterprise emits it is a leverdemo open item (§12). No `Solver` protocol / `NoopSolver` / `balance()`/`report()` ship — silent-pass-only is expressed by simply having no solver key set.

### 5.4 Answers (`answers/`)
- **`rules.py` (primary)** — normalize each card question, match to intent, emit an answer **constrained to that card's option text** (work-auth→Yes, sponsorship→No, salary→`expected_salary`, state→from location, relocate→No, EEO→profile/"decline", agreements→checked); returns answer + `unmapped`.
- **`llm.py` (optional)** — unmapped *required*, **non-sensitive** questions only (EEO/eligibility are skipped via `is_sensitive`, never invented): Anthropic SDK (`AsyncAnthropic`), model id from `Settings.llm_model` (default `claude-haiku-4-5-20251001`, env `JOOBLE_LLM_MODEL`), profile + question + allowed options → answer **`validate_choice`d ∈ options**. Enabled only if `JOOBLE_LLM_API_KEY` set.

### 5.5 Orchestration, evidence, CLI
- **`runner.py`** — `run_all` over the first `MAX_APPLIES` vacancies, **sequential with a log-normal inter-apply delay** (`sample_delay("inter_apply", …)`; **fresh browser context per apply** — `app_pw` opens a new `launch_playwright` each call); `run_one` wraps each under `asyncio.timeout(180s)` so a failure becomes a classified `ApplyResult` (PermanentError → FAILED, TimeoutError/other → RETRYABLE_ERROR) and never aborts the batch; `CancelledError` propagates. Writes `results.json` **incrementally** after each vacancy (each row carries an extra `result_string`).
- **`evidence.py`** — **`redact_html(html)` ONLY**: a regex blanks PII + the live token (`h-captcha-response`, `email`, `phone`, `eeo[*]` field values). It does **not** capture screenshots and there is **no HAR**. The actual evidence capture is **`app_pw._capture`**, which writes a full-page `page.screenshot()` and `redact_html(page.content())` per attempt; failures are suppressed (best-effort). Per attempt the files land at `output/<company>/<posting_id>/<label>.png` + `.html`, where `<label>` ∈ `{"dry-run", "unmapped", "final"}`.
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

Detection: `SUCCESS` = redirect to `…/thanks`; failure = a 400 re-render (flagged field → `FAILED`, else error banner → `CAPTCHA_BLOCKED`, else `FAILED:no_thanks_redirect`); `http_status >= 500` → `RETRYABLE_ERROR`. `DUPLICATE_SUSPECTED` is a **defined status literal reserved for the roadmap dedupe ledger** (§11) — the MVP ships no ledger, so it is not emitted yet (Lever merges silently and still returns `/thanks`).

---

## 7. Error handling & resilience

- **`errors.py` hierarchy:** `ApplyError` → `RetryableError` {`NetworkError`, `CloudflareChallenge`, `SolverTimeout`} vs `PermanentError` {`SolverAuthError`, `SchemaUnmappedError`, `PayloadTooLargeError`, `AutofillConflict`, `WebDriverLeak`}. The split exists so retry logic can target `RetryableError` only — never a bad API key. (`tenacity` is a declared dep reserved for that filter; the MVP's resilience is the per-vacancy timeout below, not yet a tenacity retry loop.)
- **Classify, don't crash** — `runner.run_one` wraps each vacancy: `PermanentError` → `FAILED`, `TimeoutError`/anything else → `RETRYABLE_ERROR`; every divergence becomes an `ApplyResult`, never aborts the batch. `app.apply_fn` re-raises attempt failures as `PermanentError` so the wrapper classifies them.
- **Timeout, not retry (MVP):** each vacancy runs under `asyncio.timeout(180s)` (`run_one`); fragile in-flow steps are best-effort under `contextlib.suppress`. Browser teardown is in `app_pw`'s `launch_playwright` `finally`. **`CancelledError` is never swallowed** (the catch-all is `except Exception`, not `BaseException`).
- **Idempotency** — the `(normalized_email, company, posting_id)` dedupe ledger is **roadmap** (§11), not in the MVP.

---

## 8. Tech stack & tooling

### Why browser automation, not raw requests
The submit POST is double-gated: Cloudflare fingerprints TLS/JA4 + HTTP/2 *before JS runs*, and Lever's invisible/passive hCaptcha scores a *live browser environment* a headless HTTP client can't emit. A real browser fixes both + the `__cf_bm` cookie + the JS cards.

### Engine — why patchright
**zendriver (raw CDP) was implemented first and rejected after testing.** It is the stealthiest *transport* (direct CDP, no Playwright/Selenium shim → no `webdriver`/`Runtime.enable` leak), but it cannot drive Lever's apply form: uploading the résumé triggers a client-side `parseResume` **re-render that destroys the JS execution context**, after which the raw-CDP `Runtime` calls (`Runtime.evaluate`, `Element.apply`→`callFunctionOn`) **hang (headless) or crash the renderer (headful — "Aw, Snap!" `STATUS_ACCESS_VIOLATION`)**. Worse, cancelling a hung CDP call corrupts the connection, so bounding/retry can't recover (proven: a 45s `asyncio.timeout` did **not** fire). This is the classic Puppeteer/Playwright *"Execution context was destroyed, most likely because of a navigation"* — but **patchright (a stealth Playwright fork) auto-waits through the re-render and throws-not-hangs**, so its `fill()`/`check()`/`select_option()` succeed where raw CDP cannot. Patchright also keeps `navigator.webdriver` genuinely false. Verified: the full dry-run runs end-to-end on the real leverdemo `/apply` page (résumé upload → human-filled standard fields → cards answered → `DRY_RUN_READY` + screenshot), reproducible headless in Docker. Other engines rejected earlier: undetected-chromedriver (unmaintained), playwright-stealth (loses to Cloudflare's protocol layer), Camoufox (heavier), rebrowser/selenium-driverless (≈ vanilla).

### Dependencies (verified PyPI, 2026-06)

| Tier | Package | Floor | Role |
|---|---|---|---|
| core | `patchright` | `>=1.60.1` | browser engine (stealth Playwright fork; auto-waits through Lever's parseResume re-render) |
| core | `httpx` | `>=0.28.1` | async CapSolver/2Captcha REST |
| core | `pydantic[email]` | `>=2.13` | models + `EmailStr` (needs `email-validator`) |
| core | `pydantic-settings` | `>=2.14.1` | typed config from `.env` |
| core | `selectolax` | `>=0.4.10` | fast, safe HTML parsing (form + 400 re-render + fixtures) |
| quality | `tenacity` | `>=9.1.4` | bounded/jittered/filtered async retries |
| quality | `structlog` | `>=26.1` | per-apply contextvars tracing |
| feature | `2captcha-python` | `>=2.0.7` | fallback solver (`from twocaptcha import AsyncTwoCaptcha`) |
| feature | `imap-tools` | `>=1.13` | confirmation-email evidence |
| feature | `anthropic` | `>=0.109` | optional LLM card-answer fallback |

**Dropped:** `numpy` (stdlib `math`/`random`), CapSolver SDK (REST via httpx), `curl_cffi` (raw-requests path rejected).

### Dev tooling
**uv** (with `pip install -e .` fallback so the grader isn't forced into uv); **ruff** (`>=0.15`, `ASYNC`/`I`/`UP`/`B` rules on); **basedpyright** (`>=1.39.7`, strict on `src/`, `reportMissingTypeStubs=false`); **pytest** (`>=9.0`) + **pytest-asyncio** (`>=1.4`, `asyncio_mode="auto"`) + pytest-cov (`>=7.0`). Dev deps live in `[dependency-groups]` (installed by plain `uv sync`). Python 3.12+, standard GIL build, single package + `src/` layout.

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
├── app.py (resolve_answers + run_command) · app_pw.py (single-vacancy patchright flow)
├── browser/ pw_engine.py · human.py
├── lever/   form.py · pw_fill.py · locations.py · submit.py · verify.py
├── captcha/ base.py · capsolver.py · twocaptcha.py · _const.py
├── answers/ rules.py · llm.py
├── evidence.py · runner.py
data/    profile.json · resume.pdf                         (git-ignored)
output/  results.json · <company>/<posting_id>/<label>.{png,html}   (git-ignored; <label> ∈ dry-run|unmapped|final)
tests/   unit/ · integration/ · fixtures/
docs/    ARCHITECTURE.md · REPORT.md
```

---

## 11. Production & X1000-scale roadmap

(Report material; not built.) `(candidate, posting)` queue + worker pool, one isolated profile each, throttled per IP/ASN. **Write-ahead resumability ledger** (INTENT_TO_SUBMIT before POST, OUTCOME after; `UNKNOWN_INFLIGHT` on crash → manual confirm, mailbox poll as tiebreaker — over-engineering for a supervised 5-apply run, essential at scale). Residential proxy pool (geo-matched, sticky-per-app), mobile reserve. Answer-mapping cache + LLM fallback with human-review queue. **Warmup parameter tuning** (dwell medians, scroll depth, warmth gate) correlated to silent-pass rate. Solver economics: silent-pass ⇒ most applies $0; at challenge-rate `c`, 1000 applies ≈ `c·1000·$0.0008`; proxy GB dominates. Logfire span-tree; dedupe ledger; quarantine bad identities/proxies. Confirm authorization + respect Lever/hCaptcha ToS before scaling.

---

## 12. Open risks — resolve on leverdemo

1. **Solver fallback is empirically unreliable (TESTED 2026-06-10, live keys):** CapSolver has **dropped hCaptcha** (`ERROR_INVALID_TASK_DATA: not supported`, all task types); 2Captcha **timed out** (110s) on a proxyless, no-`rqdata` solve — consistent with Lever = Enterprise hCaptcha (`secure-api.js`). So the paid-solver fallback is effectively unavailable for Lever today; the **in-browser silent pass is the load-bearing path** (see REPORT §4). `rqdata` capture (§5.3) only helps if a still-supporting provider is found; otherwise rely on the real-browser native `hcaptcha.execute()`.
2. **Silent-pass rate** for clean-IP + headful + warmed sessions — the load-bearing KPI; measured as the first smoke metric.
3. **`selectedLocation` exact schema** — capture from a real `/searchLocations` response; validate the injected blob.
4. **Does the authenticated POST re-fingerprint harder than the GET?** Test a `leverdemo` submission.
5. **Card-schema variance** across the 5 real postings (only Aledade inspected) — fail-closed handling makes this a clean classified outcome, not a crash, but sizes the rules-vs-LLM split.
6. **Token timing** — confirm ~120s TTL/single-use empirically; keep solve≤90s.
7. **Chrome version drift** — the pre-flight's supported range vs an auto-updated Chrome; a launch failure raises a clear error → classified `RETRYABLE_ERROR` (no silent dead path).
8. **Legal/ToS** — authorization before any real-mode/scaled use.
