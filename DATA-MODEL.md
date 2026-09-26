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
- `core.AuditLog`: score writes record actor, operation, target, and old/new
  value metadata.

## Import/export

`scripts/seed.py` imports the official fixture IDs without collapsing duplicate
team names or projects. `/api/export.csv` is an organizer/admin export with
submission, score, normalization, and progress columns.
