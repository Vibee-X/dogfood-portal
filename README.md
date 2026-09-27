# Dogfood Portal

A self-hostable hackathon submission & judging platform, built for the Dogfood 72-Hour Hackathon.

## Quick Start

```bash
docker compose up
```

That's it. The portal will be available at **http://localhost:8080** with the database seeded from the official fixtures.

No cloud services, no external APIs, no hosted database. Works offline.

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

DRF auth tokens are printed by the seed script on boot — paste them into `.dogfood.toml`.

## Running the Acceptance Checker

```bash
python3 run.py .dogfood.toml
```

Update `.dogfood.toml` with the auth tokens from the seed output.

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

## Features (T2 and T3)

- **Judging integrity**: Event rubrics, scoped judge assignments, own-score
  isolation, audit logging, normalization snapshots, and organizer CSV export.
- **Community voting**: Per-event public/authenticated/participant access,
  configured voting windows, stable randomized ballot order, duplicate-vote
  database protection, and rate-limited vote attempts.
- **Comments**: Active event members can comment on submitted public projects;
  authors can edit their own comments and organizer/admin moderators can
  hide or unhide them.

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
