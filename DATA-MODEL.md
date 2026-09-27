# Data Model

## T1 core

- `accounts.User`: Django user with `display_name`.
- `accounts.EventMembership`: unique `(user, event)` role and invitation state.
- `events.Event`, `Track`, and `Prize`: event configuration; `reviews_per_submission` defaults to 3.
- `teams.Team`, `TeamMembership`, and `InviteLink`: team ownership and membership.
- `submissions.Submission`: project data, status, and a per-event `fixture_id`
  that preserves official duplicate records.

## T2 judging

- `judging.Rubric`: event-scoped named rubric. Names are unique per event.
- `judging.RubricCriterion`: rubric criterion with positive `weight` and
  positive `max_score`; names are unique within a rubric.
- `judging.JudgeTrack`: unique `(event, judge, track)` permission scope. No
  rows means an event-wide judge.
- `judging.JudgeAssignment`: unique `(judge, submission)` review assignment;
  validates matching event and rejects a judge's own team project.
- `judging.Score`: unique `(assignment, criterion)` numeric value. Validation
  requires the criterion's rubric event to match the assignment event and the
  value to be in the criterion's `1..max_score` range.
- `judging.NormalizationRun`: immutable method, timestamp, and JSON snapshot
  containing all weighted z-score inputs and outputs.
- `judging.PairwiseComparison`: event-scoped judge assignment pair with two
  canonical ordered submissions, an optional winner, and timestamp. Database
  constraints require `submission_a < submission_b`, one pair per judge and
  submission pair, and a winner that is one of the two submissions. Model
  validation also requires an active judge and valid `JudgeAssignment` records
  for both submissions.
- `core.AuditLog`: score writes record actor, operation, target, and old/new
  value metadata.

## T3 community voting and comments

- `events.Event.voting_access`: per-event policy of `public`,
  `authenticated`, or active event `participants`. Voting also requires a
  published event and an open `[voting_start, voting_end)` window.
- `voting.Vote`: a submitted, published submission and an opaque `voter_ref`,
  with a database `UniqueConstraint(submission, voter_ref)`. Authenticated
  voters use `user:<user primary key>`; anonymous voters use a server-generated
  signed browser-ballot cookie prefixed with `anon:`.
- `voting.Comment`: submission, author, body, creation time, and `is_hidden`.
  Comments retain their author even when an organizer/admin moderator hides
  them.

Vote creation and comment create/edit/moderation append `core.AuditLog` rows.
The vote rate-limit counter is cache-backed and keyed by the event plus the
same opaque voter reference.

## Import/export

`scripts/seed.py` imports the official fixture IDs without collapsing duplicate
team names or projects. `/api/export.csv` is an organizer/admin export with
submission, score, normalization, and progress columns. Pairwise rankings are
available from the organizer/admin-only `/api/judging/pairwise/rankings` API.
