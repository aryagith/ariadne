# Outreach drafts

Ask the agent: “Find an appropriate contact at this company and draft an internship outreach email and LinkedIn message.” The agent researches public professional sources, uses Sol to write from the saved official JD and protected resume, and saves both drafts locally. Laya may support decisions when available; no additional language model is required.

There is no email/LinkedIn sending integration. Review and send manually. Contact research uses public search/fetch only. Unknown emails remain blank. A source reference is retained for review; it is not automatic proof that the contact or message is correct.

Use the existing JSON-file interface:

```powershell
& '.venv/Scripts/python.exe' -m backend.tool call --request-file requests/outreach.json
```

Available tools:

| Tool | Arguments | Result |
|---|---|---|
| `get_outreach_context` | `job_id` | Official JD, master hash, source bullet IDs/text, sender name, history |
| `save_outreach_draft` | Fields below | Local Markdown file with email, LinkedIn message, contact evidence, resume evidence; state DRAFT |
| `list_outreach` | Optional `job_id` | Draft/history records and file paths |
| `record_outreach_result` | `outreach_id`, `state` (SENT/REPLIED/CLOSED), `evidence` | Records confirmed manual activity; sends nothing |

Start with `{"tool":"get_outreach_context","arguments":{"job_id":"SAVED_JOB_ID"}}`.

To save, pass `job_id`, `snapshot_id`, and `master_sha256` from that context, plus:

- `contact_name`, `contact_role`, `contact_url`, `contact_evidence`: actual public professional information and its source/evidence.
- `email`, `email_evidence`: optional; exact published or user-confirmed address and evidence. Never infer an address pattern.
- `subject`, `email_body`, `linkedin_message`: the Sol-written messages for user review.
- `source_bullet_ids`: one to six distinct IDs supporting personal claims in the messages.

Draft revisions retain separate files under `outreach/`. Repeating the same job/contact updates the draft record instead of creating another contact attempt. Once a contact has sent/replied/closed history, new drafting for that same contact is blocked pending review. Records never move backwards automatically. No model or paid API calls occur in these storage tools; Sol drafts in the conversation.
