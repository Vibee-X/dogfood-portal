# Architecture

Dogfood Portal is a Django 5 server-rendered application with Django REST
Framework APIs, PostgreSQL in Compose, and HTMX/Alpine static assets served by
WhiteNoise. There is no CDN or client-side build step.

T1 owns account, event, team, submission, and gallery boundaries. T2 is
isolated in `apps.judging`: models hold rubric, scope, assignment, score, and
normalization data; `services.py` owns deterministic assignment and math; and
`views.py` applies token-authenticated role checks before reading or writing.

Role isolation is enforced in the server querysets, not presentation logic:
judges are filtered to their own assignments, peer `judge` query parameters are
rejected, participants are denied, and organizer/admin reads are constrained to
their event. Score writes additionally verify assignment ownership, track scope,
and criterion/event consistency. Every successful write creates an audit row.

The normalization snapshot is deliberately stored rather than recalculated for
an export: it records the raw values, criteria configuration, judge statistics,
fallback use, and project aggregates needed to reproduce the result later.

Pairwise mode remains inside `apps.judging` and reuses existing
`JudgeAssignment` authorization rather than creating a parallel permission
system. The service layer builds canonical, deterministic pairs only from a
judge's active in-scope assignments, and the model validates the same
relationship on normal saves. API and server-rendered HTMX workflow writes pass
through the shared completion service, which makes the decision immutable and
adds an audit row. Bradley--Terry estimation uses SciPy in connected components
with a per-component anchor, so disconnected graphs and projects lacking
comparisons are represented explicitly rather than given invented global ranks.

T3 is isolated in `apps.voting`. Its DRF endpoints enforce the event's voting
window and configured access policy before returning a per-ballot deterministic
shuffle of submitted projects or accepting a vote. The ballot identity is an
authenticated account identity or a server-signed anonymous cookie; the
database uniqueness constraint and cache-backed attempt limiter use that same
identity. Active voting totals are returned only to the event organizer/admin;
the public results endpoint opens after the voting window closes.

Comment reads expose only unhidden comments to the gallery. Posting requires an
active event membership, edits require comment ownership, and hiding/unhiding
requires organizer/admin membership. These checks are all in API views, so a
request parameter or hidden UI control cannot grant access.
