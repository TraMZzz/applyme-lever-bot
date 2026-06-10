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

Drive the **real `/apply` form** through a **stealth, direct-CDP browser** that lets Lever's _invisible_ hCaptcha self-solve for clean sessions, falling back to a paid solver only when an interactive challenge actually fires. This single decision fixes the four things that defeat a raw-HTTP approach at once: the Cloudflare TLS/JA4 fingerprint, the `__cf_bm` session cookie, the in-browser behavioral signals hCaptcha scores, and the JS-rendered per-posting custom questions.

| Layer | Choice | One-line why |
|---|---|---|
| Language | **Python** | best-in-class tools (`zendriver`) are Python-first; Node's stealth ecosystem is decayed |
| Browser engine | **zendriver** (direct-CDP `nodriver` fork) | no Playwright/Selenium shim → `navigator.webdriver` genuinely false; top performer in a 2026 Cloudflare benchmark (28 OK/3 gated/0 hard-blocked — page access, not hCaptcha-pass) |
| Engine licensing | zendriver-only (AGPL-3.0) | **patchright** is the permissive swap path for SaaS, but needs its own CDP-tab adapter — documented, not wired in the MVP |
| Captcha | **in-browser silent pass first**, solver fallback | clean session self-solves invisible hCaptcha for free; solver is insurance |
| Solver | **CapSolver** → **2Captcha** | AI/token (fast, cheap, `isInvisible`+`rqdata`) with a hybrid fallback, behind one interface |
| Human behavior | **log-normal delays** + **own stdlib Bézier mouse** | non-fixed timing + curved/overshoot/in-element-random clicks (ghost-cursor is dead/no-CDP; zendriver's `mouse_move` is straight-line, so this is our code) |
| Proxies | clean local IP (test) · residential pool (scale) | highest trust for 5 applies; datacenter IPs get challenged |
| Models / output | **Pydantic** + `results.json` + screenshots | lean for a "script"; DB/queue are scale concerns, not built |

Full justification incl. **why each alternative was rejected**: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#stack-rationale).

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
├── .env.example                # CAPSOLVER/2CAPTCHA keys, IMAP_* mailbox, SUBMIT_MODE, LLM key
├── src/applyme/
│   ├── __main__.py / cli.py     # entrypoint:  applyme run …
│   ├── config.py                # settings (pydantic-settings), submit-mode flag
│   ├── models.py                # CandidateProfile, Vacancy, ApplyResult (pydantic)
│   ├── profile_loader.py        # parse profile.json + fetch/locate resume PDF
│   ├── browser/
│   │   ├── engine.py            # zendriver launch, fingerprint, stealth
│   │   ├── human.py             # log-normal delays, stdlib-Bézier CDP mouse
│   │   └── warmup.py            # session warming
│   ├── lever/
│   │   ├── form.py              # parse apply form + cards/baseTemplate JSON
│   │   ├── fill.py              # fill standard fields + card answers
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
├── data/                        # profile.json, resume.pdf (gitignored)
├── output/                      # results.json, screenshots/  (gitignored)
├── docs/  ├── ARCHITECTURE.md  └── REPORT.md
└── tests/  # unit + integration; fixtures/ holds captured HTML
```

---

## Inputs & email

The bot reads three things, all **git-ignored** (supply them under `data/`):

| File | What | Source |
|---|---|---|
| `data/profile.json` | candidate profile (→ the `CandidateProfile` model) | generated from the provided `profile.md` |
| `data/resume.pdf` | the résumé to upload | downloaded from the profile's `resume_url` |
| `data/vacancies.txt` | Lever URLs, one per line | from the provided `vacancies.md` |

ApplyMe provides these as `.md` (and the résumé as a *URL*), so a one-shot prep step bridges them:

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
uv run applyme run --help     # zendriver uses your system Chrome (no download)
# (patchright is an optional future engine-swap — not needed to run)

cp .env.example .env          # add CAPSOLVER_API_KEY + mailbox creds (optional for a silent-pass run)
```

Key dependencies _(versions verified 2026-06)_ — **core:** `zendriver>=0.15.3`, `httpx>=0.28` (CapSolver REST), `pydantic[email]>=2.12` (`EmailStr` needs `email-validator`) + `pydantic-settings`, `selectolax` (safe HTML parsing); **quality:** `tenacity` (retries), `structlog` (tracing); **optional features:** `patchright>=1.60` (engine-swap path, not wired), `2captcha-python` (fallback solver), `imap-tools` (confirmation-email evidence). Human mouse/delays use **stdlib `random`/`math`** (no `numpy`). **Dev:** `ruff` (with `ASYNC` rules), `basedpyright` (strict on our code), `pytest` + `pytest-asyncio`.

## Usage

```bash
# Run all vacancies from a file (dry-run is the default — fills the form, stops before POST)
uv run applyme run --vacancies data/vacancies.json --profile data/profile.json

# Single posting, headful
uv run applyme run --url https://jobs.lever.co/<company>/<id> --headful

# Explicit submit mode
uv run applyme run --vacancies data/vacancies.json --profile data/profile.json \
    --submit-mode dry-run   # default: fill + solve, stop before POST
    --submit-mode sandbox   # submit to Lever's leverdemo sandbox
    --submit-mode real      # submit to the live ATS posting

# Additional options
#   --headful              open a visible browser window (default: headless)
#   --max-applies N        cap the number of vacancies processed (default: 5)
```

Outputs land in `output/`: `results.json` (one `ApplyResult` per vacancy) plus per-attempt screenshots and HTML snapshots used as the evidence deliverable.

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
