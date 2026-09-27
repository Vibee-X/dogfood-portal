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

## Pairwise mode

Pairwise mode is an additional judging workflow; it does not replace rubric
scores or normalization. An organizer/admin calls
`POST /api/judging/pairwise/generate` after normal assignments exist. For each
active judge, the generator takes only that judge's currently assigned,
submitted, in-scope projects, sorts their database IDs, and creates every
two-project combination in that deterministic order. Repeating generation is
idempotent: existing pending or completed comparisons remain untouched.

Each `PairwiseComparison` stores an event, judge, canonical
`submission_a_id < submission_b_id`, optional `winner_id`, and timestamp. The
database constraint makes `(A, B)` and `(B, A)` the same pair for a judge and
requires a non-null winner to be one of the pair. Model validation additionally
requires an active event judge and a `JudgeAssignment` for both projects, so
track scope and the no-self-team rule are inherited from the assignment layer.

Judges use `/events/<event-slug>/judging/pairwise/`, which shows Project A and
Project B with their available project details and explicit **Choose A** and
**Choose B** actions. The HTMX response replaces the completed comparison with
the next assigned pair. A decision is immutable through the application API;
every generated assignment and completed decision is written to `AuditLog`.
The token API equivalents are `/api/judging/pairwise/next`,
`/api/judging/pairwise/comparisons`, and
`/api/judging/pairwise/rankings`. Judges are restricted to their own rows;
organizer/admin users may inspect only their event's comparisons and rankings.

### Bradley--Terry ranking

Completed comparisons use the binary Bradley--Terry model. For a winner `w`
and loser `l` with log-strengths `theta`, the likelihood contribution is:

`P(w beats l) = exp(theta_w) / (exp(theta_w) + exp(theta_l))`

and the implementation minimizes the negative log likelihood:

`sum(log(1 + exp(-(theta_w - theta_l))))`.

`scipy.optimize.minimize` with L-BFGS-B estimates the parameters. Strengths
are only identifiable up to a shared additive constant, so the lowest-ID
submission in each connected comparison graph is fixed at `theta = 0`. The
returned strength is `exp(theta)` and the returned probability is versus that
component anchor; neither is a cross-component probability.

Incomplete data is retained: submissions with no completed comparisons are
returned with `component_id`, rank, strength, and probability set to `null`.
Disconnected graphs are fitted separately and receive independent component
IDs and ranks; the API deliberately does not manufacture a global ordering.
The UI has no tie option, so each saved decision has exactly one winner. For
numerical stability and a finite result under a perfectly one-sided/separated
history, free log-strengths use bounded constrained MLE in `[-20, 20]`; a
strength at that boundary signals limited evidence rather than production-proof
certainty. The implementation surfaces optimizer failure rather than inventing
fallback strengths.

## Progress and export

Organizers see `/events/<event-slug>/judging/progress/`, an HTMX table polling
every ten seconds. `/api/judging/progress` and `/api/export.csv` are
organizer/admin-only. The CSV contains submission details, raw criteria, raw
weighted score, normalized review and project scores, plus per-judge progress.
