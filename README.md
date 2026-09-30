# Dogfood Portal

A self-hostable hackathon submission & judging platform, built for the Dogfood 72-Hour Hackathon.

## Quick Start

Prerequisite: Docker Engine with the Compose plugin.

```bash
docker compose up
```

Compose starts PostgreSQL, waits for it to become ready, applies committed
migrations, collects the vendored static files, runs `scripts/seed.py`, and
starts Gunicorn on **http://localhost:8080**. Startup deliberately does not run
`makemigrations`. The seed output includes the four demo token headers.

To stop the demo while retaining its Compose volume, use `docker compose down`.

## Offline and self-hosted operation

At runtime the portal uses only the Compose PostgreSQL service, its local
database volume, vendored browser assets, and Django/DRF token or session
authentication. It makes no calls to external APIs, hosted databases, CDNs, or
external identity providers. A first Docker build still needs a locally
available `python:3.12-slim` base image and Python package source (or an
equivalent local image/wheel cache); after that build, runtime operation is
self-hosted and does not require network access.

## Stack

- **Django 5** + Django REST Framework
- **PostgreSQL 16**
- **Docker Compose**
- **HTMX** + **Alpine.js** (vendored, no CDN)
- Hand-written CSS (no Tailwind, no Node build step)
- **pytest-django** for tests
- **numpy/scipy** available for judging math

## Demo Accounts

All accounts use password: `dogfood2026`

| Role | Username | Purpose |
|------|----------|---------|
| Organizer | `organizer` | Can create/edit events, view all scores, export CSV |
| Judge A | `judge_a` | Can view own scores |
| Judge B | `judge_b` | Can view own scores, blocked from judge_a's scores |
| Participant | `participant` | Can submit projects (when deadline permits) |

Demo process: browse the public gallery as an anonymous visitor, sign in with
one of the accounts above to exercise its server-enforced role, or copy a token
line emitted by the seed script into a request as `Authorization: Token <key>`.
The committed `.dogfood.toml` contains placeholders only; never commit a
seeded token.

## Running the Acceptance Checker

```bash
python3 run.py .dogfood.toml
```

Update `.dogfood.toml` with the auth tokens from the seed output.

## API documentation

The generated OpenAPI 3 document is available at `/api/schema/` (JSON:
`/api/schema/?format=json`). See [API.md](API.md) for the implemented endpoint
surface, token header, and server-enforced role restrictions.

## Features (T1 Core)

- **Authentication**: Signup, login, logout (session-based for browsers, token-based for API)
- **Per-event roles**: EventMembership(user, event, role) — not global user roles
- **Events**: Create, edit with configurable dates (submission deadline, judging window, voting window)
- **Tracks & Prizes**: Organizer-configurable
- **Teams**: Create, invite via shareable link, join, leave
- **Submissions**: Draft/edit until deadline, then submit. **Server-side deadline enforcement**.
- **Public Gallery**: Search by title/tags, filter by track. Only submitted projects visible.
- **Role Isolation**: All permission checks server-side (DRF permission_classes)
- **Real Fixtures**: Seeded from the official fixtures.json with 41 projects, 30 judges, 8 tracks

## Features (T2, T3, and pairwise judging)

- **Judging integrity**: Event rubrics, scoped judge assignments, own-score
  isolation, audit logging, normalization snapshots, and organizer CSV export.
- **Pairwise judging**: Deterministic in-scope project pairs, immutable judge
  choices, audit history, and organizer-only component-scoped
  Bradley--Terry rankings.
- **Community voting**: Per-event public/authenticated/participant access,
  configured voting windows, stable randomized ballot order, duplicate-vote
  database protection, and rate-limited vote attempts.
- **Comments**: Active event members can comment on submitted public projects;
  authors can edit their own comments and organizer/admin moderators can
  hide or unhide them.
- **Embed and certificates**: Published events expose a read-only frameable
  gallery at `/projects/embed/<event-slug>/`; active event members can issue a
  printable participation certificate with a public verification URL.

## Known limitations

- Public voting is not Sybil resistant: a voter can clear an anonymous ballot
  cookie or create accounts. The cache-backed vote limiter is not a distributed
  abuse-control system unless deployment supplies a shared cache.
- Pairwise judging has no tie option. Bradley--Terry rankings are comparable
  only inside a connected comparison component, and bounded numerical fitting
  can reach a boundary with sparse or one-sided evidence.
- Fixture URLs are preserved as fixture content. The portal does not fetch
  them during startup or normal operation.
- Several judging steps have no web page and are API-only (documented at
  `/api/schema/`): rubric scoring (`/api/judge/scores`), rubric and criterion
  setup (`/api/judging/rubrics`), judge invitations
  (`/api/judging/judges/invite`), assignment generation
  (`/api/judging/assignments/generate`), pairwise pair generation
  (`/api/judging/pairwise/generate`) and normalization runs
  (`/api/judging/normalization/run`). The web UI covers making pairwise
  choices and the organizer progress table.
- There is no results page. Normalized results are available through the
  organizer/admin CSV export (`/api/export.csv`, which the UI does not link
  to) and `scripts/normalization_report.py`; community votes through
  `/api/voting/results`; pairwise rankings through
  `/api/judging/pairwise/rankings`. A normalization run's API response
  returns only the run id, not the ranking.
- The audit log can only be read in Django admin (`/admin/`), and no seeded
  account can open it: the seed creates no staff or superuser, so the seeded
  organizer is redirected to the admin login. Create one with
  `python manage.py createsuperuser` (in Docker:
  `docker compose exec web python manage.py createsuperuser`). The seed
  imports fixture scores directly, so they have no audit entries; the trail
  starts with the first action taken through the app or API.
- Duplicate submissions are not detected. The fixture's two "Dry Harbour"
  entries (`prj_07`, `prj_41`) are kept and ranked separately; see
  `JUDGING.md`.
- Normalization uses per-judge z-scores, which assume each judge saw a
  comparable batch of projects. The weaknesses, and what we would do next,
  are measured on the fixture data in `JUDGING.md`.

## Project Structure

```
dogfood-portal/
├── config/          # Django settings, URLs, WSGI
├── apps/
│   ├── accounts/    # Custom User, EventMembership, auth
│   ├── events/      # Event, Track, Prize
│   ├── teams/       # Team, TeamMembership, InviteLink
│   ├── submissions/ # Submission, gallery, deadline enforcement
│   ├── judging/     # Rubric, Score, JudgeAssignment (T2)
│   ├── voting/      # Vote, Comment (T3)
│   └── core/        # AuditLog, Certificate, shared utils
├── templates/       # Django templates (HTMX + Alpine.js)
├── static/          # Vendored JS (htmx, alpine), hand-written CSS
├── fixtures/        # Official fixtures.json
├── scripts/         # seed.py
├── tests/           # pytest-django test suite
└── docker-compose.yml
```

## License

MIT
