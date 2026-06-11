# ApplyMe — Lever Auto-Apply: Report

> **DRAFT** — the required short report. Sections 1, 2, 3, 4, 5 are written from verified research and live testing; the §0 result summary and the per-vacancy table are filled after the full 5-apply submit run. Deeper detail: [`ARCHITECTURE.md`](ARCHITECTURE.md).

## 0. Result summary _(filled after the run)_

| # | Company | Posting | Status | Reason | Captcha | Evidence |
|---|---|---|---|---|---|---|
| 1 | aledade | Staff AI Researcher | _TBD_ | | | |
| 2 | raptv | _TBD_ | _TBD_ | | | |
| 3 | padsplit | _TBD_ | _TBD_ | | | |
| 4 | skillerszone | _TBD_ | _TBD_ | | | |
| 5 | theathletic | _TBD_ | _TBD_ | | | |

Statuses: `SUCCESS` (reached `/thanks`) · `FAILED:<reason>` · `CAPTCHA_BLOCKED`. Each row carries the brief-literal `result_string` (`success` / `failed:<reason>` / `captcha blocked`). Per-attempt evidence is a full-page screenshot (`output/<company>/<posting_id>/<label>.png`, labels `dry-run`/`unmapped`/`final`) and the matching redacted HTML snapshot (`<label>.html`), plus one `ApplyResult` per vacancy in `output/results.json` (written incrementally). **Committed evidence is generated on the `leverdemo` sandbox; real-posting runs are opt-in.**

### Task coverage (where each requirement is answered)

| Task requirement | Where |
|---|---|
| Open 5 vacancies + full Apply (form / resume / submit) | §1, §3 + code |
| Per-vacancy result `success` / `failed:…` / `captcha blocked` | §0 table (`result_string`) |
| CAPTCHA handled + approach explained | §2 |
| Human behavior — randomized delays, non-linear mouse, no headless/UA/WebDriver | §3a |
| Tech stack justified (free choice) | §1a |
| What requests the frontend makes | §3 |
| What didn't work + why | §4 |
| Production-level + X1000 scale | §5 |

---

## 1. Which Apply approach I chose, and why

**Browser automation** (a stealth real-Chrome session via **patchright**, a Playwright fork) driving the real `/apply` form — **not** raw network requests.

The Lever submit *looks* like a plain `multipart/form-data` POST to `/<company>/<id>/apply`, which tempts a pure `requests` implementation. But the POST is double-gated:

1. **Cloudflare** fingerprints the TLS/JA4 handshake + HTTP/2 settings *before any JS runs*. A realistic-UA `curl` passes the GET, but the authenticated POST is the scrutinized path where a non-browser client is flagged.
2. **Invisible/passive hCaptcha** scores a *live browser environment* (canvas, WebGL, mouse, timing, IP). There is no image puzzle to outsource; a headless HTTP client emits none of those signals and never reaches a passing score.

A real browser fixes all of it at once — the TLS fingerprint, the `__cf_bm` cookie (via session warming), the in-browser behavioral signals hCaptcha needs, and the JS-rendered per-posting custom questions. patchright specifically because it launches the **system Chrome** (`executable_path`, no separate browser download) with the automation signals patched out (`navigator.webdriver` genuinely false), and — unlike a raw-CDP driver — it **auto-waits through Lever's reactive re-renders** instead of hanging on them (the finding that drove this choice: §4). We do **not** spoof the UA — the real browser's fingerprint is the stealthiest. (Rejected alternatives, the zendriver finding, and full reasoning: §4 and [`ARCHITECTURE.md#stack-rationale`](ARCHITECTURE.md#stack-rationale).)

## 1a. Why this stack (free choice — justified)

The brief left the stack open and asks for justification. Reasoning, layer by layer (full why-not matrix in [`ARCHITECTURE.md`](ARCHITECTURE.md#engine--why-patchright)):

- **Language — Python.** The two decisive libraries (`patchright`; the captcha REST client) are Python-first and the profile/answer-mapping tooling fits naturally. Node's stealth ecosystem is decayed (`puppeteer-extra-stealth` unmaintained since 2023).
- **Engine — patchright (stealth Playwright fork).** A Playwright fork with the automation tells patched out (`navigator.webdriver` genuinely false, no `Runtime.enable`/CDP-bridge leak) that drives the **system Chrome** via `executable_path` — and crucially **tracks the JS execution-context lifecycle and auto-waits through navigations/re-renders**, so its `fill`/`check`/`select_option` survive Lever's parseResume re-render. We started on **zendriver** (direct-CDP) for its even-cleaner transport, but **rejected it after testing** because its raw `Runtime` calls hang/crash on exactly that re-render (the §4 finding). **Also rejected:** undetected-chromedriver (unmaintained), playwright-stealth (loses to Cloudflare's protocol layer), Camoufox (no CDP, heavier), Selenium (protocol baggage). patchright is also permissively licensed (zendriver is AGPL-3.0).
- **Captcha — in-browser silent-pass first; CapSolver→2Captcha only on a real challenge.** Avoiding the challenge is free and most reliable; a solver as the *foundation* is risky (Enterprise `rqdata` + a degraded 2026 solver market). Detail in §2.
- **Human behavior — our own Bézier + log-normal layer (stdlib).** Playwright/patchright's built-in mouse move is straight-line and `python-ghost-cursor` is dead, so the curved path is our code (dispatched via `page.mouse`). Detail in §3a.
- **Why a browser at all, not raw requests** — §1: the POST is double-gated by Cloudflare TLS/JA4 fingerprinting *and* a behavioral hCaptcha that a headless HTTP client cannot satisfy.
- **Lean by design** — stdlib over `numpy`; CapSolver via REST (its SDK is stale); no DB/queue/proxies for a 5-apply MVP (those are §5 scale concerns). It's a *script*, not a platform.

## 1b. Build vs. adopt — existing tools considered

I checked whether an off-the-shelf tool or API already solves this before building (verified June 2026):

- **No sanctioned applicant API.** Lever's official "Apply to a Posting" `POST .../postings/SITE/ID?key=` *is* captcha-free and supports cards — but `?key` is the **employer's** Super-Admin / OAuth-partner key, unobtainable for five unrelated companies. Greenhouse (employer Basic-Auth) and Workday (tenant OAuth) are identical: public read, **credentialed write**. Driving the candidate-facing form is the only route for an external applicant.
- **No adoptable tool.** The auto-appliers are LinkedIn-Easy-Apply-focused closed SaaS / extensions (Simplify, Sonara, LazyApply, JobRight); the most-starred OSS (AIHawk) was **archived 2026-05**, LinkedIn-only, AGPL. The OSS projects that *do* target Lever (`neonwatty/job-apply-plugin`, `simonfong6/auto-apply`) drive the real form, draft answers with an LLM, and **stop before submit** — the same shape as this design — and none solves hCaptcha.
- **LLM browser-agents don't solve the hard part.** browser-use / Stagehand / Skyvern / Steel gate stealth + CAPTCHA behind a **paid cloud routed through proxies** — the opposite of the clean-local-IP that makes silent-pass viable; their "native CAPTCHA solving" means standard hCaptcha, not invisible *Enterprise*. The shipping products that genuinely submit (e.g. FastApply) "handle" the captcha by **surfacing it to a human or skipping** — the decisive tell that the wall is real for everyone.

**Conclusion: build.** The surface is narrow (one ATS, 5 postings) and the gradable value is exactly the engineering a black box would hide. FastApply (closest prior art) independently chose the same browser-automation architecture, which validates it.

## 2. Which captcha approach I chose, and why

**In-browser silent-pass first, third-party solver as fallback** — a combination.

Lever uses **invisible hCaptcha** (sitekey `e33f87f8-88ec-4e1a-9a13-df9bbb1d8120`, field `h-captcha-response`, loaded via `js.hcaptcha.com/1/secure-api.js`). Invisible hCaptcha targets <0.1% challenge rate for human-looking sessions, so a clean IP + coherent fingerprint + warmed session + human mouse/timing usually self-solves **for free** in-browser. That free silent-pass rate is the real KPI.

A paid solver (**CapSolver** primary, **2Captcha** fallback, behind one `solve({sitekey, pageurl, isInvisible, rqdata?, proxy?})` interface) is called **only when an interactive challenge actually fires**. It is a fallback, not the foundation, for two honest reasons: the `secure-api.js` endpoint signals hCaptcha **Enterprise**, which likely requires a fresh per-challenge `rqdata` blob (out-of-band proxyless tokens get rejected); and the 2026 enterprise-hCaptcha solver market is degraded after 2024 legal enforcement. So the design leans on avoiding the challenge and treats solving as insurance — recording `captcha_blocked` honestly when it can't be solved.

**Honest framing — silent-pass is best-effort, not a solved problem.** hCaptcha hardened invisible/Enterprise detection against AI agents in 2026, and the solver fallback is empirically unreliable for this exact case (§4). When a challenge renders and can't be solved, the attempt is recorded `captcha_blocked` — which is precisely how every shipping competitor behaves (FastApply *surfaces the captcha to a human* in co-pilot mode or *skips* in auto-pilot; none silently solves invisible Enterprise hCaptcha). At scale the correct design adds an explicit **human-in-the-loop hand-off** for challenged sessions rather than implying robust automated solving.

## 3. What requests the frontend makes (form fill + submit)

Verified by direct inspection of the live page JS and confirmed against the real `leverdemo` `/apply` page during the dry-run (résumé upload fires `parseResume`; the rest is read off the live DOM):

- **`GET /<company>/<postingId>/apply`** — loads the form. Cloudflare sets `__cf_bm`. The page loads `js.hcaptcha.com/1/secure-api.js` and renders an invisible hCaptcha.
- **`POST /parseResume`** (multipart: `resume` + `accountId`) — fired on resume upload; returns profile JSON (`{name,email,phone,position,location,links,resumeStorageId,…}`) used to autofill fields and stamp `resumeStorageId`. The resume file is **still re-sent** in the final submit.
- **`GET /searchLocations?text=<q>&hcaptchaResponse=<token>`** — location autocomplete; **hCaptcha-gated** (403 without a token). Automation can skip it and set `selectedLocation` directly.
- **Final submit** — there is **no XHR/JSON submit**. `application.js` is UI glue only. An inline script gates submission behind `hcaptcha.execute()`: on token it writes `h-captcha-response` and clicks a hidden submit button, firing a **native full-page `multipart/form-data` POST to `/<company>/<postingId>/apply`** (the `<form>` has no `action`). The body carries `resume` (file), `name`, `email`, `phone`, `location`/`selectedLocation`, `org`, `urls[…]`, `eeo[…]`, all hidden fields (`accountId`, `resumeStorageId`, `timezone`, `source`, …), the custom-question `cards[<id>][field0..N]` answers (submitted as option **text**), and `h-captcha-response`.
- **Outcome:** **SUCCESS** = redirect to `GET /<company>/<postingId>/thanks` (200). **FAILURE** = HTTP 400 re-rendering the form with `p.error-message` "There was an error verifying your application." (same message for bad-captcha and missing-required-field — distinguished by re-scraping flagged fields).

## 3a. Human-behavior simulation (the anti-bot requirements)

The brief requires randomized delays, human-like / non-straight-line mouse, and no headless / missing-UA / WebDriver signals. How each is met:

- **Randomized, non-fixed delays** — every action waits a **log-normal** sample (`exp(N(log median, σ))`, distinct per class: keystroke / field-think / read / pre-submit), never `sleep()` or uniform jitter (uniform timing is itself a tell). Real wall-clock `asyncio.sleep` (hCaptcha scores genuine spacing). The RNG seed is logged for replay.
- **Human-like / non-straight-line mouse** — our own **Bézier** paths (curved, non-constant velocity, overshoot-and-correct, click a random point *inside* the element) stepped through `page.mouse.move`. Playwright/patchright's built-in mouse move is a straight line and `python-ghost-cursor` is dead — so the curve is our code.
- **No headless / UA / WebDriver signals** — real **system Chrome, headful** (launched via `executable_path`, no separate browser download), so `navigator.webdriver` is *genuinely* false (patchright patches the automation tells, not shimmed); the UA and fingerprint are the real browser's (we don't spoof — a mismatch is the tell). A startup guard (`assert_no_webdriver_leak`) asserts `navigator.webdriver` is falsy before any field is touched.
- **Plus session warming** (homepage → dwell/scroll → posting before `/apply`) and a minutes-scale inter-apply delay, so Cloudflare's `__cf_bm` bot-score reflects a natural session.

## 4. What didn't work, and why

**The original engine (zendriver, raw CDP) — rejected after live testing. This is the load-bearing finding.**

zendriver was implemented first: it drives Chrome over raw CDP with the cleanest possible transport (no Playwright/Selenium shim), which is exactly why it was the initial pick. It works fine until the résumé upload — and then Lever breaks it:

- Uploading the résumé fires a client-side **`parseResume` re-render** that **destroys the page's JS execution context**. Every subsequent raw-CDP `Runtime` call (`Runtime.evaluate`, and the `Element.apply` → `Runtime.callFunctionOn` used to read/set fields) then either **hangs indefinitely** (headless) or **crashes the renderer** (headful: the "Aw, Snap!" page, `STATUS_ACCESS_VIOLATION`). This is the classic Puppeteer/Playwright *"Execution context was destroyed, most likely because of a navigation"* — but at the raw-CDP layer there is no auto-wait to absorb it.
- Worse, it is **unrecoverable, not just slow**: a hung CDP call **corrupts the connection**, so bounding it with a timeout doesn't help — the surrounding `asyncio.timeout(45)` did *not* fire, and retry/backoff can't re-establish a usable session. You can't engineer around a wedged transport.
- **The fix was to swap the engine, not patch around it.** **patchright** (a stealth Playwright fork) tracks the execution-context lifecycle and **auto-waits through the re-render**, and where it can't proceed it **throws rather than hangs** — so a bounded retry actually works. Its `fill()` / `check()` / `select_option()` succeed on the exact form where raw CDP wedges. patchright keeps the properties that motivated zendriver (`navigator.webdriver` genuinely false; system Chrome via `executable_path`; no UA spoofing) while surviving Lever's reactivity.
- **Verified after the swap:** the full **dry-run runs end-to-end on the real `leverdemo` `/apply` page** — résumé upload → human-filled standard fields → cards answered → `DRY_RUN_READY` with a screenshot — with `navigator.webdriver` false, reproducibly **headless in Docker**. The whole zendriver apply path (`browser/engine.py`, `browser/actions.py`, `browser/warmup.py`, `lever/fill.py`) and the dependency were removed; the engine-agnostic pieces (form parser, answer engine, human-behaviour math, captcha/submit/verify) carried over unchanged.

**Live-tested the captcha solvers (2026-06-10, real API keys) — both failed for Lever's hCaptcha:**

- **CapSolver has dropped hCaptcha support.** Every task type (`HCaptchaTaskProxyless`, `HCaptchaTask`, `HCaptchaTurboTaskProxyless`) returns `ERROR_INVALID_TASK_DATA: "This service is not supported."` The account is healthy (balance OK, key valid for other captcha types). This matches 2026 reality: after hCaptcha's legal pressure, the major solvers pulled their public hCaptcha endpoints.
- **2Captcha timed out** — no token after 110s on a proxyless solve without `rqdata`. Consistent with Lever loading hCaptcha via `secure-api.js` (**Enterprise**), which gates token validity on a fresh per-challenge `rqdata` blob a proxyless, out-of-band solve cannot supply.

**Consequence (anticipated by the design):** the third-party-solver fallback is **empirically unreliable for Lever's Enterprise hCaptcha in 2026** — exactly the build-time risk flagged in §8/§12. The bot therefore leans on the **in-browser silent pass**: a real, clean, headful Chrome lets `hcaptcha.execute()` mint the token natively (the page supplies its own `rqdata`), which is the high-success path for a clean IP + coherent fingerprint + warmed session. The code degrades correctly — CapSolver error → 2Captcha attempt → `CAPTCHA_BLOCKED` recorded honestly if both fail.

**What worked in testing:** the Anthropic LLM fallback (key + `claude-haiku-4-5-20251001` returns valid, option-constrained answers); the full unit/logic suite; the solver failover plumbing; and — verified end-to-end against a **live** Lever page in a headless-Chromium Docker container — the patchright engine genuinely **drives Chrome through Cloudflare with `navigator.webdriver` false (no leak)** (`scripts/check_chrome.py`), the form parser extracts the exact invisible-hCaptcha sitekey (`e33f87f8-…`), 6 standard fields, and the 1 custom card off the real DOM, and the **full dry-run reaches `DRY_RUN_READY` on the real `leverdemo` `/apply` page** — résumé upload → human-filled standard fields → cards answered → screenshot, stopping before the POST. The browser integration tests (`pytest -m integration`) also pass against real Chromium in the container.

**Answer engine — rules-first → LLM-fallback → option-validated, fail-closed.** Deterministic rules map the structured/high-stakes questions (work-auth, sponsorship, relocation, salary, EEO→decline, consent) to each card's option *text*; the LLM (Haiku) fires only on still-unmapped free-form questions, with its output hard-constrained to the allowed options (`validate_choice`, tolerant of a verbose reply), and the attempt **fails closed** (`FORM_SCHEMA_UNMAPPED`) when neither yields a valid in-options answer rather than guessing. For integrity — a fabricated profile applying to *real* companies — the LLM is **never** consulted for legal/EEO/eligibility questions (visa, citizenship, clearance, ITAR, protected-class, criminal-history); those are answered only from profile facts via the rules engine or left unmapped. This matches the mature commercial pattern (deterministic-first; LLM only for novel free-text) and is more conservative than the LLM-fills-everything bots. _An early live run (on the original zendriver engine, against the real Aledade posting) surfaced — and we fixed — three real-browser bugs the unit fakes had masked: the override typing appended over Lever's `parseResume` autofill, a `<select>` set via the wrong CDP call, and rules substring-collisions ("statements" matching the state branch). The first two are now handled natively by patchright's auto-waiting `fill`/`select_option` (clear-then-type, label-based select); the rules fix carried over unchanged._

**Still to confirm on a full submit run (the dry-run proves launch → parse → fill → evidence on `leverdemo`, not submission):** the in-browser silent-pass rate on the 5 postings (the load-bearing KPI — the dry-run reaches `DRY_RUN_READY` but stops before triggering `hcaptcha.execute()` or the POST); whether any posting escalates to an interactive challenge (→ `captcha blocked`, given the solver state); and whether the data mismatch (resume = pipeline engineer vs profile = Product Manager) triggers any field-level rejection. The headless Docker path already runs the dry-run; the headful 5-apply *submit* run happens on the operator's machine (or the headless Docker container with a submit-mode flag).

## 5. What's needed for production-level + X1000 scale

- **Concurrency & isolation:** a `(candidate, posting)` job queue + a pool of browser workers, each owning one isolated profile (fingerprint + cookie jar + proxy); throttle a few applies/min/IP; sticky session per application, rotate IP between.
- **Proxies:** residential pool (geo-matched, sticky-per-app), mobile reserve for repeatedly-challenged postings; score and auto-retire burned IPs. (Datacenter IPs are heavily challenged — avoided.)
- **Answer mapping:** deterministic profile→card mapping with a cache keyed by `(company, question, options-hash)`, plus an **LLM fallback** for novel required questions constrained to the card's option text, with a human-review queue for low confidence. This — not the captcha — is the real scaling complexity.
- **Generalize across ATSs:** the hand-coded Lever selectors + cards parser are brittle to HTML/schema drift — the honest cost of hand-rolling for one ATS. To productionize across many ATSs, evaluate an LLM browser-agent that absorbs layout changes; **Stagehand v3** (CDP-direct, mixes deterministic Playwright with AI `act`/`extract`) is the best-designed path — with the caveat that even then Lever's invisible Enterprise hCaptcha stays unproven and still hinges on silent-pass + a human hand-off.
- **Captcha economics:** silent-pass means most applies cost $0; budget = (measured challenge rate) × volume × ~$0.0008. Proxy GB dominates cost, not solving. Plan for an escalating challenge rate and possibly-unreliable Enterprise solving.
- **Confirmation email:** Lever sends a post-submit confirmation (from `@hire.lever.co`, suppressible per tenant) — there is **no blocking click-to-verify step**; success is the `/thanks` redirect. A per-identity mailbox (catch-all + IMAP) captures it as best-effort evidence.
- **Observability & recovery:** Logfire span-tree per apply (warm→fill→solve→submit→verify) with dashboards on silent-pass rate, challenge rate, solver acceptance, cost/apply; classify outcomes, retry retryables on fresh proxies, quarantine bad identities/proxies; dedupe ledger by `(email, company, posting_id)`.
- **Legal/ToS:** confirm authorization; respect Lever/employer and hCaptcha terms; keep volume and targeting responsible.
