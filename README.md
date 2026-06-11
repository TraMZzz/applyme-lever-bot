# ApplyMe — Lever Auto-Apply

Automated job-application bot for **[jobs.lever.co](https://jobs.lever.co)**. Given a candidate profile + resume, it opens a list of Lever postings, completes each Apply flow (fills the form, uploads the resume, handles the invisible hCaptcha, submits), and records a structured outcome per vacancy.

Built as an engineering take-home: an auto-apply bot for Lever.

> **Status: implemented and working end-to-end.** The full apply pipeline (parse → fill → captcha → submit → evidence) is working code. A headful submit **silent-passes the invisible Enterprise hCaptcha and succeeds** on both Lever's `leverdemo` sandbox and a **real** posting (skillerszone → "Application submitted!"). Across the 5 real postings: 1 `SUCCESS`, 3 `DUPLICATE_SUSPECTED` (already applied), 1 `FAILED` fail-closed (padsplit — the bot declines to fabricate answers to an ill-fitting required survey). Architecture: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md); deliverable report: [`docs/REPORT.md`](docs/REPORT.md).

---

## The task (summary)

Write a script that auto-submits applications to **5 Lever postings** on behalf of a provided candidate, and:

- complete the Apply flow per posting (form + resume upload + submit),
- record the result per vacancy: `success` / `failed:<reason>` / `captcha_blocked`,
- **handle Lever's CAPTCHA** (solver / browser stealth / combination — justified),
- **simulate human behavior**: randomized (non-fixed) delays, human-like / non-straight-line mouse, no headless / missing-UA / WebDriver signals,
- **justify the tech stack**,
- deliver: working code, screenshots/video of the 5 attempts, and a short report (apply approach · captcha approach · what requests the frontend makes · what failed · what's needed for production + X1000 scale).

Inputs (candidate profile JSON, resume PDF, target URLs) are supplied **locally** and are git-ignored — they are not committed to the deliverable. Place them under `data/` (see `.env.example`).

---

## Approach at a glance

Drive the **real `/apply` form** through a **stealth Playwright (patchright) browser** whose session is hardened to push Lever's _invisible_ Enterprise hCaptcha toward a silent self-pass (the token is a passive risk score — there's no puzzle to solve and no out-of-band token it will accept; §4). This single decision fixes the four things that defeat a raw-HTTP approach at once: the Cloudflare TLS/JA4 fingerprint, the `__cf_bm` session cookie, the in-browser behavioral signals hCaptcha scores, and the JS-rendered per-posting custom questions. **Measured result (§4): on `leverdemo` the hardened headful session silent-passes the invisible hCaptcha and submits (`SUCCESS`), reproducibly** — the long-running "captcha blocked" turned out to be a submit-button bug, not the captcha. When the session's score is too low (stricter tenant, dirty IP) or a challenge renders, the bot **fails closed** with a truthful `captcha_blocked` — the other half of the brief's *"pass **or** correctly handle."*

| Layer | Choice | One-line why |
|---|---|---|
| Language | **Python** | best-in-class browser tools are Python-first; Node's stealth ecosystem is decayed |
| Browser engine | **patchright** (stealth Playwright fork) | `navigator.webdriver` genuinely false **and** it auto-waits through Lever's `parseResume` re-render — which hangs/crashes a raw-CDP driver (see below) |
| Engine note | zendriver (raw CDP) **rejected after testing** | stealthiest transport, but its `Runtime.evaluate`/`callFunctionOn` **hang (headless) or crash the renderer (headful)** on Lever's reactive re-render, and a hung-call cancellation corrupts the connection — unrecoverable. Documented in [`REPORT`](docs/REPORT.md) §4. |
| Captcha | **in-browser silent-pass, hardened** | the token _is_ a passive risk score (§4); we drive it down (persistent profile, fingerprint coherence, IP pre-flight, behavioural telemetry) and **measured: it silent-passes on `leverdemo` → `SUCCESS`**; fails closed (`captcha_blocked`) when the score is too low |
| Solver | **disabled / fail-closed** | CapSolver delisted hCaptcha; out-of-band tokens are score-rejected (§4). Wired but never fires a doomed request — records `captcha_blocked` honestly |
| Human behavior | **log-normal delays** + **own stdlib Bézier mouse** | non-fixed timing + curved/in-element-random clicks + scroll, dispatched via Playwright's `page.mouse`; per-character typing |
| Proxies | clean home IP (pre-flighted) · sticky mobile reserve | IP/ASN reputation is the deciding variable; a _rotating_ residential pool **raises** the score — avoided (§4) |
| Models / output | **Pydantic** + `results.json` + screenshots | lean for a "script"; DB/queue are scale concerns, not built |

Full justification incl. **why each alternative was rejected**: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#stack-rationale).

> **Verified:** the full dry-run runs end-to-end on the real `leverdemo` apply page (résumé upload → human-filled fields → cards answered → `DRY_RUN_READY` + screenshot), `navigator.webdriver=false`. Reproducible headless via [Docker](#docker-reproducible-browser-run).

**Why build it, not adopt a tool (verified June 2026):** there is no sanctioned applicant API (Lever/Greenhouse/Workday submit endpoints are *employer*-credentialed) and no off-the-shelf bot that does unattended Lever submit — the auto-appliers are LinkedIn-focused SaaS, the most-starred OSS (AIHawk) is archived/LinkedIn-only, and the LLM browser-agents (browser-use, Stagehand, Skyvern) gate stealth+CAPTCHA behind a paid cloud and don't defeat invisible Enterprise hCaptcha. Driving the form is the only route, and the products that *do* submit "handle" the captcha by surfacing it to a human or skipping. Build-vs-adopt analysis: [`docs/REPORT.md`](docs/REPORT.md) §1b.

**Custom-question answers** use a rules-first → LLM-fallback → option-validated, **fail-closed** engine: deterministic mapping for structured/high-stakes fields, the LLM (Haiku) only for novel free-form questions (output constrained to the allowed options), and `FORM_SCHEMA_UNMAPPED` rather than a guess when neither resolves. The LLM is **never** consulted for legal/EEO/eligibility questions — those come from profile facts or fail closed (integrity: a synthetic profile applying to real companies).

---

## How it works (high level)

```
load profile + resume
      │
      ▼
for each vacancy:
   warm session (homepage → dwell → posting)         # raises Cloudflare trust
      │
      ▼
   open /apply, parse form + custom "cards" JSON
      │
      ▼
   map profile → custom-question answers (rules; LLM fallback)
   upload resume                                     # triggers parseResume autofill
   override standard fields (human-like timing + mouse)  # after upload, so our values win
   answer custom "cards"
      │
      ▼
   click Lever's real submit button → hcaptcha.execute() (invisible Enterprise):
      ├─ silent pass (clean headful session)    → POST accepted
      └─ score too low / challenge renders       → solver FAILS CLOSED (dead market) → CAPTCHA_BLOCKED
      │
      ▼
   detect outcome (post-submit navigation):
      ├─ /…/thanks  OR  …?LeverAppId=<uuid>      → SUCCESS   (leverdemo has no /thanks page)
      └─ 400 re-render with a real error banner  → FAILED / CAPTCHA_BLOCKED
      │
      ▼
   on SUCCESS: capture Lever confirmation email as evidence (best-effort; not a gate)
      │
      ▼
   capture screenshot + redacted HTML → output/<company>/<id>/<label>.png+.html, append ApplyResult
      │
      ▼
write output/results.json  (one ApplyResult per vacancy, incrementally)
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the exact form fields, endpoints, captcha config, and success/failure signals.

---

## Project structure

```
.
├── README.md
├── pyproject.toml
├── Dockerfile / .dockerignore  # headless-Chromium image (reproducible browser run / CI)
├── .env.example                # keys (LLM/IPQS/solver), IMAP mailbox, SUBMIT_MODE, stealth tuning (profile/proxy/tz)
├── src/applyme/
│   ├── __main__.py / cli.py     # entrypoint:  applyme run …
│   ├── config.py                # settings (pydantic-settings), submit-mode flag
│   ├── models.py                # CandidateProfile, Vacancy, ApplyResult (pydantic)
│   ├── profile_loader.py        # parse profile.json + fetch/locate resume PDF
│   ├── errors.py                # exception hierarchy (Retryable/Permanent, WebDriverLeak)
│   ├── app.py                   # resolve_answers (rules + guarded LLM) + run_command (orchestrate all vacancies)
│   ├── app_pw.py                # single-vacancy flow: warm → /apply → parse → resolve → fill → dry-run / submit;
│   │                            #   owns evidence capture (page.screenshot + page.content)
│   ├── browser/
│   │   ├── pw_engine.py         # patchright launch_persistent_context (real Chrome, no_viewport, locale/tz, proxy), webdriver-leak guard
│   │   ├── preflight.py         # egress-IP reputation check (IPQualityScore) — used by the fingerprint_check diagnostic
│   │   └── human.py             # log-normal delays, stdlib-Bézier mouse + scroll (via page.mouse)
│   ├── lever/
│   │   ├── form.py              # parse apply form + cards/baseTemplate JSON
│   │   ├── pw_fill.py           # résumé upload FIRST → human-override standard fields → cards (auto-waiting check/select_option/fill)
│   │   ├── locations.py         # selectedLocation handling
│   │   ├── submit.py            # submit + outcome detection (/thanks vs 400)
│   │   └── verify.py            # best-effort: capture Lever confirmation email (evidence, not a gate)
│   ├── captcha/
│   │   ├── base.py              # solve_hcaptcha({sitekey,pageurl,isInvisible,rqdata?,proxy?}) + vendor normalize
│   │   ├── _const.py            # shared sitekey / endpoint constants
│   │   ├── capsolver.py
│   │   └── twocaptcha.py
│   ├── answers/
│   │   ├── rules.py             # deterministic profile → card-answer mapping
│   │   └── llm.py               # optional LLM fallback for unmapped questions
│   ├── evidence.py              # redact_html() — blank sensitive values before an HTML snapshot is written
│   │                            #   (the screenshot+HTML capture itself lives in app_pw._capture)
│   └── runner.py                # orchestrate the N vacancies, collect results
├── inputs/                      # raw provided: profile.md, resume.md, vacancies.md, test.pdf (gitignored)
├── scripts/
│   ├── prepare_inputs.py        # inputs/ → data/
│   ├── check_chrome.py          # smoke: drive Chrome on a live Lever page (no data/ or keys)
│   ├── fingerprint_check.py     # silent-pass readiness gate: score the session (CreepJS/incolumitas/IPQS/WebRTC) for free
│   ├── record_motion.py         # record real human mouse/scroll/keystroke motion → data/motion/human_traces.json
│   └── check_motion.py          # smoke: prove the motion engine drives real Chrome with human-shaped events
├── examples/                    # synthetic profile.example.json + vacancies.example.txt + resume.example.pdf (run from a clone)
├── screenshots/                 # the 6 committed run shots (5 real-posting outcomes + leverdemo submit) — the brief's evidence
├── data/                        # generated: profile.json, resume.pdf, vacancies.txt (gitignored)
├── output/                      # results.json + per-attempt <company>/<id>/<label>.png+.html  (gitignored)
├── docs/  ├── ARCHITECTURE.md  └── REPORT.md
└── tests/  # unit + integration; fixtures/ holds captured HTML
```

---

## Inputs & email

The bot reads three things, all **git-ignored** (supply them under `data/`):

| File | What | Source |
|---|---|---|
| `data/profile.json` | candidate profile (→ the `CandidateProfile` model) | generated from `inputs/profile.md` |
| `data/resume.pdf` | the résumé to upload | downloaded from the profile's `resume_url` |
| `data/vacancies.txt` | Lever URLs, one per line | from `inputs/vacancies.md` |

**From a fresh clone (no provided inputs)** — synthetic examples ship in [`examples/`](examples/) so the bot runs immediately:

```bash
mkdir -p data
cp examples/profile.example.json  data/profile.json
cp examples/vacancies.example.txt data/vacancies.txt
cp examples/resume.example.pdf    data/resume.pdf       # replace with a real résumé for a meaningful upload
```

**With the original take-home material** — ApplyMe provides the raw `.md` in **`inputs/`** (and the résumé as a *URL*), so a one-shot prep step bridges `inputs/` → `data/`:

```bash
uv run python scripts/prepare_inputs.py
# profile.md → data/profile.json · downloads the résumé → data/resume.pdf · vacancies.md → data/vacancies.txt
```

**Email — optional, and you do _not_ need to control it.** The `email` in `data/profile.json` is submitted to Lever as the applicant's contact. Lever has **no click-to-verify gate** (success = the `/thanks` redirect), so the application goes through regardless of whether you can read that inbox. A mailbox is used *only* when you set `JOOBLE_IMAP_*`, and then *only* to capture the post-submit confirmation email as **best-effort evidence** — it never blocks a result. So either:

- leave the provided email as-is and **don't** set `JOOBLE_IMAP_*` → the bot skips the confirmation poll (you just won't get a confirmation screenshot); **or**
- point `email` at a mailbox you control (a free Gmail with an **App Password**, or a catch-all domain) and set `JOOBLE_IMAP_*` → the bot additionally captures the confirmation email.

## Setup

```bash
# Python 3.12+. uv recommended, but NOT required for the grader:
uv sync                       # installs runtime + dev tooling (ruff / basedpyright / pytest)
#   …or with pip:  pip install -e .       (runtime only — for the lint/type/test commands also run:
#                  pip install ruff basedpyright pytest pytest-asyncio)
uv run applyme run --help     # patchright drives your system Chrome (no separate browser download)

cp .env.example .env          # ALL keys are OPTIONAL — a dry-run needs none (see "Keys" below)

# Verify the engine actually drives Chrome — loads a live Lever page through Cloudflare, asserts
# there's no `navigator.webdriver` leak, and parses the form. Needs nothing but Chrome (no data/, no keys):
uv run python scripts/check_chrome.py
# → OK | …/leverdemo/…/apply | navigator.webdriver=false | sitekey=e33f87f8-… | standard_fields=6 | cards=1

# Then score silent-pass readiness for FREE (no Lever attempt burned) — gate the session before submitting.
# Scores the egress IP + drives CreepJS / incolumitas / sannysoft / WebRTC, screenshotting each to output/fpcheck/.
uv run python scripts/fingerprint_check.py     # set JOOBLE_IPQS_API_KEY for the IP-reputation axis (optional)
```

> **No local Chrome / headless box / CI?** Use [Docker](#docker-reproducible-browser-run) — one command builds a Chromium image and runs the same check.

**Keys (`.env`) — all optional.** A dry-run (and the smoke test) need **no keys** — only system Chrome. Set these to enable optional features:

| Key | Enables | Without it |
|---|---|---|
| `JOOBLE_LLM_API_KEY` | LLM fallback for novel free-text custom questions (Claude Haiku) | rules-only; an unmapped required question fails closed (`FORM_SCHEMA_UNMAPPED`) |
| `JOOBLE_LLM_TIMEOUT_S` · `JOOBLE_PER_APPLY_TIMEOUT_S` | hard ceilings: per card-answer LLM call (default 30s) and per-vacancy wall-clock (default 180s; raise for submit runs) | defaults apply; a slow LLM call still fails closed, a long submit run still gets guillotined at 180s |
| `JOOBLE_CAPSOLVER_API_KEY` / `JOOBLE_TWOCAPTCHA_API_KEY` | the solver fallback path | solver fails closed (it's empirically dead for Lever — §4); silent-pass only |
| `JOOBLE_IMAP_*` | capture the post-submit confirmation email as evidence | confirmation poll skipped (never blocks a result) |
| `JOOBLE_IPQS_API_KEY` | egress-IP reputation axis in `fingerprint_check.py` | that axis prints "unknown" and proceeds |
| `JOOBLE_PROXY_*` · `JOOBLE_USER_DATA_DIR` · `JOOBLE_BROWSER_TIMEZONE` | silent-pass tuning (proxy exit, persistent profile, geo coherence) | direct home IP, default profile dir, system timezone |
| `JOOBLE_MOTION_TRACES` · `JOOBLE_MOTION_SOURCE` | replay **real recorded-human** mouse/scroll/keystroke motion in the captcha's scoring window (record via `scripts/record_motion.py`) | synthetic Bézier motion (the default — no regression) |

Key dependencies _(versions verified 2026-06)_ — **core:** `patchright>=1.60.1` (stealth Playwright fork — the browser engine; drives system Chrome via `executable_path`), `httpx>=0.28.1` (solver REST + IP pre-flight), `pydantic[email]>=2.13` (`EmailStr` needs `email-validator`) + `pydantic-settings`, `selectolax` (safe HTML parsing); **quality:** `structlog` (tracing); **optional features:** `2captcha-python` (fallback solver), `imap-tools` (confirmation-email evidence). Human mouse/delays use **stdlib `random`/`math`** (no `numpy`). **Dev** (installed by `uv sync`): `ruff` (with `ASYNC` rules), `basedpyright` (strict on our code), `pytest` + `pytest-asyncio`.

## Usage

```bash
# Run all vacancies from the generated file (dry-run is the default — fills the form, stops before POST)
uv run applyme run --vacancies data/vacancies.txt --profile data/profile.json

# Single posting, headful
uv run applyme run --url https://jobs.lever.co/<company>/<id> --headful

# Explicit submit mode — pick ONE of: dry-run (default) | sandbox (leverdemo) | real (live ATS)
uv run applyme run --vacancies data/vacancies.txt --profile data/profile.json --submit-mode sandbox

# Additional options
#   --headful              open a visible browser window (default: headless)
#   --max-applies N        cap the number of vacancies processed (default: 5)
#   --per-apply-timeout S  per-vacancy wall-clock ceiling (default 180s). A submit run (warm dwell ×2
#                          + human typing + pre-submit dwell + the dead /thanks wait + recorded motion)
#                          needs more than a dry-run — pass e.g. 600 so it reaches a verdict, not a timeout.
```

Outputs land in `output/` (git-ignored): `results.json` (one `ApplyResult` per vacancy, written incrementally) plus per-attempt evidence at `output/<company>/<posting_id>/<label>.png` (full-page screenshot) + `<label>.html` (redacted snapshot), where `<label>` is `dry-run`, `unmapped`, or `final`. The **committed** copies of the 6 run shots are in [`screenshots/`](screenshots/) (see its README).

---

## The silent-pass test (headful submit) — the one thing only a human-looking session can prove

**What's verified.** The full pipeline — navigate → fill → upload résumé → answer cards → **POST** → classify → evidence — works end-to-end. A headful submit **silent-passes the invisible hCaptcha and succeeds** on both `leverdemo` and a **real** posting (skillerszone → "Application submitted!"); across the 5 real postings: 1 SUCCESS, 3 duplicates, 1 fail-closed (screenshots). The silent-pass needs **real headful Chrome** (headless is itself a detection signal) **+ a clean IP**; on a stricter tenant or a dirtier IP the same path **fails closed** (`captcha_blocked`).

**Run it (on your own machine, visible window):**

```bash
# 0) Gate readiness for free first — fix any red before spending a Lever attempt:
uv run python scripts/fingerprint_check.py
# 0b) (optional) Record real human motion + verify it drives Chrome — the behavioural lever (REPORT §4a):
uv run python scripts/record_motion.py            # → data/motion/human_traces.json
export JOOBLE_MOTION_TRACES=data/motion/human_traces.json
uv run python scripts/check_motion.py             # prints "OK | … | source=recorded"
# 1) Sandbox = Lever's own demo tenant. A REAL POST, but to a fake company — safe, spams no employer.
#    --per-apply-timeout 600: a submit run does more than a dry-run (warm + dwell + the /thanks wait),
#    so give it headroom or asyncio.timeout chops it mid-flight → RETRYABLE_ERROR with no verdict.
uv run applyme run \
  --url https://jobs.lever.co/leverdemo/33538a2f-d27d-4a96-8f05-fa4b0e4d940e \
  --submit-mode sandbox --headful --per-apply-timeout 600
```

**How to read the result** (`output/leverdemo/<id>/final.png` + the `result_string` in `output/results.json`):

| Outcome | Means |
|---|---|
| **`success`** → redirected to `/<co>/<id>/thanks` | The **silent-pass worked**: a clean headful session minted the hCaptcha token natively and the application submitted. This is the load-bearing KPI passing. |
| **`captcha blocked`** → stayed on `/apply`, a challenge rendered or the token was rejected | The invisible hCaptcha did **not** silent-pass this session (stricter tenant / dirtier IP). The bot records it honestly and never injects a fake token ([REPORT §4](docs/REPORT.md)). |
| **`failed:<reason>`** → 400 re-render, a field flagged | A form/validation issue (e.g. the fabricated profile vs. résumé mismatch), distinct from the captcha. |

**Measured (2026-06-11) — it silent-passes on `leverdemo`.** A headful `--submit-mode sandbox` run **succeeds**: `status: SUCCESS`, `silent_pass: true`, no challenge rendered, redirecting to `www.lever.co/hp-b?LeverAppId=<uuid>` (leverdemo has no `/thanks` page; Lever mints that application id only *after* accepting the POST). Reproduced with distinct app ids. **The correction worth reading ([REPORT §4](docs/REPORT.md)):** for most of this project the submit reported `captcha blocked`, and an extensive by-elimination investigation (fingerprint, IP tier, geo, motion) concluded the captcha was an unbeatable wall. **That was wrong — the cause was our own bug:** the bot clicked `button[type=submit]`, which on Lever's page is a *hidden* element; the real control is `button.template-btn-submit` (`type=button`), whose handler runs `hcaptcha.execute()` then POSTs. So the submit never fired and the page sat on `/apply`; a hidden "résumé too large" banner then made the classifier mislabel it `captcha blocked`. Fixing both, the silent-pass works. **Honest caveats:** `leverdemo` is a *demo* tenant that may score more permissively than a real company's Enterprise config; the score is probabilistic (opportunistic, not a guarantee); real-posting submit is opt-in and unmeasured. When the score is too low the bot **fails closed** (`captcha_blocked`, never a bad submit) — the brief's _"pass **or** correctly handle."_ Production answer ([REPORT §5](docs/REPORT.md)): an IP fleet to maximise the silent-pass rate + a human-in-the-loop / employer-API tail for the residual.

---

## Docker: reproducible browser run

A `Dockerfile` ships a headless **Chromium** runtime so the browser path runs anywhere — a server, CI, or a machine with no desktop Chrome — with zero host setup. The image installs Chromium + the project (via `uv sync --frozen`, exact `uv.lock` pins) and runs as a non-root user.

```bash
docker build -t applyme-bot .

# Default: prove the engine drives Chrome end-to-end (loads a live Lever page, no webdriver leak,
# parses the form). Needs no data/ or API keys:
docker run --rm applyme-bot
# → OK | …/leverdemo/…/apply | navigator.webdriver=false | sitekey=e33f87f8-… | standard_fields=6 | cards=1

# Run the browser-dependent integration tests against real headless Chromium:
docker run --rm applyme-bot uv run pytest -m integration

# Run a real dry-run apply (fills + captures evidence, stops before POST). Mount your inputs + outputs
# and pass secrets via --env-file (never baked into the image):
docker run --rm --env-file .env \
  -v "$PWD/data:/app/data" -v "$PWD/output:/app/output" \
  applyme-bot uv run applyme run --vacancies data/vacancies.txt --profile data/profile.json
```

The container sets `JOOBLE_HEADFUL=false` and `JOOBLE_CHROME_NO_SANDBOX=true` (both required in a container); the same env contract drives `scripts/check_chrome.py` and the integration tests, so they auto-adapt to headless without code changes. A visible/headful run still happens on your own machine (`--headful`).

---

## Troubleshooting

**Browser won't launch.** patchright drives your system Chrome/Chromium via `executable_path` (no separate download). In order of likelihood:

1. **No display / running in a container or CI** — keep it headless with `JOOBLE_HEADFUL=false`, and in a container also set `JOOBLE_CHROME_NO_SANDBOX=true` (Chrome refuses to start as root with the sandbox on).
2. **Chrome isn't auto-found** — set `JOOBLE_CHROME_PATH` to the binary (e.g. `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`).
3. **No local Chrome at all** — use the [Docker path](#docker-reproducible-browser-run): it bundles Chromium, so no host browser is needed.

**Smoke-test the browser end-to-end** (no `data/` or keys needed): `uv run python scripts/check_chrome.py` — prints `OK | … navigator.webdriver=false | sitekey=… | cards=N` when the engine drives Chrome correctly.

---

## Result model

Each vacancy yields an `ApplyResult` with `status ∈ {SUCCESS, FAILED, CAPTCHA_BLOCKED, DRY_RUN_READY, DUPLICATE_SUSPECTED, RETRYABLE_ERROR}`, a `reason`, the `final_url`, `http_status`, any `flagged_fields` (parsed from a 400 re-render), solver telemetry, and evidence paths — plus a `result_string` in the brief's literal form (`success` / `failed:<reason>` / `captcha blocked`). Detection: **SUCCESS** = redirect to `/<company>/<id>/thanks`; **FAILED/CAPTCHA_BLOCKED** = HTTP 400 re-rendering the form with `p.error-message`. Details in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#result-model).

---

## Deliverables mapping (task → artifact)

| Task deliverable | Where |
|---|---|
| Working script | `src/applyme/` |
| Working code (repo/archive) | this repository |
| Screenshots/video of 5 attempts | [`screenshots/`](screenshots/) — the 5 real-posting outcomes (skillerszone `SUCCESS`, 3 `DUPLICATE`, padsplit fail-closed) + the `leverdemo` submit (**`SUCCESS`** — silent-pass) |
| Report: apply approach · captcha approach · frontend requests · failures · prod+X1000 | [`docs/REPORT.md`](docs/REPORT.md) |
| Stack justification | this README + [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#stack-rationale) |

---

## Decisions (locked) & build-time unknowns

**Locked decisions:**
- **Submit mode** — `SUBMIT_MODE` defaults to `dry-run` (fill, stop before POST); `sandbox`/`real` require the explicit flag, so a stray run can't fire a live application. The committed evidence is the 5 real postings run in submit mode (1 `SUCCESS`, 3 `DUPLICATE`, 1 fail-closed) + the `leverdemo` sandbox `SUCCESS` — the silent-pass is proven on **both a real tenant and the demo**.
- **Scope** — clean modular single-process MVP CLI for the 5 applies; X1000-scale is the report's roadmap, not built.
- **Captcha/proxy** — the design leans on the **in-browser silent pass**; the solver fallback is wired but **fails closed** and is **empirically unreliable** for Lever's invisible Enterprise hCaptcha. Verified 2026-06 (live keys + multi-source research, §4): CapSolver **delisted hCaptcha entirely** (its hCaptcha doc 404s; "service not supported"), and for invisible Enterprise the token _is_ a passive **risk score** — so an out-of-band token is rejected even when `siteverify` passes. The bot therefore no longer fires a doomed proxyless request (no `rqdata` → `SolverUnavailable`); a non-solvable challenge is recorded `captcha_blocked`. Run from a clean local IP, no proxies for the 5-apply test.
- **Profile data** — used **as-is**, including the provided email (Lever has **no blocking verify step** — success is the `/thanks` redirect). The confirmation-email poll is **optional**: only if you set `JOOBLE_IMAP_*` does the bot additionally capture Lever's post-submit confirmation email as best-effort evidence.

**Verified:** the full pipeline **submits end-to-end** — a headful submit **silent-passes the invisible hCaptcha and succeeds** on both `leverdemo` and the **real** skillerszone posting ("Application submitted!"); across the 5 real postings, 1 `SUCCESS` / 3 `DUPLICATE` / 1 fail-closed, `navigator.webdriver=false`, engine reproducible headless in Docker. The third-party solvers were tested with live keys and found unreliable for Lever (above) — which is *why* the working silent-pass is the path, not a solver.

**Remaining unknowns:** the silent-pass *rate* across real tenants and IP tiers — it's demonstrated on `leverdemo` and one real posting (skillerszone), but each tenant's Enterprise config + the session's IP reputation set a per-run pass probability that only volume can measure (the load-bearing production KPI); plus the interactive-challenge rate and whether a Lever applicant email-verification step fires.

## Ethics & legal note

The provided candidate data is synthetic/inconsistent and the target postings are real companies' ATS pipelines. Mass auto-submission may conflict with Lever's / employers' Terms and with hCaptcha's terms (active 2024–2026 enforcement). For the test, volume is kept minimal, outcomes are reported honestly (including failures), and a sandbox/dry-run path is available so the pipeline can be demonstrated without polluting real ATS instances. Authorization should be confirmed before any scaled use.
