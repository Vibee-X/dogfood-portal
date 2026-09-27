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

`NormalizationRun` is a reproducibility record, not a second scoring system.
Each saved `weighted-zscore-v1` snapshot preserves the active criteria, entered
criterion values, observed weights, raw review score, per-judge statistics,
normalized review score, and project means used for that run.

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

### Reproducible local snapshot example

The local run exercised by
`tests/test_t2.py::test_normalization_handles_weights_incomplete_reviews_and_zero_variance`
uses these two active criteria:

| Criterion | Weight | Maximum |
| --- | ---: | ---: |
| Quality | 2 | 5 |
| Impact | 3 | 10 |

The variable-scale judge gave one project `Quality=5, Impact=10`, producing
`r = (2 * 5/5 + 3 * 10/10) / (2 + 3) = 1.0`. For another project the same
judge gave `Quality=1, Impact=2`, producing
`r = (2 * 1/5 + 3 * 2/10) / 5 = 0.2`. The stored snapshot statistics for that
judge are mean `0.6` and population standard deviation `0.4`, so the stored
normalized scores are `(1.0 - 0.6) / 0.4 = +1.0` and
`(0.2 - 0.6) / 0.4 = -1.0`. With one included review per project, their
project `normalized_mean` values are respectively `+1.0` and `-1.0`.

The same local run includes a partial review with only `Quality=5`. Its
observed weight is `2`, so its raw score is `2 * 5/5 / 2 = 1.0`; the missing
Impact value is not zero-filled. That judge has only one scored review, so the
snapshot records `fallback: "zero_variance"` and stores its normalized score
as `0.0`. A separate assignment with no entered criterion is stored with
`included: false` and contributes to neither judge statistics nor project
means. The test recomputes the documented mean, population standard deviation,
and z-scores from the persisted snapshot values.

`POST /api/judging/normalization/run` stores the criteria, raw criterion values,
weights, per-judge statistics, each normalized review, and project aggregates
in an immutable `NormalizationRun.snapshot`. The seeded official fixture also
gets one snapshot.

## Progress and export

Organizers see `/events/<event-slug>/judging/progress/`, an HTMX table polling
every ten seconds. `/api/judging/progress` and `/api/export.csv` are
organizer/admin-only. The CSV contains submission details, raw criteria, raw
weighted score, normalized review and project scores, plus per-judge progress.
