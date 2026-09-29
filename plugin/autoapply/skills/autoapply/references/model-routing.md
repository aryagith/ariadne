# Task routing and host setup

`model_routing_status` is read-only. It shows the selected CLI backend, configured
models, router mode and CLI availability without using model quota. Inspect it
before a run; do not promise live model availability based on configuration alone.

Laya chooses the writer tier from bounded task descriptions and input sizes.
It receives no resume, contact details, JD text or reviewer feedback. Code keeps
experience/team/project comparison and substantive revisions in the reasoning
tier even if Laya votes cheap. Skills matching makes one pinned model call:
Luna low on Codex, `opencode/space-bunny-free` on OpenCode. Only skill names and
the public JD go to this matcher. It ranks every source term once; code rejects
invented/renamed/duplicate/missing skills, filters covered terms and packs the page.
Ranking is reused during layout. This role bypasses Laya and never upgrades to a
paid model on failure. The final reviewer checks the displayed skills.
Layout-only decisions can use cheap.
Router outage/uncertainty selects the configured reasoning model and records a
degraded route. This is routing fallback only; failed writers/reviewers stop.

The reviewer is pinned for candidates, selection/layout advice and final review.
It must differ from both writer models. It is instructed to be harsh on vague
claims, inflated scope and keyword stuffing. For weak wording or no clear
improvement, a separate writer applies the criticism once and proposes three new
alternatives. The same reviewer reevaluates old and new candidates against the
master, and code selects supported wording or retains the original. Never treat
reviewer feedback as new factual evidence. No content approval permits applying.

Defaults: Codex uses Luna low for cheap work, Astra high for reasoning work, and
Sol medium as reviewer. OpenCode Go uses `opencode-go/glm-5.3-flash`,
`opencode-go/glm-5.3`, and `opencode-go/kimi-k2.6` respectively. Go uses provider
defaults for reasoning; do not claim Codex effort levels were applied to Go.
Models can be overridden in the workspace `.env` using `AUTOAPPLY_CHEAP_MODEL`,
`AUTOAPPLY_REASONING_MODEL`, and `AUTOAPPLY_REVIEWER_MODEL`. Preserve the separate
reviewer. Unknown models or unsupported images stop the run, with no substitution.

For OpenCode Go, the user connects their subscription through OpenCode `/connect`
and checks `/models`. Connect OpenCode Zen as well for `opencode/space-bunny-free`;
other stages retain their Go models. Verify that the free model remains available
and free before live use. Set `AUTOAPPLY_MODEL_BACKEND=opencode` in the shell used for
tool calls or in the workspace `.env`. Never read credentials into the conversation.
The CLI sends its own provider/session headers and uses its stored Go login.
Use the same `python -m backend.tool call --request-file ...` protocol from the
workspace. No Codex plugin installation is required inside OpenCode.

`AUTOAPPLY_ROUTER=laya` is the default. The separately installed Laya service must
be reachable at `LAYA_URL` (default `http://127.0.0.1:8001`). `AUTOAPPLY_ROUTER=rules`
explicitly selects the deterministic task policy and must not be described as a
Laya prediction. Routing receipts are saved per call, frozen across restarts, and
included in each completed packet's `routing.json`. An unresolved service issue
does not authorize silently changing provider settings or installing software.

Calls use fresh contexts and disable tools/sharing. OpenCode can retain local
session history; neither CLI setup is an OS isolation guarantee. Account settings
control Go overage behavior; do not claim the tool disables paid account overages.
No live browser worker or automatic submission is enabled by this integration.
