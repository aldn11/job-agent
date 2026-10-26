# Vargas — Job Screening Agent (v1.3.4)

[![Version](https://img.shields.io/badge/version-1.3.4-blue.svg)](https://github.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-green.svg)](https://www.python.org/)
[![Hermes Agent](https://img.shields.io/badge/Agent-Hermes%20%2F%20Gemini-orange.svg)](https://github.com/NousResearch/Hermes-Agent)

**Vargas** is an autonomous AI agent designed for automated job screening and candidate-matching evaluation. It eliminates the time-consuming process of manually reviewing dozens of job postings on LinkedIn and RSS feeds, automating data collection, factual profile matching, and real-time Telegram notifications for high-compatibility opportunities.

---

##  Problem & Solution

### The Problem
Searching for relevant job opportunities requires manually screening hundreds of job postings per week. Most listings feature generic titles, misaligned requirements, unspecified work modes (hybrid/remote/on-site restrictions), or unrealistic experience demands. This manual process consumes hours every day and leads to decision fatigue.

### The Solution
**Vargas** acts as an automated personal recruiter running 24/7 on a local server:
1. **Filters Out Noise:** Automatically ingests LinkedIn job alerts and RSS feeds, applying regex pattern filtering, keyword inclusion/exclusion rules, and SHA-256 fingerprint deduplication.
2. **Factual AI Extraction:** Sends raw job descriptions to a local agent (Hermes Agent) instructed under strict guidelines (ASD-STE100 technical English) to extract pure facts without hallucinations (mandatory requirements, nice-to-haves, location, seniority, and required language proficiency).
3. **Deterministic Score Calculation:** Applies a strict mathematical scoring formula in Python over the extracted facts, delivering deterministic match scores (0 to 100) and actionable verdicts (`APPLY`, `MAYBE`, or `SKIP`).
4. **Real-Time Alerts:** Sends formatted Telegram notifications for recommended openings.

---

##  System Architecture

```text
 ┌────────────────────────┐      ┌─────────────────────────┐
 │   Email Job Alerts     │      │ RSS: WWR + Jobspresso + │
 │ (LinkedIn / Remōt)     │       │       Remotive          │
 └───────────┬────────────┘      └────────────┬────────────┘
             │ IMAP                           │ HTTP / RSS
             ▼                                ▼
  ┌─────────────────────┐          ┌─────────────────────┐
  │ email_collector.py  │          │ RSS collectors      │
  │ (Anonymous Scraping)│          │ (WWR/Jobspresso/Rem.)│
  └──────────┬──────────┘          └──────────┬──────────┘
             │                                │
             └────────────────┬───────────────┘
                              │
                              ▼
                  ┌──────────────────────┐
                  │    Inbox Folder &    │
                  │  SQLite (jobs.db)    │
                  └───────────┬──────────┘
                              │ Pending 'NEW' Jobs
                              ▼
                  ┌──────────────────────┐
                  │   AI Agent (Vargas)  │ ◄── SOUL.md / criteria.md
                  │  (Hermes in Docker)  │ ◄── candidate.md
                  └───────────┬──────────┘
                              │ Factual Extraction (JSON)
                              ▼
                  ┌──────────────────────┐
                  │      wrapper.py      │ ── Deterministic Engine
                  │  (Score Calculation) │    (0-100 Score & Verdict)
                  └───────────┬──────────┘
                              │ APPLY / MAYBE
                              ▼
                  ┌──────────────────────┐
                  │ telegram_notify.py   │
                  │ (Text alert)         │
                  └──────────────────────┘

```

---

##  How the Pipeline Works

1. **Multi-Source & Anonymous Ingestion:**
* **`collector.py`**: Ingests the public We Work Remotely RSS feed, applying inclusion/exclusion lists and text normalization.
* **`jobspresso_collector.py`**: Ingests only the public Jobspresso job-listing RSS feed (`https://jobspresso.co/feed/?post_type=job_listing`); it does not fetch listing pages. It reads `content:encoded` (or RSS `description` as fallback), and records company/location from RSS creator metadata and publication time from `pubDate`. Only HTTPS links on `jobspresso.co` and its subdomains are accepted; duplicate entries are normalized before SQLite persistence. Descriptions shorter than 100 characters or 15 words are skipped. Publication date and location are recorded outside the `JOB TEXT` section; the complete feed content remains untrusted data within Vargas's existing `BEGIN`/`END` prompt boundary.
* **`remotive_collector.py`**: Ingests only Remotive's public RSS XML at `https://remotive.com/remote-jobs/feed` (`https://remotive.com/feed` currently returns 404). It stores the original Remotive listing URL and credits Remotive in the inbox text and alert source; it does not crawl listing HTML or submit/relist jobs on third-party job boards. The resulting evaluation and private Telegram alert remain within the user's personal workflow. RSS entries include title, job ID, company, location, job type, category, publication date and HTML description. Descriptions need at least 100 characters and 15 words; prompt boundary lines from RSS are neutralized. Remotive's API notice describes a 24-hour delay for API data; that statement is not assumed to apply to RSS, for which no separate freshness guarantee was found.
* **`bairesdev_collector.py`**: Reads active support-role data embedded in the public careers landing page at `https://talent.bairesdev.com/`. It does not access the applicant portal or fetch individual application pages. The collector validates the embedded widget configuration, keeps only active jobs with official BairesDev application URLs and complete descriptions, and applies the same support-role title filter as We Work Remotely. Location variants sharing one application URL are consolidated, with each listed region retained as metadata. The embedded page data is not an RSS feed and may change format; missing or invalid listing data raises an explicit error rather than being treated as an empty feed.
* **Optional source preferences:** Both RSS collectors can optionally filter parsed RSS records using `.env` settings. `REMOTIVE_FEED_CATEGORIES` selects Remotive's upstream category feeds (blank means its general feed). For either source, `*_INCLUDE_TERMS` keeps a job when any semicolon-separated term occurs in title, description, type or category; `*_EXCLUDE_TERMS` removes a job if any listed term occurs there. `*_INCLUDE_CATEGORIES` matches an exact RSS category label, and `*_INCLUDE_LOCATIONS` matches a substring of the RSS location field. Values within one setting use OR; non-empty settings are combined with AND. Empty settings do not narrow results. These are source collection preferences, not changes to the private Vargas candidate/criteria profile. Jobspresso's current job-listing RSS does not document category/location/keyword parameters; testing `search_keywords`, `search_location` and `search_categories` query parameters showed the feed returned the same entries, so its optional filters run locally using RSS metadata only. No collector fetches job-detail HTML.
* **`email_collector.py`**: Connects via IMAP and searches for unread (`UNSEEN`) LinkedIn and Remōt alert emails only; message bodies are fetched with `BODY.PEEK[]`, so inspection does not mark them read. LinkedIn URLs retain the existing anonymous public HTTPS scraping flow (without cookies or user login); redirects outside LinkedIn are rejected. Remōt links are accepted only from HTTPS `workremot.com` hosts (including its subdomains), normalized, and saved to SQLite. No Remōt page or external application URL is fetched. An email containing a supported listing is marked read only after the listing is durably persisted. An unread message with no supported listing has minimal headers (Message-ID, sender, subject and date), a reason, and a SHA-256 fingerprint recorded in SQLite `email_review_queue`; only after that record is committed is the message marked read. The original message remains in the mailbox for manual review. Fetch or persistence failures remain unread for retry. Review entries can be inspected with `sqlite3 jobs.db "SELECT recorded_at, message_id, sender, subject, reason FROM email_review_queue ORDER BY recorded_at DESC;"`.
* **Remōt alert format and description limit:** The repository does not contain a real Remōt alert sample or a verified email template. Its parser therefore finds official listing URLs in generic HTML or plain-text messages rather than assuming a specific sender or markup. Title/link-only alerts are stored as `DESCRIPTION_REQUIRED`, not placed in the evaluation inbox, and never sent to Vargas without job-description text. External links labeled as application links are stored separately for transparency when one listing and one application URL are unambiguous. Add the full job description (at least 100 characters and 15 words) with `python add_remot.py`; this promotes the saved alert to `NEW` and creates its inbox file. The Remōt alert's search is IMAP `UNSEEN BODY workremot.com`; providers that do not support standard IMAP `BODY` search may require a provider-specific query change.
* The public listing's location and workplace-type metadata are appended to the job text when available. This gives the AI evidence to cite without granting it Internet access.
* The We Work Remotely RSS collector accepts only We Work Remotely URLs. Links whose public pages are blocked, rate-limited, or lack a usable description stay in a `SCRAPE_PENDING` queue. Extraction tries the page description and structured `JobPosting` data; results need at least 100 characters and 15 words. Transient network failures retry with exponential backoff. After five attempts specifically failing to extract a usable description or encountering a recognized access-block page, the job moves to `SCRAPE_REVIEW` and is no longer retried automatically; inspect it with `sqlite3 jobs.db "SELECT id, url, scrape_attempts, scrape_last_error FROM jobs WHERE status = 'SCRAPE_REVIEW';"` and review the listing manually. Other transient failures continue retrying under the existing policy. Confirmed HTTP 404/410 responses become `SCRAPE_UNAVAILABLE` and are no longer retried. Entries older than `SCRAPE_MAX_AGE_DAYS` (default: 30) are also retired, since old job postings are unlikely to remain actionable. There is no per-run job count limit.
* Configure the Jobspresso feed URL with `JOBSPRESSO_FEED_URL` in `.env` if needed; its default is the job-listing RSS URL above. The pipeline reads that RSS feed on each run and does not crawl job detail pages.
* Configure upstream Remotive category RSS feeds with `REMOTIVE_FEED_CATEGORIES`; blank means the general feed. The eight `REMOTIVE_*` / `JOBSPRESSO_*` include/exclude settings in `.env.example` are blank by default. Set only the preferences you want in your private, untracked `.env`; the schedule is unchanged. Jobspresso category and location filtering is limited to values present in its RSS entries, and no metadata means that a configured include filter will exclude that listing.
* Schema upgrades do not backfill historical evaluated jobs into the scraping queue. A one-time repair restores their saved evaluation status and retires old unresolved entries (including legacy rows with at least three failed scrape attempts, missing/invalid dates, or age beyond the configured window) instead of triggering large batches of old requests. Newer transient failures continue retrying as before.


2. **Persistence & Storage:**
* Records job entries in an SQLite database (`jobs.db`) using a unique SHA-256 fingerprint (`company:title`) to prevent duplicate evaluations.
* Saves raw pending job descriptions into the `inbox/` directory.


3. **Factual AI Screening:**
* **`wrapper.py`** triggers the local Hermes Agent Docker container.
* The LLM parses `SOUL.md` (persona), `criteria.md` (ASD-STE100 extraction rules), and `candidate.md` (anonymized candidate profile) to produce a structured JSON factual breakdown.
* Before starting Hermes, the pipeline checks for a valid HTTPS job URL (when supplied) and a usable description (at least 100 characters and 15 words). Invalid URLs go to `INPUT_REVIEW`; missing/short LinkedIn descriptions return to `SCRAPE_PENDING`; other sources go to `SCRAPE_REVIEW`. These jobs do not consume an AI call.
* Collectors deduplicate canonical job URLs before evaluation. Manual entries also deduplicate by URL, or by exact company, title and pasted text when no URL is supplied. Company/title-only matches remain labeled as possible duplicates instead of being discarded, because that heuristic can hide distinct openings.
* `MAX_JOB_DESCRIPTION_CHARS` limits the description sent to Vargas (default: 12000). Longer descriptions are truncated at the end, and Vargas is told not to infer facts from omitted text. The default can be changed in `.env`.
* Each Hermes attempt adds a privacy-preserving row to the SQLite `ai_usage` table with model label, prompt/response character counts, token estimates, outcome and error type. The estimates use 4 characters per token and are not provider billing totals; see [`docs/uso-economia-tokens.md`](docs/uso-economia-tokens.md).


4. **Deterministic Python Scoring:**
* Python computes the final score using a strict formula:

$$\text{Total Score} = \text{Role Match (35)} + \text{Skills (25)} + \text{Seniority (15)} + \text{Location (15)} + \text{English (10)}$$


* Location eligibility is decided in Python from an exact quote that must also occur in the ingested job text. Explicitly incompatible locations are rejected; missing or ambiguous location evidence forces `MAYBE`.
* The complete AI response is validated against the expected schema, enum values, types and exact job-text citations. Invalid, incomplete or malformed evaluations stay pending and do not create Telegram alerts. URL-only inbox files are rejected before calling the AI.
* Every due `NEW` job is evaluated without a per-run count limit. Jobs and Telegram notifications are atomically claimed in SQLite to prevent overlapping pipeline runs from processing the same item; expired claims are recovered, and failed evaluations retry with exponential backoff. On a recognized AI quota/rate-limit response, the current job waits for `AI_QUOTA_RETRY_SECONDS` (default: 86400) and the pipeline stops sending additional evaluations during that run.
* Hermes evaluation uses `HERMES_TIMEOUT_SECONDS` (default: 1800 seconds); IMAP operations use `IMAP_TIMEOUT_SECONDS` (default: 120 seconds). Telegram delivery failures retry with exponential backoff. A crash after Telegram accepts a message but before Vargas records confirmation can still result in a duplicate after lease recovery.
* Private location allowlists are configured with `ALLOWED_REMOTE_COUNTRIES` and `ALLOWED_ONSITE_LOCATIONS` in `.env`. Separate multiple values with semicolons. Keep personal location details out of the public repository.
* Automatically triggers Hard Rejects if the job requires incompatible country residency, mandatory native language fluency, or excessive seniority.

5. **Telegram Notifications:**
* Stores recommended alerts in a SQLite outbox in the same transaction as the evaluation. Alerts use Telegram `sendMessage`; failed deliveries remain pending and are retried with exponential backoff.



---

##  Security & Engineering Highlights

* **Decoupled Responsibilities:** The LLM is used strictly for natural language understanding and fact extraction. Numerical scoring and final verdicts are 100% governed by deterministic Python code.
* **Malformed JSON Handling:** Invalid or excessively nested model output is treated as a failed evaluation; corrupt legacy evaluations are retired without aborting the database migration. Evaluation validation remains strict: `hard_reject_flags` must use the exact documented enum codes, and `location_evidence` / requirement quotes must occur literally in the job text. Failure diagnostics report the invalid flag or rejected evidence without weakening these checks. Hermes non-zero exits and timeouts include bounded stdout/stderr excerpts to help diagnose the container/profile runtime.
* **AI call diagnostics:** `ai_usage` stores character counts, token estimates, a compact categorical result summary, and bounded error details for failed Hermes attempts. It does not store the job text or response stdout. Provider usage dashboards remain the source of truth for billed tokens; see [`docs/uso-economia-tokens.md`](docs/uso-economia-tokens.md).
* **Prompt Injection Protection:** Job description text is isolated as untrusted data between `BEGIN` and `END` blocks. If prompt injection attempts are detected within a job listing, the system records `prompt_injection_detected: true` and downgrades the job.
* **Anonymous Ingestion:** No personal LinkedIn cookies or session tokens are used during job scraping.
* **Environment Isolation:** Secrets, tokens, and API keys are stored in an untracked `.env` file.

---

##  Repository Structure

```text
job-agent/
├── .env.example          # Generic environment variables template
├── .gitignore            # Git ignore rules (database, logs, .env)
├── LICENSE               # MIT Open Source License
├── README.md             # Technical project documentation
├── docs/
│   └── uso-economia-tokens.md # AI usage estimates, queries and filter guidance
├── requirements.txt      # Locked Python dependencies
├── scripts/
│   └── validate.py       # Syntax check and complete offline test suite
├── add_linkedin.py       # Terminal interactive CLI for manual job ingestion
├── add_remot.py          # Manual description ingestion for saved Remōt alerts
├── collector.py          # RSS feed collector script
├── jobspresso_collector.py # Jobspresso RSS-only collector
├── remotive_collector.py  # Remotive RSS-only collector
├── bairesdev_collector.py # BairesDev public careers-page collector
├── email_collector.py    # IMAP reader and anonymous LinkedIn scraper
├── remot_parser.py       # Conservative Remōt alert-link parser
├── list_jobs.py          # Terminal viewer for recommended jobs
├── location_policy.py    # Deterministic private location eligibility rules
├── run_pipeline.py       # Main pipeline orchestrator
├── store.py              # SQLite persistence and database module
├── telegram_notify.py    # Telegram Bot notification module
├── wrapper.py            # Docker executor and Python deterministic scoring engine
├── tests/                # Automated pipeline and feature tests
└── templates/            # Generic agent rule templates
    ├── candidate.md      # Candidate profile template
    ├── criteria.md       # ASD-STE100 criteria and extraction rules
    └── SOUL.md           # AI persona guidelines and security rules

```

---

##  Getting Started

### 1. Prerequisites

* Python 3.10+
* Docker running the `hermes` container
* A Telegram account and a Telegram Bot token (via `@BotFather`)

### 2. Installation & Configuration

1. **Clone the repository:**
```bash
git clone https://github.com/your-username/job-agent.git
cd job-agent

```


2. **Create and activate a Python virtual environment:**

Windows PowerShell:
```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Linux/macOS:
```bash
python3 -m venv .venv
source .venv/bin/activate

```


3. **Install dependencies:**
```bash
python -m pip install -r requirements.txt

```


4. **Set up environment variables:**
```bash
cp .env.example .env
nano .env

```


Fill in your Telegram Bot token, Chat ID, and IMAP email credentials.
Set the private location allowlists in `.env`, for example by listing the countries accepted for remote work and the cities accepted for on-site or hybrid work. Do not commit these personal settings.
The optional Remotive and Jobspresso source filters are also configured only in
this local `.env`; their values are blank in `.env.example`, so the collectors
remain unfiltered by default. These source filters do not replace or modify the
private Vargas profile. The versioned `templates/candidate.md` and
`templates/criteria.md` are generic starting points; keep personalized copies
in the private active Vargas profile, not in the public repository.
5. **Configure Agent Files:**
Copy the template files from `templates/` into your Hermes profile directory and customize `candidate.md` with your real background.
Keep the real candidate profile in the private active Vargas profile. Do not copy personal candidate data into this public repository.

### 3. Running the Pipeline

To run the complete pipeline manually:

```bash
python run_pipeline.py

```

The pipeline continues with other work after a stage fails, but exits with a non-zero status and prints the failed stages. This allows cron or a service monitor to detect incomplete runs. IMAP collection is reported as skipped when both email credentials are absent. Jobs waiting for their backoff interval remain persisted and are retried on subsequent runs.

To view all approved (`EVALUATED_APPLY`) jobs in your terminal:

```bash
python list_jobs.py

```

### 4. Ambiente e Validação dos Testes

O projeto usa o `unittest` que já vem com o Python; não é preciso instalar
ferramenta de testes adicional. No PowerShell, a partir da pasta do repositório,
crie o ambiente virtual (somente na primeira vez), instale as dependências e
rode a validação:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\validate.py
```

Nas próximas execuções, basta repetir:

```powershell
.\.venv\Scripts\python.exe scripts\validate.py
```

No Linux/macOS, use:

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python scripts/validate.py
```

O script verifica a sintaxe de todos os arquivos Python do projeto, confirma
que cada módulo Python principal tem arquivo de teste associado e executa
toda a suíte em `tests/`. Os testes cobrem RSS, cadastro manual, leitura de
e-mail, políticas de URL/localização, persistência e retentativas SQLite,
avaliação/pontuação, cartas, notificações, listagem e orquestração do pipeline.
Chamadas de Docker/Hermes, IMAP, HTTP e Telegram são simuladas; a validação não
usa credenciais nem acessa esses serviços. Ela detecta regressões cobertas
pelos testes, mas não garante funcionamento perfeito em produção nem substitui
um teste operacional controlado com o ambiente do usuário.

### 5. Automated Daily Execution (`cron`)

To schedule daily automated runs on your Linux server (e.g., twice daily at 07:00 and 19:00):

```bash
crontab -e

```

Add the following schedule:

```cron
0 10,22 * * * cd /path/to/job-agent && ./.venv/bin/python run_pipeline.py >> pipeline.log 2>&1

```

---

##  License

This project is licensed under the MIT License - see the [LICENSE](https://www.google.com/search?q=LICENSE) file for details.
