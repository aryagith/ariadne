# Tailoring and recovery

Read before preparing, revising, or recovering a LaTeX application packet.

The user supplied and designated `C:/projects/autoapply/master resume/master.tex`
as the source of truth and selected the existing subscription and ONE PAGE output.
Choose experience, engineering design teams, and ONE or TWO personal projects for
the actual JD. Do not include a team by default. If embedded systems are not central
to the role, omit Lassonde Motorsports and consider stronger relevant evidence such
as PharmShift. Keep Arbalest only when its work helps the role, and avoid repeating
the same rocket-tracking work in both a team and a personal-project entry. During
layout, preserve the strongest relevant evidence rather than a fixed section quota.
Use the plain-text presentation from the renderer: no contact icons, icon fonts,
table-based headings or letter-spaced name. Keep thin horizontal section dividers
for readability, as requested by the user. Keep contact details
in the document body and show readable URLs. Preserve the original hyperlink targets.
The PDF export includes resume.txt for extraction inspection. Inspect the rendered
PDF and its reading order; never claim a guaranteed ATS score or vendor certification.
Call `master_resume_status` to verify its hash. Never edit that master or reuse an
earlier tailored resume as evidence. Use the configured CLI subscription; OpenCode
Go credentials remain in its own login store. Do not infer dates, metrics,
responsibilities or application answers.

Write achievement bullets using technical STAR principles: concrete action,
component/workflow, implementation mechanism, and a supported purpose, challenge
or outcome. Use at most two sentences; avoid framework lists and generic process,
meeting, Git or status-report bullets. Specific tests of failure recovery or
calibration can be substantive. Use three to five bullets per role when source
facts and page space support them, without padding. Never invent percentages or
turn ongoing work into completed results. If impact is qualitative, say so.

Use consistent emphasis across every section: keep technologies, metrics, and all
bullet text in regular weight; reserve bold for names, role/project titles and
section headings. Apply this equally to verbatim source bullets and rewritten ones.
Technology stacks in project headings and the Skills section also use regular weight.

Use the pinned Luna low / free Zen skills matcher described in [model routing](model-routing.md)
to rank the master skill catalog once per resume/JD pair. Code validates exact
source membership and reuses the ranking across layout changes. Preserve as many
relevant, source-supported technical skills as the available space
allows. Allocate space to relevant experience, selected teams, and projects first. Then
fill up to three Skills rows with skills not already demonstrated in the selected
bullets/headings. Rank explicit JD matches and aliases first, then skills related
to the job's work and technical domain, then broader relevant technical skills.
Do not stop after one exact match: the next skill should be the next most relevant
supported skill even if the JD never names it. Fill the row width naturally rather
than leaving a sparse category row; code packs continuously across rows using actual
LaTeX font widths and tries shorter remaining terms when a longer term will not fit.
Reduce Skills rows before cutting substantive content if the page overflows. Never
invent a skill, infer one from a related technology, add generic soft-skill filler,
or shrink fonts/stretch spaces to fill the section. Every displayed skill must
come from the protected master. With no JD, use a general technical baseline and
label it general. Final review checks support, ordering, fullness, and consistent weight.

After saving the official JD, call `prepare_latex_application` with job_id and only
explicitly user-supplied answers (omit unknowns). The tool records its automatic
route in `routing.json` and the returned pair. Clearly identified Google/Alphabet,
Amazon, Apple, Meta, Netflix, Microsoft, or NVIDIA roles, or official JDs showing
a USD hourly pay floor of at least $60, retain the premium routing label. Unknown
or ambiguous pay uses the standard label. Do not infer pay or employer identity
from a listing's title or repository prose. Both labels use one review strategy
through the configured CLI; do not spawn multiple reviewer agents. Read
[task routing](model-routing.md) for Laya, provider selection and model overrides.

The routed reasoning writer selects entries and generates three alternatives for
up to four source bullets. One pinned independent harsh reviewer evaluates each
alternative alongside the original for factual
support and writing quality, then recommends source-backed entries to include or
omit. Deterministic code selects acceptable wording or keeps the original, and
validates the reviewer's entry choices. The reviewer owns content decisions: maximize
truthful JD fit and the signal of a high-value candidate by comparing actual work,
depth, and relevance across experience, teams, and projects. Distinguish analyzed
data from merely collected or displayed data. Do not favor Quizzle automatically
over an e-commerce chatbot with honest but modest classifier results, or duplicate
rocket-tracking evidence when Arbalest already covers it. These are comparison
examples, not fixed project choices. Never exaggerate metrics or invent expertise.
If no clear wording improvement wins, or the winner's quality score is below 80,
code gives the separate writer the reviewer's criticism for one regeneration.
The reviewer then sees all old and new contenders against the original evidence.
Unsupported/unclear wording can never win on quality. These scores and thresholds
are engineering heuristics, not calibrated hiring or ATS predictions.

The resume should fill 94–100% of the printable one-page height with normal margins,
readable type and natural spacing. Only when the first PDF fails those bounds, the
routed fit model chooses a compact/standard preset and one to three Skills rows. It does not
choose content. If that still fails, return the measured fit problem to the same
pinned reviewer to decide which source-backed bullets to add or remove. Give it
specific feedback on the failing page and visible entry: fill and bottom gap,
overflow-page opening lines, page-end lines, clipped or overlapping text, and TeX
overfull source locations when available. Render each reviewed change and return
new failure feedback until the one-page checks pass. Preserve each trial. Stop if
the reviewer repeats an ineffective plan or after six distinct repair rounds;
report the unresolved issue rather than accepting a poor fit. Added wording is
verbatim from the master. Do not fill space with inflated gaps, tiny fonts,
irrelevant facts or new claims. A fresh call to the pinned reviewer checks the assembled PDF
against the source facts and JD. Do not silently substitute models, self-approve,
or regenerate wording repeatedly. A failed stage stops. At most 14 model calls,
including the single wording revision and reevaluation, are allowed per run.
Tell the user which provider, reviewer and task routes were recorded, including
any conservative route caused by missing or uncertain Laya.

Allow the command to finish; do not duplicate it while a preparation is running.
The archive stores stages and failures. Read a failed stage's diagnostics before
attempting recovery. Do not delete attempt records to bypass the call limits.
For missing CLI login or model availability, explain the actual blocker. Existing
Laya tools remain an optional separately configured legacy path; never represent a
subscription review as Laya approval or silently switch review providers.
