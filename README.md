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
4. **Real-Time Alerts:** Sends formatted Telegram notifications for recommended openings, with a cover-letter draft attached for `APPLY` jobs.

---

##  System Architecture

```text
 ┌────────────────────────┐      ┌─────────────────────────┐
 │   Email Job Alerts     │      │   Job RSS Feed Source   │
 │ (LinkedIn / Remōt)     │      │   (We Work Remotely)    │
 └───────────┬────────────┘      └────────────┬────────────┘
             │ IMAP                           │ HTTP / RSS
             ▼                                ▼
  ┌─────────────────────┐          ┌─────────────────────┐
  │ email_collector.py  │          │    collector.py     │
  │ (Anonymous Scraping)│          │  (Keyword Filtering)│
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
                              │ APPLY: Draft letter
                              ▼
                  ┌──────────────────────┐
                  │  cover_letter.py     │ ◄── Private candidate profile
                  │  (Draft only)        │
                  └───────────┬──────────┘
                              │ APPLY draft + APPLY/MAYBE alert
                              ▼
                  ┌──────────────────────┐
                  │ telegram_notify.py   │
                  │ (Alert + .txt file)  │
                  └──────────────────────┘

```

---

##  How the Pipeline Works

1. **Multi-Source & Anonymous Ingestion:**
* **`collector.py`**: Ingests public RSS feeds (e.g., We Work Remotely), applying inclusion/exclusion lists and text normalization.
* **`email_collector.py`**: Connects via IMAP to process unread LinkedIn and Remōt alert emails. LinkedIn URLs retain the existing anonymous public HTTPS scraping flow (without cookies or user login); redirects outside LinkedIn are rejected. Remōt links are accepted only from HTTPS `workremot.com` hosts (including its subdomains), normalized, and saved to SQLite. No Remōt page or external application URL is fetched. Emails are marked as read only after a supported listing URL is durably stored.
* **Remōt alert format and description limit:** The repository does not contain a real Remōt alert sample or a verified email template. Its parser therefore finds official listing URLs in generic HTML or plain-text messages rather than assuming a specific sender or markup. Title/link-only alerts are stored as `DESCRIPTION_REQUIRED`, not placed in the evaluation inbox, and never sent to Vargas without job-description text. External links labeled as application links are stored separately for transparency when one listing and one application URL are unambiguous. Add the full job description (at least 100 characters and 15 words) with `python add_remot.py`; this promotes the saved alert to `NEW` and creates its inbox file. The Remōt alert's search is IMAP `UNSEEN BODY workremot.com`; providers that do not support standard IMAP `BODY` search may require a provider-specific query change.
* The public listing's location and workplace-type metadata are appended to the job text when available. This gives the AI evidence to cite without granting it Internet access.
* The RSS collector accepts only We Work Remotely URLs. Links whose public pages are blocked, rate-limited, or lack a usable description stay in a `SCRAPE_PENDING` queue. Extraction tries the page description and structured `JobPosting` data; results need a minimum usable text length. Transient failures retry on later runs with exponential backoff. Confirmed HTTP 404/410 responses become `SCRAPE_UNAVAILABLE` and are no longer retried. Entries older than `SCRAPE_MAX_AGE_DAYS` (default: 30) are also retired, since old job postings are unlikely to remain actionable. There is no per-run job count limit.
* Schema upgrades do not backfill historical evaluated jobs into the scraping queue. A one-time repair restores their saved evaluation status and retires old unresolved entries (including legacy rows with at least three failed scrape attempts, missing/invalid dates, or age beyond the configured window) instead of triggering large batches of old requests. Newer transient failures continue retrying as before.


2. **Persistence & Storage:**
* Records job entries in an SQLite database (`jobs.db`) using a unique SHA-256 fingerprint (`company:title`) to prevent duplicate evaluations.
* Saves raw pending job descriptions into the `inbox/` directory.


3. **Factual AI Screening:**
* **`wrapper.py`** triggers the local Hermes Agent Docker container.
* The LLM parses `SOUL.md` (persona), `criteria.md` (ASD-STE100 extraction rules), and `candidate.md` (anonymized candidate profile) to produce a structured JSON factual breakdown.


4. **Deterministic Python Scoring:**
* Python computes the final score using a strict formula:

$$\text{Total Score} = \text{Role Match (35)} + \text{Skills (25)} + \text{Seniority (15)} + \text{Location (15)} + \text{English (10)}$$


* Location eligibility is decided in Python from an exact quote that must also occur in the ingested job text. Explicitly incompatible locations are rejected; missing or ambiguous location evidence forces `MAYBE`.
* The complete AI response is validated against the expected schema, enum values, types and exact job-text citations. Invalid, incomplete or malformed evaluations stay pending and do not create Telegram alerts. URL-only inbox files are rejected before calling the AI.
* Every due `NEW` job is evaluated without a per-run limit. Jobs and Telegram notifications are atomically claimed in SQLite to prevent overlapping pipeline runs from processing the same item; expired claims are recovered, and failed/incomplete evaluations retry with exponential backoff and no maximum attempt count.
* Hermes evaluation and cover-letter generation use `HERMES_TIMEOUT_SECONDS` (default: 1800 seconds); IMAP operations use `IMAP_TIMEOUT_SECONDS` (default: 120 seconds). Telegram delivery failures also retry with exponential backoff; confirmed message/document stages are not sent again. A crash after Telegram accepts a message but before Vargas records confirmation can still result in a duplicate after lease recovery.
* Private location allowlists are configured with `ALLOWED_REMOTE_COUNTRIES` and `ALLOWED_ONSITE_LOCATIONS` in `.env`. Separate multiple values with semicolons. Keep personal location details out of the public repository.
* Automatically triggers Hard Rejects if the job requires incompatible country residency, mandatory native language fluency, or excessive seniority.

5. **Cover Letter Drafts:**
* For every notified `APPLY` or `MAYBE` job, Vargas creates a job-specific cover letter in the language of the job posting using facts from the private candidate profile and the job description.
* Drafts use placeholder contact details, are saved under the ignored `cover_letters/` directory, and are sent as a `.txt` document immediately after the full Telegram text alert.
* Vargas prepares drafts only. It never submits an application or sends a message to an employer.

6. **Telegram Notifications:**
* Stores recommended alerts in a SQLite outbox in the same transaction as the evaluation. The full alert uses Telegram `sendMessage`; the cover-letter file is sent separately so Telegram caption limits cannot truncate the message. Failed delivery stages remain pending and are retried without resending stages already acknowledged by Telegram.



---

##  Security & Engineering Highlights

* **Decoupled Responsibilities:** The LLM is used strictly for natural language understanding and fact extraction. Numerical scoring and final verdicts are 100% governed by deterministic Python code.
* **Malformed JSON Handling:** Invalid or excessively nested model output is treated as a failed evaluation or draft; corrupt legacy evaluations are retired without aborting the database migration.
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
├── requirements.txt      # Locked Python dependencies
├── scripts/
│   └── validate.py       # Syntax check and complete offline test suite
├── add_linkedin.py       # Terminal interactive CLI for manual job ingestion
├── add_remot.py          # Manual description ingestion for saved Remōt alerts
├── collector.py          # RSS feed collector script
├── cover_letter.py       # Vargas cover-letter draft generator for APPLY jobs
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
5. **Configure Agent Files:**
Copy the template files from `templates/` into your Hermes profile directory and customize `candidate.md` with your real background.
Before using cover-letter generation, synchronize the updated `templates/SOUL.md` with the private active Vargas profile. Follow your existing backup, hash-verification, and immutable-file procedure. Keep the real candidate profile and generated `cover_letters/` drafts private.

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
