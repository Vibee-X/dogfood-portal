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
