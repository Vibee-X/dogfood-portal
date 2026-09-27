# Data Model

## T1 core

- `accounts.User`: Django user with `display_name`.
- `accounts.EventMembership`: unique `(user, event)`, event-scoped role,
  invitation status, inviter, and creation time.
- `events.Event`: event name/slug, publication state, submission/judging/voting
  windows, voting-access policy, creator, and `reviews_per_submission`
  (default 3). `Track` is unique by `(event, name)`; `Prize` belongs to an
  event and may refer to a track.
- `teams.Team`: event, creator, display name, and per-event `fixture_id`.
  `TeamMembership` is unique by `(team, user)`. `InviteLink` supplies a unique
  code with optional expiry and use limit.
- `submissions.Submission`: event, team, optional track, project metadata and
  URLs, status, submission timestamps, and a unique `(event, fixture_id)` that
  preserves intentional duplicate fixture records rather than collapsing them.
- `core.AuditLog`: nullable actor, action, target type/id, JSON metadata, and
  timestamp; score, vote, comment, and pairwise writes use it for accountability.
- `core.Certificate`: event/user certificate with optional team, type, issue
  time, and unique verification code.

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
team names or projects. It creates the named organizer, judge A, judge B, and
participant accounts and prints their newly created DRF token headers to
standard output. `/api/export.csv` is an organizer/admin export with
submission, score, normalization, and progress columns. Pairwise rankings are
available from the organizer/admin-only `/api/judging/pairwise/rankings` API;
the data is not folded into the rubric-normalization export.
