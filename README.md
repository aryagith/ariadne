# Internship discovery, resumes, outreach, and application tracking

This is a local assistant workflow. It finds internships, prepares resumes with Sol and available Laya decision support, drafts outreach, and keeps application records. It does not use computer/browser automation, submit applications, or send messages. Current agent rules are in [AGENTS.md](AGENTS.md).

## Quickstart (Windows, fresh clone)

Install Python 3.12 and PowerShell. Clone the repository, then run these commands from its root in PowerShell (replace the URL with the actual GitHub URL):

```powershell
git clone https://github.com/OWNER/REPO.git
Set-Location REPO
py -3.12 -m venv .venv
& './.venv/Scripts/python.exe' -m pip install -r requirements.txt
& './scripts/sync-job-tracker.ps1'
& './scripts/check-jobs.ps1'
```

Open `job apps/APPLICATIONS.html` after the sync. A fresh clone starts with an empty tracker; the repository check fetches the three configured GitHub feeds and saves results under `data/job-watch/`. It does **not** search LinkedIn or Glassdoor: an assistant with public web search/fetch access must do that separately. Source access or network restrictions may cause the check to report an error.

Before asking any AI agent to find or tailor jobs, read [AGENTS.md](AGENTS.md) and replace its owner-specific search preferences, work authorization, and availability with your own verified facts. The original user's resume, job history, and credentials are intentionally excluded from Git. Keep your own `master resume/`, `job apps/`, `data/`, and `.env` private. Do not copy someone else's application history or claim its status as yours.

Resume tailoring needs your own compatible LaTeX resume at `master resume/master.tex` and a `master resume/source.json` containing its SHA-256 hash and your identity. The parser expects the template's `\resumeSubheading` / `\resumeProjectHeading` structure; an arbitrary `.tex` resume will need adaptation. For example, after placing your own compatible `master.tex`:

```powershell
$masterHash = (Get-FileHash 'master resume/master.tex' -Algorithm SHA256).Hash.ToLowerInvariant()
$masterSource = @{ sha256 = $masterHash; source = 'User-provided resume'; protected = $true; target_pages = 1; identity = 'Your Name' } | ConvertTo-Json
[System.IO.File]::WriteAllText((Join-Path (Get-Location) 'master resume/source.json'), $masterSource, [System.Text.UTF8Encoding]::new($false))
```

Automatic tailoring currently calls a **ChatGPT-authenticated Codex CLI** with access to `gpt-6-sol`; using Claude Code as the agent does not replace that CLI or model. PDF checks also require Tectonic, Python with PDF inspection dependencies, and Poppler; set `AUTOAPPLY_TECTONIC`, `AUTOAPPLY_PDF_PYTHON`, and `AUTOAPPLY_PDFTOPPM` to their executable paths if the Codex bundled runtime is unavailable. Laya is optional decision support. See [automatic tailoring](docs/automatic-tailoring.md) for the verified-JD and quality-gate workflow. The hourly Codex automation belongs to the original user's local app and is **not** installed by cloning; configure your own schedule only after the manual check and tailoring prerequisites work.

Optional dashboard: install Node.js/npm, then run `& './scripts/start.ps1'`. It installs the web dependencies, builds the dashboard, and serves it on `http://127.0.0.1:8000`; the local passphrase is in `data/auth-token.txt` unless `APP_PASSWORD` is set. The `job apps/APPLICATIONS.html` tracker above does not need the dashboard.

## Start here

After running the quickstart sync, open **[job apps/APPLICATIONS.html](job%20apps/APPLICATIONS.html)** in your browser. Search by company, role, location, status, or notes. Each row links to the official posting, job information, status file, and document folder. This is a local generated report, not a hosted website.

Each saved job has a stable folder named `Company-Posting-JobID`. Existing job/version folders are reused, not renamed or deleted.

```text
job apps/
  APPLICATIONS.html                  Searchable overview of every saved job
  TRACKER-ERRORS.json                 Status-file errors that need correction
  Company-Posting-JobID/
    STATUS.txt                       YOU CAN EDIT: status, posting date, notes
    JOB.md                           Generated summary, links, history, outreach
    job-description.txt              Latest saved official JD, when available
    drafts/                          Working resume, cover letter, answers, etc.
    submitted/
      ArchiveTimestamp-Fingerprint/
        01-resume.pdf                Exact copy confirmed as submitted
        02-cover-letter.pdf          Other submitted files, when supplied
        manifest.json                File hashes, evidence, dates, posting link
    ExistingVersionID/               Older resume runs retained as they were
```

The overview includes all saved jobs, including backlog and potentially closed listings. Being in the tracker does not certify that a job is still open or that you are eligible. Check the official posting before tailoring/applying. Do not assume a generated draft or an old resume-version folder was submitted.

## Find jobs and receive alerts

The hourly Codex automation **Canada and sponsored internship alerts** runs in **Resume New Posting Alert**. Luna runs the local GitHub checker and searches public LinkedIn and Glassdoor listings. It verifies new relevant jobs, then calls **GPT-6 Sol high** to tailor a full one-page resume and check the PDF. Ready alerts include the official application link, PDF and editable source. Keep the PC awake and Codex running for local scheduled work. The PowerShell script fetches GitHub feeds; public web search is performed by the scheduled assistant. See [automatic tailoring](docs/automatic-tailoring.md) for the queue, model pin and quality gates.

Sources:

Scheduled repository checks run `scripts/check-jobs.ps1` from this checkout. The original Codex installation has a local permission rule for that command; a clone needs its own shell/network access. Public LinkedIn/Glassdoor searches use the assistant's web search/fetch tool every run. A Windows socket error 10013 means network execution is restricted; report the failure rather than treating cached listings as a fresh scan.

- SimplifyJobs/Summer2027-Internships: `README.md` and `README-Off-Season.md` on `dev`.
- zapplyjobs/Internships-2027: `README.md` on `main`.
- Public LinkedIn and Glassdoor results, verified against employer careers/ATS pages.

Prioritize Canadian technical internships and foreign roles with explicit immigration support. The user confirmed a Canadian co-op work permit and no US work authorization. Permit dates and job-specific eligibility remain to be checked. Four months is preferred; eight months is conditional on chaining with summer. Missing sponsorship language is not evidence of support.

Manual repository check, from `C:/projects/autoapply`:

```powershell
& './scripts/check-jobs.ps1'
```

This refreshes the tracker and saves the scan under `data/job-watch/`. The watcher tracks changes independently of manual scans. Public-board leads must be registered through `add_application_link` using their official URL; observed aliases should be linked with `link_application_urls`. Do not deduplicate distinct requisitions based only on similar titles. Failed/inaccessible sources are reported, not represented as empty successful scans.

## Update an application's status

Open `job apps/APPLICATIONS.html` in Chrome or Edge. Click **Select job apps folder**, select this project's `job apps` directory, and allow editing when the browser asks. Click **Edit status / posted** on a row, choose its status, enter its posting date and source/evidence, edit its notes, and click **Save**. The save writes that job's `STATUS.txt` immediately and updates the visible row. Notes support multiple lines and can be cleared. Backend records and the generated overview update on the next sync/hourly check; refreshing before that check shows the last generated view. Reopen Edit to read the current saved file. Select the folder again after reopening the page. No server or login is needed. Browsers without folder-write support can use the text-file method below.

Alternatively, open its `STATUS.txt` in Notepad or another text editor. Keep the labels; change the values and add notes below `Notes:`.

```text
Status: SHORTLISTED
Posted: UNKNOWN
Posted source: UNKNOWN
Notes:
Interested in the four-month Toronto placement. Check the deadline.
```

Statuses: `DISCOVERED`, `SHORTLISTED`, `DRAFTED`, `READY`, `IN_PROGRESS`, `APPLIED`, `OA`, `INTERVIEW`, `OFFER`, `REJECTED`, `WITHDRAWN`, `SKIPPED`, `SUBMISSION_UNKNOWN`.

Use `APPLIED` only after actually submitting. `OA`, `INTERVIEW`, `OFFER`, `REJECTED`, and `WITHDRAWN` also imply a prior application. Use `SKIPPED` for a role you chose not to apply to. `SUBMISSION_UNKNOWN` means the outcome is uncertain; do not retry automatically. Terminal application history is retained even if the visible stage advances.

Save the file and run:

```powershell
& './scripts/sync-job-tracker.ps1'
```

Or ask the assistant to update the status; it uses `update_job_tracking`. The next hourly check also imports edits. Refresh the HTML page afterward. `JOB.md` and `APPLICATIONS.html` are generated; use the HTML editor or edit `STATUS.txt`, rather than changing the HTML source. Invalid edits are left untouched and listed in `TRACKER-ERRORS.json`. A previously applied/unknown job cannot silently be reset to an unapplied state. The HTML editor detects changes made to the file since opening it and asks you to reopen before saving.

## Posting dates and timestamps

`Posted` is the employer's actual posting date/time, not the scan time. Use `YYYY-MM-DD` if only a date is known, or an ISO timestamp with timezone such as `2026-09-28T14:00:00-04:00`. Supply the source URL and date-field evidence in `Posted source`. Leave unknown values as `UNKNOWN`; do not invent a time, timezone, or date from a search crawl timestamp. Relative labels such as “3 days ago” may be noted as unverified evidence in Notes.

First discovered is displayed in Toronto local time with AM/PM and EST/EDT (for example, Sep 28, 2026, 6:27 PM EDT), automatically accounting for daylight saving time. Stored timestamps and last listing change remain UTC. Submission time is stored only when supplied with evidence. The submitted folder's timestamp is the time of archival, not proof of when the application was sent.

## Tailor and store documents

New relevant jobs are tailored automatically after official-JD verification; you can also ask for a specific saved job. Start from the protected `master resume/master.tex`. Automatic tailoring pins Sol to high reasoning through `tailor_alert_resume`, with existing Laya decision support when available. Use natural STAR/XYZ achievement bullets and supported JD skill keywords. No separate writer/reviewer models or mandatory alternative tournaments. The older multi-model preparation commands are not the current agent workflow.

Save working documents under that job's `drafts/` in a dated subfolder. Keep the master unchanged. Fill one readable page (94-100% of printable height) with verified facts and ATS-friendly formatting, and inspect both the PDF and extracted text. A ready automatic result requires the local layout checks and Sol's visual/content self-check to pass. These checks are not an ATS score or an interview guarantee. Deliver the PDF, editable source, official link, and a short selection summary.

After you manually apply, tell the assistant which exact files you submitted and provide your confirmation/receipt. It uses `archive_submitted_documents` to copy those files into `submitted/`, record hashes and confirmation evidence, and set application history to APPLIED. Later edits to working files cannot change these copies. Repeating an identical archive request reuses the existing archive. Never edit files in `submitted/`; corrected/new submissions get a new archive.

No documents are uploaded by this command. If exact submitted files are unavailable, record APPLIED with a note explaining the missing documents; do not substitute a newer resume or guess which draft was used. Existing confirmed historical receipts can be imported after verifying the corresponding saved file hashes.

## Outreach

Ask for outreach to a particular company/role. Use public professional sources to find suitable recruiters or team members, verify their current company/role, and retain evidence. Do not guess emails or claim a relationship/referral. Sol drafts a short email and LinkedIn message using supported resume facts. [Outreach tool documentation](docs/outreach.md) describes the draft and history commands. Each job summary links its saved outreach drafts.

Review and send manually. Record SENT/REPLIED/CLOSED only from your confirmation or observed evidence. Hourly discovery never sends outreach or generates automated follow-ups.

## Local JSON tools

Write a UTF-8 request under `requests/`, then run:

```powershell
& './.venv/Scripts/python.exe' -m backend.tool call --request-file requests/my-request.json
```

Example status update:

```json
{"tool":"update_job_tracking","arguments":{"job_id":"SAVED_JOB_ID","status":"INTERVIEW","notes":"Recruiter confirmed an interview; details in my calendar."}}
```

Example archive request (replace every placeholder with the actual files and confirmation):

```json
{"tool":"archive_submitted_documents","arguments":{"job_id":"SAVED_JOB_ID","files":["C:/projects/autoapply/job apps/EXACT-JOB-FOLDER/drafts/resume.pdf"],"evidence":"Actual user confirmation or receipt reference","submitted_at":"2026-09-28T14:00:00-04:00"}}
```

Omit `submitted_at` if unknown. `sync_job_tracker` refreshes the complete folder/index view. Existing `add_application_link`, `save_job_description`, `get_user_context`, and outreach commands remain available. CLI scans and command calls sync the tracker; scheduled repository checks sync before and after discovery. Other direct backend integrations should call `sync_job_tracker` after changing job/history data.

State lives in `data/app.sqlite` by default; documents live in `job apps/`. Back up both together, plus `master resume/` and `outreach/`. Keep private files and credentials out of source control. Do not delete old submission evidence or overwrite unrelated user files.

## Verification and historical implementation

Run focused tests with:

```powershell
& './.venv/Scripts/python.exe' -m pytest tests/test_job_tracker.py tests/test_job_watch.py tests/test_outreach.py tests/test_tool.py -q
node --test tests/tracker_editor.test.cjs
```

Tests use synthetic data and temporary folders; they do not send messages, apply to jobs, or spend on models. Live source access is checked separately. [Historical setup and older workflow details](docs/legacy-setup-and-workflow.md) are retained for reference and do not override the current scope in AGENTS.md.
