# Hourly discovery and Sol resume tailoring

The user authorized this workflow on September 28, 2026. The heartbeat remains in
**Resume New Posting Alert**, with Luna doing discovery and orchestration.
All resume selection, writing, skill ranking, revision and visual/content checking
runs through `tailor_alert_resume`, pinned to `gpt-6-sol` with **high** reasoning.
It uses the existing Codex CLI with ChatGPT authentication, never paid API-key
fallback. Laya sees task metadata only and cannot change the model pin.

## Each hourly run

1. Run the existing repository check and search public LinkedIn and Glassdoor as
   specified in AGENTS.md. Read `data/job-watch/latest.json` immediately after the
   command. Preserve genuinely new/materially changed candidates in
   `data/job-watch/tailor-queue.json` **before** expensive verification or tailoring.
   This matters because the repository watcher advances its seen checkpoint.
   Do not initialize this queue from the entire historical job database.
2. Maintain this JSON ledger as an object with an `items` array. Each item records
   job ID/official URL, discovery source and change evidence, state (`pending`,
   `ready`, `blocked`, `failed`), snapshot ID when known, result-file path when
   available, and the last announced result/failure fingerprint. Preserve all
   existing entries. Write through a temporary file and replace the ledger only
   after serialization succeeds. Reconcile aliases by official URL/requisition ID.
   Resume pending items on the next run even when the discovery report is empty.
3. Verify each candidate against its live official careers/ATS JD. Prioritize
   Canada and explicitly supported immigration roles; check term, availability and
   saved history. Skip known mismatches and applied/skipped/in-progress/unknown
   submissions. Record unresolved permit dates or other eligibility conditions;
   a prepared resume is not proof of eligibility. For inaccessible JDs, mark the
   item blocked and request text/a working link once. Never tailor search snippets.
4. Register official URLs with `add_application_link`, link observed aliases,
   save the complete JD with `save_job_description`, and retain its snapshot ID.
   Update observed posting dates with evidence, otherwise keep UNKNOWN. Sync the
   tracker. Save verification notes with the official URL, observed availability,
   location, term and eligibility evidence/uncertainty.
5. Write a UTF-8 tool request and invoke the existing CLI:

   ```json
   {"tool":"tailor_alert_resume","arguments":{"job_id":"ACTUAL_JOB_ID","snapshot_id":"ACTUAL_SNAPSHOT_ID","verification":"Official URL and observed availability, location, term, eligibility evidence and remaining uncertainty."}}
   ```

   ```powershell
   & './.venv/Scripts/python.exe' -m backend.tool call --request-file requests/tailor-ACTUAL_JOB_ID.json
   ```

   Use the shell's supported `require_escalated` approval flow for this command:
   the live CLI connectivity check required access to Codex's authentication/temp
   directories and the network outside the shell sandbox. Never bypass denial.
   Do not rewrite resume text in Luna, call legacy `prepare_latex_application`,
   spawn writer/reviewer agents, change models, or use API-key fallback.
6. Read the actual returned result. `state=READY` is the only finished resume
   state; `MOCKED`, `FAILED`, `RUNNING`, or `NEEDS_ATTENTION` must never be presented
   as ready. Update the queue with the result and retain the saved files. For a
   failure, record the diagnostic once and continue other eligible jobs. Do not
   reset failed attempts or rerun the same input every hour. Resume only after a
   meaningful input/environment change and explicit inspection of the prior run.
7. Alert in the scheduled chat when a new resume is ready, with company/title,
   location and known term, the **official application link**, absolute clickable
   **PDF**, **editable .tex** and **interview_notes** paths returned by the tool, a short selection
   summary, and any eligibility uncertainty or unavailable Laya support. Present
   this as a prepared draft for the user to inspect and manually apply with.
   Record the announced result fingerprint. Never claim ATS certification,
   independent approval, eligibility certainty or application submission.
8. If a run must end with pending work, retain the pending items for the next
   scheduled run. Stay quiet for unchanged jobs, unchanged failures and previously
   announced ready drafts. Keep `web-seen.json` for discovery deduplication;
   discovery-seen and resume-ready are separate states.

## Resume requirements and saved evidence

- Protected master facts only. Natural STAR/Google XYZ bullets with supported
  actions, implementation details and outcomes; metrics only when documented.
- Sol also writes `INTERVIEW_PREP.md` for the exact posting/resume: metric evidence
  with source bullet IDs, supported personal contributions and implementation,
  likely technical questions, and a Things to know before interview checklist.
  Unknown measurement methods/baselines/attribution remain questions to confirm.
  Proposed estimates stay in a labeled Needs confirmation section with a formula
  and missing inputs; do not guess numbers or insert unconfirmed estimates in the
  resume. Practice explanations must not invent experience or measurement stories.
  The notes are included in Sol's self-check and linked from tracker notes and alerts.
- Strongest relevant experience, relevant design teams, one or two projects.
- Sol ranks the verified skills catalog against the JD. Local validation rejects
  added/renamed skills. The existing renderer fits relevant keywords into available
  skill lines. Explicit and related JD skills remain eligible for the Skills section
  even when demonstrated in experience; unrelated already-covered terms are omitted.
  Exact matches precede related matches; Sol's ranking orders terms within those groups.
- One column, standard headings, plain contact details, readable URLs, thin section
  dividers, regular-weight technologies and bullets, normal readable type.
- One page, at least 94% printable-height fill, safe bounds, no overfull boxes or
  detected text overlaps; then Sol checks the PDF images and extracted text.
- Up to three repairs using the same Sol model. An unresolved check produces
  `NEEDS_ATTENTION`, preserving drafts without a ready alert.
- Dated job `drafts/` folder with the saved JD, posting/verification, numbered render
  attempts, selections, self-check, model/usage receipts and `result.json`.
  Successful results include file hashes and are reused for unchanged official
  URL/JD/master/policy inputs. Changed files are not silently reused.
- Tracker status becomes DRAFTED only after a real successful run. No forms,
  employer uploads, submissions, outreach messages or APPLIED updates occur.
