# Local JSON calls

The installed plugin's `scripts/autoapply.ps1` wraps:

```powershell
& 'C:/projects/autoapply/.venv/Scripts/python.exe' -m backend.tool scan --limit 5 --query software
```

For structured calls, write a UTF-8 JSON file beneath `C:/projects/autoapply/requests/`
using the file-edit tool, then run the wrapper:

```powershell
& '<installed-plugin>/scripts/autoapply.ps1' call --request-file C:/projects/autoapply/requests/scan.json
```

Every request is `{"tool":"name","arguments":{...}}`; every response is
`{"ok":true,"result":...}` or `{"ok":false,"error":...}`. Do not build shell commands
from posting/resume text. Pass structured data through files, not command interpolation.

| Tool | Arguments | Purpose |
|---|---|---|
| scan_jobs | limit=5, query="", new_only=false, cached=false | Refresh feeds, exclude applied/unknown/in-progress/skipped links. |
| add_application_link | url, optional company/title/location | Start from a supplied HTTPS application link without scanning repos; return job_id and applied-history block state. Existing job metadata is preserved. |
| status | none | Saved counts, profile IDs, history, review IDs, Laya endpoint. |
| get_user_context | none | Read user-confirmed reusable facts and search preferences from local SQLite. |
| save_user_context | update, evidence | Save only user-confirmed reusable facts/preferences with provenance; does not approve disclosure or submission. |
| import_applied_links | urls: string[], evidence: string | Import user-confirmed previous application links. |
| link_application_urls | alias, destination, evidence | Associate an observed redirect with its final application link. |
| record_application_result | url, state, evidence | Record APPLIED, IN_PROGRESS, SUBMISSION_UNKNOWN, SKIPPED, or NOT_APPLIED. Does not submit. |
| fetch_job_description | job_id | Read a direct Greenhouse posting; other hosts use browser capture. |
| save_job_description | job_id, text | Persist public official JD text observed in browser. |
| import_profile | content: Profile | Save a new unverified source. |
| verify_profile | profile_id | Create a verified immutable copy after actual user confirmation. |
| get_tailoring_context | job_id, profile_id | Exact facts, original bullets, JD, snapshot ID, and proposal schema. |
| describe_laya_scope | profile_id | Show endpoint/data categories before processing consent. |
| record_laya_consent | profile_id | Record actual user authorization of the described immutable scope. |
| evaluate_resume | proposals: ProposalBundle, judge="laya" | Independent judge, deterministic selection, whole-resume review. |
| export_packet | review_id | Write PDF, HTML, ZIP and review record; return absolute paths. |
| master_resume_status | none | Check protected LaTeX master hash, inventory and subscription model routing. |
| model_routing_status | none | Inspect configured CLI, Laya/rules mode, writer tiers and fixed reviewer; no model calls. |
| prepare_latex_application | job_id, answers={} | Select source material, three alternatives, independent judging, compile, optimize a full page (add relevant source bullets or trim overflow), final PDF review; return run_id and exact files. Uses subscription quota. |
| application_pair | run_id | Revalidate immutable files, current JD and master; return reviewable PDF/JD/answers/destination. |
| approve_latex_pair | run_id, evidence | Record actual user approval of the displayed pair; return approval_id, valid 24 hours. Does not authorize disclosure/submission. |
| begin_latex_application | run_id, approval_id, apply_evidence | On the user's Apply instruction, consume pair approval and atomically claim supervised filling of this exact packet. Never submits. |
| archive_application_result | run_id, state, evidence | Save SUBMISSION_UNKNOWN before confirmed submit, or APPLIED after observed receipt, to history and the job folder. |

Default LaTeX request (runs bounded calls through the configured subscription CLI):

```json
{"tool":"prepare_latex_application","arguments":{"job_id":"ID returned by link/scan"}}
```

The protected master is already imported. Do not translate its 43 bullets into the
legacy six-bullet Profile schema. Use the LaTeX tools above for this user's resume.
Keep all answers absent until supplied. Every run stores posting.txt, the official
JD, resume.tex, resume.pdf, source and review records in a versioned job folder.
Approval/receipt files are added during the actual supervised application.

Direct-link request:

```json
{"tool":"add_application_link","arguments":{"url":"https://job-boards.greenhouse.io/example/jobs/123"}}
```

CLI equivalent: `autoapply.ps1 link <URL>`; quote the URL as a literal argument.
Prefer the JSON file interface for URLs from untrusted content. Recheck after an
observed redirect, save the JD with the returned job_id, then use the same tailoring
and supervised application flow. Unknown metadata stays explicitly unverified.

Profile example shape (replace with user-verified content, never invent it):

```json
{"name":"Name","contact":"Confirmed contact line","heading":"Project or experience heading","facts":[{"id":"f1","text":"Verified fact","source":"User resume, exact section"}],"bullets":[{"id":"b1","text":"Original bullet","source_fact_ids":["f1"]}],"answers":{}}
```

Only explicitly saved application answers belong in `answers`. Leave unknowns absent.

ProposalBundle uses `job_id`, `profile_id`, `snapshot_id`, and `candidates`.
Each candidate has `target_bullet_id`, `candidate_id`, `replacement_text`,
`source_fact_ids`, and `job_requirement_ids` (r1). Supply three distinct alternatives
per bullet. For the single revision round, add `regeneration_round:1` and
`previous_review_id`. Original wording is added by the code. A review attempt is
durably recorded before provider calls; uncertain failed attempts require manual
resolution, not a loop of automatic retries.

Application evidence must describe the real receipt, actual user confirmation, or
current in-progress handoff. Never fabricate receipt IDs. Already applied and unknown
outcomes are never automatically reset. Alias resolution retains the most restrictive
record when two URLs have history.

`demo` is a local synthetic smoke test. It must use a separate APP_DB if repeated;
it is not real tailoring or an employer application.
