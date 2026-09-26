# Judging

## Roles and scope

Roles are event-scoped through `EventMembership`. An active judge can only read
and write their own `JudgeAssignment` records. Organizers and event admins can
read every score in their event; participants and visitors are denied.

An organizer invites a judge through `POST /api/judging/judges/invite`. The
optional `track_ids` are checked against the invitation event and stored as
`JudgeTrack` rows. A judge with at least one scope row can only be assigned
projects in those tracks. A judge with no scope rows is event-wide. In all
cases, a team member is never assigned their own project.

## Assignment

`POST /api/judging/assignments/generate` processes submitted projects in
primary-key order. It chooses the least-loaded eligible judge, breaking ties by
judge primary key. The batch therefore has a stable result and a repeat call is
idempotent. It keeps existing assignments, creates only missing rows, and
returns an error rather than deleting completed history if a project already
has more than `event.reviews_per_submission` reviews. Track eligibility and the
self-assignment ban take priority over load balancing.

## Scoring

`POST`, `PUT`, and `PATCH` to `/api/judge/scores` accept
`assignment_id`, `criterion_id`, and `value`. The server checks the requesting
judge owns the assignment, the assignment is in scope, the criterion belongs
to the same event, the rubric is active, and `1 <= value <= max_score`. A
write creates `AuditLog` entry `score.created` or `score.updated`.

## Normalization

For each review, the raw weighted score uses only criteria actually entered:

`r = sum(w_c * value_c / max_c) / sum(w_c)`.

For every judge, values are z-scored using the population standard deviation:

`z = (r - mean_judge(r)) / stddev_judge(r)`.

The project result is the mean of its included review z-scores. Partial reviews
are included using their observed weights; assignments with no scores are
omitted rather than treated as zero. If a judge has fewer than two scored
reviews, or rates every review identically (`stddev = 0`), every one of that
judge's z-scores is defined as `0`. This avoids division by zero and ensures a
constant scorer supplies no artificial ranking signal.

`POST /api/judging/normalization/run` stores the criteria, raw criterion values,
weights, per-judge statistics, each normalized review, and project aggregates
in an immutable `NormalizationRun.snapshot`. The seeded official fixture also
gets one snapshot.

## Progress and export

Organizers see `/events/<event-slug>/judging/progress/`, an HTMX table polling
every ten seconds. `/api/judging/progress` and `/api/export.csv` are
organizer/admin-only. The CSV contains submission details, raw criteria, raw
weighted score, normalized review and project scores, plus per-judge progress.
