# ApplyMe — Lever Auto-Apply

Automated job-application bot for **[jobs.lever.co](https://jobs.lever.co)**. Given a candidate profile + resume, it opens a list of Lever postings, completes each Apply flow (fills the form, uploads the resume, handles the invisible hCaptcha, submits), and records a structured outcome per vacancy.

Built as an engineering take-home: an auto-apply bot for Lever.

> **Status: implemented.** The full apply pipeline (parse → fill → captcha → submit → evidence) is working code. The architecture is in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md); the deliverable report in [`docs/REPORT.md`](docs/REPORT.md).

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

Drive the **real `/apply` form** through a **stealth Playwright (patchright) browser** that lets Lever's _invisible_ hCaptcha self-solve for clean sessions, falling back to a paid solver only when an interactive challenge actually fires. This single decision fixes the four things that defeat a raw-HTTP approach at once: the Cloudflare TLS/JA4 fingerprint, the `__cf_bm` session cookie, the in-browser behavioral signals hCaptcha scores, and the JS-rendered per-posting custom questions.

| Layer | Choice | One-line why |
|---|---|---|
| Language | **Python** | best-in-class browser tools are Python-first; Node's stealth ecosystem is decayed |
| Browser engine | **patchright** (stealth Playwright fork) | `navigator.webdriver` genuinely false **and** it auto-waits through Lever's `parseResume` re-render — which hangs/crashes a raw-CDP driver (see below) |
| Engine note | zendriver (raw CDP) **rejected after testing** | stealthiest transport, but its `Runtime.evaluate`/`callFunctionOn` **hang (headless) or crash the renderer (headful)** on Lever's reactive re-render, and a hung-call cancellation corrupts the connection — unrecoverable. Documented in [`REPORT`](docs/REPORT.md) §4. |
| Captcha | **in-browser silent pass first**, solver fallback | clean session self-solves invisible hCaptcha for free; solver is insurance |
| Solver | **CapSolver** → **2Captcha** | AI/token with a hybrid fallback, behind one interface (both empirically unreliable for Lever Enterprise hCaptcha — §4) |
| Human behavior | **log-normal delays** + **own stdlib Bézier mouse** | non-fixed timing + curved/in-element-random clicks, dispatched via Playwright's `page.mouse`; per-character typing |
| Proxies | clean local IP (test) · residential pool (scale) | highest trust for 5 applies; datacenter IPs get challenged |
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
   fill standard fields (human-like timing + mouse)
   map profile → custom-question answers (rules; LLM fallback)
   upload resume
      │
      ▼
   trigger hCaptcha:
      ├─ silent pass (clean session)            → submit
      └─ interactive challenge → CapSolver/2Captcha token → submit
      │
      ▼
   detect outcome:
      ├─ redirect to /…/thanks                  → SUCCESS
      └─ 400 re-render with error-message       → FAILED / CAPTCHA_BLOCKED
      │
      ▼
   on SUCCESS: capture Lever confirmation email as evidence (best-effort; not a gate)
      │
      ▼
   capture screenshots + HTML snapshot, append ApplyResult
      │
      ▼
write output/results.json  +  evidence
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the exact form fields, endpoints, captcha config, and success/failure signals.

---

## Project structure

```
.
├── README.md
├── pyproject.toml
├── Dockerfile / .dockerignore  # headless-Chromium image (reproducible browser run / CI)
├── .env.example                # CAPSOLVER/2CAPTCHA keys, IMAP_* mailbox, SUBMIT_MODE, LLM key
├── src/applyme/
│   ├── __main__.py / cli.py     # entrypoint:  applyme run …
│   ├── config.py                # settings (pydantic-settings), submit-mode flag
│   ├── models.py                # CandidateProfile, Vacancy, ApplyResult (pydantic)
│   ├── profile_loader.py        # parse profile.json + fetch/locate resume PDF
│   ├── app_pw.py                # single-vacancy flow: warm → /apply → parse → fill → dry-run / submit
│   ├── browser/
│   │   ├── pw_engine.py         # patchright launch (system Chrome via executable_path), webdriver-leak guard
│   │   └── human.py             # log-normal delays, stdlib-Bézier mouse (via page.mouse)
│   ├── lever/
│   │   ├── form.py              # parse apply form + cards/baseTemplate JSON
│   │   ├── pw_fill.py           # fill standard fields + card answers (auto-waiting page.check/select_option/fill)
│   │   ├── locations.py         # selectedLocation handling
│   │   ├── submit.py            # submit + outcome detection (/thanks vs 400)
│   │   └── verify.py            # best-effort: capture Lever confirmation email (evidence, not a gate)
│   ├── captcha/
│   │   ├── base.py              # Solver protocol: solve({sitekey,pageurl,isInvisible,rqdata?,proxy?})
│   │   ├── capsolver.py
│   │   └── twocaptcha.py
│   ├── answers/
│   │   ├── rules.py             # deterministic profile → card-answer mapping
│   │   └── llm.py               # optional LLM fallback for unmapped questions
│   ├── evidence.py              # screenshots + HTML/HAR snapshots
│   └── runner.py                # orchestrate the N vacancies, collect results
├── inputs/                      # raw provided: profile.md, resume.md, vacancies.md, test.pdf (gitignored)
├── scripts/
│   ├── prepare_inputs.py        # inputs/ → data/
│   └── check_chrome.py          # smoke: drive Chrome on a live Lever page (no data/ or keys)
├── data/                        # generated: profile.json, resume.pdf, vacancies.txt (gitignored)
├── output/                      # results.json, screenshots/  (gitignored)
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

ApplyMe provides the raw material as `.md` in **`inputs/`** (and the résumé as a *URL*), so a one-shot prep step bridges `inputs/` → `data/`:

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
uv sync                       # OR:  pip install -e .
uv run applyme run --help     # patchright drives your system Chrome (no separate browser download)

cp .env.example .env          # add CAPSOLVER_API_KEY + mailbox creds (optional for a silent-pass run)

# Verify the engine actually drives Chrome — loads a live Lever page through Cloudflare, asserts
# there's no `navigator.webdriver` leak, and parses the form. Needs nothing but Chrome (no data/, no keys):
uv run python scripts/check_chrome.py
# → OK | …/leverdemo/…/apply | navigator.webdriver=false | sitekey=e33f87f8-… | standard_fields=6 | cards=1
```

> **No local Chrome / headless box / CI?** Use [Docker](#docker-reproducible-browser-run) — one command builds a Chromium image and runs the same check.

Key dependencies _(versions verified 2026-06)_ — **core:** `patchright>=1.60.1` (stealth Playwright fork — the browser engine; drives system Chrome via `executable_path`), `httpx>=0.28` (CapSolver REST), `pydantic[email]>=2.12` (`EmailStr` needs `email-validator`) + `pydantic-settings`, `selectolax` (safe HTML parsing); **quality:** `tenacity` (retries), `structlog` (tracing); **optional features:** `2captcha-python` (fallback solver), `imap-tools` (confirmation-email evidence). Human mouse/delays use **stdlib `random`/`math`** (no `numpy`). **Dev:** `ruff` (with `ASYNC` rules), `basedpyright` (strict on our code), `pytest` + `pytest-asyncio`.

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
```

Outputs land in `output/`: `results.json` (one `ApplyResult` per vacancy) plus per-attempt screenshots and HTML snapshots used as the evidence deliverable.

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
| Screenshots/video of 5 attempts | `output/screenshots/` |
| Report: apply approach · captcha approach · frontend requests · failures · prod+X1000 | [`docs/REPORT.md`](docs/REPORT.md) |
| Stack justification | this README + [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#stack-rationale) |

---

## Decisions (locked) & build-time unknowns

**Locked decisions:**
- **Submit mode** — `SUBMIT_MODE` defaults to `dry-run` (fill+solve, stop before POST). The **committed 5-attempt evidence is generated on Lever's `leverdemo` sandbox** (proves the pipeline without polluting real ATS); `real` (into the 5 live postings) requires the explicit flag, so a stray run can't fire a live application.
- **Scope** — clean modular single-process MVP CLI for the 5 applies; X1000-scale is the report's roadmap, not built.
- **Captcha/proxy** — real CapSolver key wired (a fired challenge is solved for real); run from a clean local IP, no proxies for the 5-apply test.
- **Profile data** — used as-is, with the placeholder email swapped for a real mailbox we control, so Lever's post-submit confirmation email is captured as evidence. (Lever has **no blocking verify step** — success is the `/thanks` redirect; the mailbox poll is best-effort.)

**Build-time unknowns to verify:** whether Lever enforces Enterprise hCaptcha `rqdata`; the real interactive-challenge rate; whether the authenticated POST re-fingerprints harder than the GET; whether a Lever applicant email-verification step fires.

## Ethics & legal note

The provided candidate data is synthetic/inconsistent and the target postings are real companies' ATS pipelines. Mass auto-submission may conflict with Lever's / employers' Terms and with hCaptcha's terms (active 2024–2026 enforcement). For the test, volume is kept minimal, outcomes are reported honestly (including failures), and a sandbox/dry-run path is available so the pipeline can be demonstrated without polluting real ATS instances. Authorization should be confirmed before any scaled use.
