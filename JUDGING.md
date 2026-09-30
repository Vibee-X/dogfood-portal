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

## Normalization on the official fixture data

Every number in this section comes from the snapshot that `scripts/seed.py`
stores for the official fixture event (`evt_01`). To reproduce it on a fresh
database:

```bash
python manage.py migrate --noinput
python scripts/seed.py
python scripts/normalization_report.py --project prj_07 --project prj_19
```

With Docker, run the last line as
`docker compose exec web python scripts/normalization_report.py`. The script
is read-only (it switches the database connection to read-only before any
query), prints every project's raw and normalized result, recomputes every
raw score, z-score and project mean from the criterion values stored in the
snapshot, and reports whether live scores have changed since the run.

The fixture yields 41 ranked projects, 30 judges, 126 reviews and 378
criterion values. Its three criteria (functionality, innovation, quality)
each have weight 1 and maximum 5, so a review's raw score is the mean of its
three values divided by 5. Normalization changes the rank of 36 of the 41
projects. Ranks use competition ranking (ties share the best rank). Iron
Switch (`prj_34`) is first under both methods; it shares raw #1 with Salt
Ledger (`prj_11`, raw mean 0.8667), and normalization breaks that tie
(`prj_11` becomes #3).

The eight largest rank changes:

| Project | Fixture id | Reviews | Raw mean | Raw rank | Normalized mean | Normalized rank | Change |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Dry Harbour | `prj_07` | 5 | 0.6667 | 30 | +0.2755 | 11 | up 19 |
| Small Relay | `prj_19` | 2 | 0.7333 | 13 | -0.2384 | 30 | down 17 |
| Glass Signal | `prj_01` | 3 | 0.6889 | 24 | +0.2882 | 10 | up 14 |
| Flat Meadow | `prj_28` | 3 | 0.6889 | 24 | -0.8897 | 38 | down 14 |
| Paper Anchor | `prj_39` | 2 | 0.7000 | 20 | +0.3070 | 8 | up 12 |
| Small Meadow | `prj_02` | 3 | 0.7111 | 16 | -0.1151 | 28 | down 12 |
| Deep Beacon | `prj_38` | 3 | 0.7556 | 11 | +0.0318 | 22 | down 11 |
| Loud Ledger | `prj_32` | 3 | 0.6889 | 24 | -0.2410 | 31 | down 7 |

The two largest moves, from the `--project` breakdowns (scores are listed as
functionality/innovation/quality):

- **Dry Harbour (`prj_07`) rises 19 places.** Its raw mean includes Tomas
  Varga's only review (2/2/2, raw 0.4000), which normalization neutralises
  (see below). Jonas Vogel scored it 4/5/5 (raw 0.9333) against his own mean
  of 0.7400 over 10 reviews, a z-score of +2.1094. The five review z-scores
  (+1.0000, +2.1094, -1.7321, 0.0000, 0.0000) average +0.2755.
- **Small Relay (`prj_19`) drops 17 places.** It has two reviews. Iva
  Petrova's 4/4/4 (raw 0.8000) raised its raw mean, but she gave every
  project that score, so it carries no ranking signal (z = 0). Ines Rocha
  scored it 3/5/2 (raw 0.6667), below her own mean of 0.7037 over 9 reviews
  (z = -0.4767). The two z-scores average -0.2384.

### Constant scorer and too-few-review judges

Three judges have every z-score set to 0. The snapshot records both cases as
`fallback: "zero_variance"`; the report derives the reason from the stored
review count and standard deviation.

| Judge | Reviews | Reason |
| --- | ---: | --- |
| Iva Petrova (`iva_petrova`) | 3 | Constant scorer: 4/4/4 on every review (raw 0.8000, population standard deviation 0) |
| Tomas Varga (`tomas_varga`) | 1 | Too few reviews to estimate a scale |
| Anya Sokolova (`anya_sokolova`) | 1 | Too few reviews to estimate a scale |

Among the other 27 judges, the mean raw score runs from 0.6000
(`emeka_adeyemi`) to 0.8444 (`wei_lindqvist`). That leniency gap is what the
per-judge z-scores remove.

### Unfinished review batches

The fixture deliberately omits some reviews ("not every judge finishes every
batch"). It contains no assignment list, only the 126 reviews that exist, so
the seed creates one `JudgeAssignment` per existing review. Missing reviews are
not zero-filled and no placeholder assignment is created; the portal therefore
cannot tell which absent reviews belonged to an unfinished batch. The effect
is uneven coverage: 8 projects have 2 reviews, 26 have 3, 3 have 4 and 4 have
5. The eight below the event target of 3 reviews are `prj_10`, `prj_15`,
`prj_18`, `prj_19`, `prj_24`, `prj_29`, `prj_39` and `prj_40`; their normalized
mean averages two z-scores. Every fixture review scores all three criteria, so
the partial-review rule (observed weights only) is exercised by the test
described above, not by this data.

### The duplicate submission

`prj_07` and `prj_41` are both titled "Dry Harbour" and belong to the same
fixture team. The seed keeps them as separate submissions, unique by
`(event, fixture_id)`, so each keeps its own reviews and rank (`prj_41` raw #9
and normalized #9; `prj_07` raw #30 and normalized #11). The portal does not
detect or flag duplicate titles to organizers: they appear as two gallery
cards and two sets of CSV rows. The report's "Data notes" section lists them.

### Known weaknesses of this method

- **Per-judge z-scores assume comparable batches.** The 126 reviews are spread
  over 30 judges: the median judge has 3.5 reviews and 15 judges have three or
  fewer. With batches that small, a judge's mean depends on which projects
  they happened to draw. A judge who draws a strong batch pushes good projects
  toward or below 0, so a project can be penalised for its neighbours.
- **Two reviews always give z = ±1.** With a population standard deviation, a
  judge with exactly two distinct reviews produces +1 and -1 whatever the gap
  between them; 6 judges have exactly two reviews.
- **Fallback reviews are averaged in as 0, not dropped.** A constant scorer's
  or a single review's z = 0 still counts toward the project mean, pulling it
  toward the event average. For `prj_19` it halves the only informative
  z-score (-0.4767 becomes -0.2384).
- **No uncertainty is reported.** A project with 2 reviews is ranked with the
  same apparent confidence as one with 5.
- Normalization corrects scale differences, not taste or coordinated intent
  (see `THREAT-MODEL.md`).

Not implemented, and what we would do next: estimate project quality and judge
leniency jointly over the whole review graph (an additive model
`raw = quality(project) + offset(judge) + noise` fitted by regularised least
squares or as a mixed model), so a judge's offset is measured against other
judges who scored the same projects rather than against their own small batch.
Alongside it, shrink judge statistics toward the pooled values when a judge has
few reviews (empirical Bayes) instead of the hard `n < 2` cut-off, report a
per-project interval (for example a bootstrap over reviews), and flag projects
below `reviews_per_submission` and duplicate titles to organizers.

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
