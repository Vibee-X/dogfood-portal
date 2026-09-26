# Dogfood Portal — Build Plan & Opus Prompt Pack

Paste the **Context Header** at the start of every fresh Opus / Claude Code session, then paste that phase's prompt. Work happens here (architecture, review, next-prompt-writing); code happens on Opus.

---

## 0. Locked Decisions

- **Stack:** Django 5 + Django REST Framework, PostgreSQL 16, Docker Compose. UI is server-rendered Django templates + HTMX + Alpine.js, styled with a small hand-written CSS file — **all three vendored as committed static files served via whitenoise, zero CDN references, no Node build step, no SPA framework.** (A CDN `<script>` tag is a runtime external dependency — exactly what the event's own network-off test checks for. htmx.min.js and alpine.min.js are single-file downloads; commit them like any other static asset.)
- **Math:** numpy/scipy for cross-judge normalization and (if time allows) Bradley-Terry ranking.
- **License:** MIT.
- **Roles are per-event, not global.** One account can be an organizer on one event and a judge on another — modeled as `EventMembership(user, event, role)`, not a role field on `User`.
- **Every permission check lives server-side** (DRF `permission_classes` / queryset filtering). Never trust the frontend to hide something — it will be tested with a raw API client.
- **Timeline (71.5h of real build time):**

| Phase | Hours | Deliverable |
|---|---|---|
| 1 — T1 Core | 0–24h | auth, roles, events, teams, submissions, gallery |
| 2 — T2 Judging | 24–48h | assignment, rubric, role isolation, normalization, CSV export |
| 3 — T3 + bonuses | 48–60h | voting, comments, Threat Model (+3), Normalization Proof (+5), API-First (+3), Pairwise (+5) if time |
| 4 — Acceptance & docs | 60–72h | acceptance suite, .dogfood.toml, README/ARCHITECTURE/DATA-MODEL/JUDGING.md, demo video |

**Rule for all four phases: never start the next phase with the current one broken.** A clean T1+T2 beats a sprawling, half-working T4 on every scoring axis the organizers named.

---

## 0.5 CRITICAL — how grading actually verifies your build (confirmed from dogfoodhack.com/spec)

**Do this before writing another line of model code** — curl the real files into the repo, they've been downloadable since Sept 24 (only project *code* had to wait for kickoff):
```
curl -O https://dogfoodhack.com/spec/spec.md
curl -O https://dogfoodhack.com/spec/run.py
curl -O https://dogfoodhack.com/spec/fixtures.json
curl -O https://dogfoodhack.com/spec/example.dogfood.toml
```

**The checker (`run.py`) never logs in.** It reads `.dogfood.toml`, attaches one literal header per role to each request, and hits exactly 7 endpoints. There's no login flow to build for it — there's a header to hand it.

Real `.dogfood.toml`:
```toml
[portal]
base_url = "http://localhost:8080"

[tiers]
claimed = ["T1", "T2"]
pitch = "One sentence on what you built."

[auth]
organizer   = "Cookie: session=org_7f2a"
judge_a     = "Cookie: session=jdg_a_91bc"
judge_b     = "Cookie: session=jdg_b_44de"
participant = "Cookie: session=prt_2e88"

[routes]
gallery      = "/projects"
submit       = "/projects/new"
judge_scores = "/api/judge/scores"
peer_scores  = "/api/judge/scores?judge=judge_a"
csv_export   = "/api/export.csv"
```
Any header format is accepted (cookie, bearer token, basic auth) — **use DRF `TokenAuthentication` (`Authorization: Token <value>`) on every route the checker touches, not session cookies.** Session auth puts Django's CSRF middleware in front of the POST `submit` check with no CSRF token available — you'd pass that check by accident (a CSRF 403 is still a 4xx) for the wrong reason, which is fragile and indefensible in JUDGING.md. Token auth sidesteps CSRF entirely.

**The seed script must print these tokens on boot**, one per role, ready to paste into `.dogfood.toml`. You need **two** judge accounts (judge_a, judge_b) specifically — the peer-isolation check hits judge_a's scores URL while authenticated as judge_b.

**The exact 7 checks, verbatim:**
1. `GET {gallery}`, no auth → expect 200
2. `GET {gallery}` → expect a known fixture project title in the body
3. `POST {submit}` as participant → expect 4xx (fixture event's `submissions_close` is already in the past — **seed with that real timestamp, not an invented future one**, so this passes from real business logic, not luck)
4. `GET {judge_scores}` as judge_a → expect 200
5. `GET {peer_scores}` (the URL returning judge_a's scores) as judge_b → expect 401/403
6. `GET {judge_scores}` as participant → expect 401/403
7. `GET {csv_export}` as organizer → expect 200 + CSV body

Passing all 7 is necessary, not sufficient — judges still read the full T1/T2 feature list qualitatively (Tier Completion is 40%); the acceptance report is just the receipt they check first.

Real `fixtures.json` shape (input format only — transform it into your own schema, you don't have to store it this way):
```json
{
  "event": { "id": "evt_01", "name": "...", "submissions_close": "<ISO8601, already past>" },
  "tracks": [ { "id": "trk_01", "name": "..." } ],
  "judges": [ { "id": "jdg_01", "name": "...", "email": "...", "tracks": ["trk_01"] } ],
  "teams": [ { "id": "tm_01", "name": "...", "members": ["email@..."] } ],
  "projects": [ { "id": "prj_01", "team": "tm_01", "track": "trk_01", "title": "...", "summary": "...", "repo_url": "...", "submitted_at": "..." } ],
  "scores": [ { "judge": "jdg_01", "project": "prj_01", "criteria": { "functionality": 4, "quality": 3 }, "comment": "..." } ]
}
```
It deliberately includes a judge who rated everything the same, incomplete review batches, and one duplicate submission — the seed/normalization logic should survive those on purpose, that's the actual test.

---

## 1. Data Model (final)

**accounts**
- `User` — Django's auth user, extended with `display_name`
- `EventMembership` — `user_id, event_id, role [participant|judge|organizer|admin], status [invited|active], invited_by, created_at` — unique on `(user, event)`

**events**
- `Event` — `name, slug, description, start_date, end_date, submission_deadline, judging_start, judging_end, voting_start, voting_end, is_published, created_by, reviews_per_submission (default 3)`
- `Track` — `event_id, name, description`
- `Prize` — `event_id, track_id (nullable), name, description, rank`

**teams**
- `Team` — `event_id, name, created_by, created_at`
- `TeamMembership` — `team_id, user_id, joined_at`
- `InviteLink` — `team_id, code, expires_at, max_uses, uses_count`

**submissions**
- `Submission` — `team_id, event_id, track_id, title, tagline, description, thumbnail, gallery_images (JSON), demo_video_url, repo_url, live_url, tech_tags (array), custom_answers (JSON), status [draft|submitted], submitted_at, updated_at`

**judging**
- `Rubric` — `event_id, name, is_active`
- `RubricCriterion` — `rubric_id, name, description, weight, max_score`
- `JudgeAssignment` — `event_id, judge_id, submission_id, status [pending|in_progress|completed], batch_id, created_at` — unique on `(judge, submission)`
- `Score` — `assignment_id, criterion_id, value, created_at, updated_at` — unique on `(assignment, criterion)`
- `NormalizationRun` — `event_id, method, computed_at, snapshot (JSON: raw + normalized per judge per submission)` — this snapshot **is** the Normalization Proof bonus deliverable, don't rebuild it later
- `PairwiseComparison` (Phase 3, only if time) — `judge_id, submission_a_id, submission_b_id, winner_id, created_at`

**voting** (T3)
- `Vote` — `submission_id, voter_ref, created_at` — unique on `(submission, voter_ref)` for duplicate detection
- `Comment` — `submission_id, user_id, body, created_at, is_hidden`

**core**
- `AuditLog` — `actor_id, action, target_type, target_id, metadata (JSON), created_at`
- `Certificate` — `event_id, user_id, team_id, type, issued_at, verification_code`

---

## 2. Role Isolation — the matrix that gets tested

| Actor | Own scores | Peer judge's scores | Other track | Aggregate/results | Audit log |
|---|---|---|---|---|---|
| Visitor | — | ✗ | — | only if published | ✗ |
| Participant | ✗ (doesn't score) | ✗ | ✗ | only if published | ✗ |
| Judge | ✓ read/write own | ✗ | ✗ (unless assigned) | ✗ until published | ✗ |
| Organizer | ✓ | ✓ | ✓ (own event) | ✓ | ✓ (own event) |
| Admin | ✓ | ✓ | ✓ | ✓ | ✓ |

Every row that says ✗ needs a test that actually attempts the access with a real DRF API client and asserts a 403/404 — not a UI check.

---

## 3. Context Header — paste first, every fresh Opus/Claude Code session

```
Project: Dogfood Portal — a self-hostable hackathon submission & judging platform,
built for the Dogfood 72-Hour Hackathon (spec: dogfoodhack.com/spec).

STACK (do not deviate): Django 5 + Django REST Framework, PostgreSQL 16,
Docker Compose, HTMX + Alpine.js + a small hand-written CSS file, all three
vendored as committed static files served via whitenoise — NO CDN script or
link tags anywhere, ever (a CDN load is a runtime external dependency, and
network-off is retested in Phase 4). No Node/webpack build step.
pytest-django for tests, numpy/scipy for judging math.

HARD CONSTRAINTS:
- `docker compose up` must produce a fully working, migrated, seeded portal with
  ZERO external/cloud dependencies. No hosted DB, no auth-as-a-service, no third-
  party API calls. Must work with network access disabled.
- Every role/permission check is enforced server-side (DRF permission_classes /
  queryset filtering). Never rely on template conditionals or hidden buttons —
  this gets tested with a raw API client.
- Roles are per-event (EventMembership), not a global field on User.
- Every model change ships with a **committed** migration file — generated during development with `manage.py makemigrations`, reviewed, committed like any other source file. **Never regenerate migrations inside the Dockerfile or entrypoint at build/boot time** — that erases schema history and is exactly the kind of thing a senior reviewer (Code Quality, 15%) notices immediately. The entrypoint runs `migrate` only, never `makemigrations`.
- Every feature ships with a test.

REPO LAYOUT:
dogfood-portal/
├── README.md / ARCHITECTURE.md / DATA-MODEL.md / JUDGING.md / THREAT-MODEL.md
├── docker-compose.yml / Dockerfile / .dogfood.toml / LICENSE
├── manage.py
├── config/              # Django project settings
├── apps/
│   ├── accounts/        # User, EventMembership, auth
│   ├── events/          # Event, Track, Prize
│   ├── teams/           # Team, TeamMembership, InviteLink
│   ├── submissions/     # Submission, gallery
│   ├── judging/         # Rubric, Criterion, Assignment, Score, NormalizationRun
│   ├── voting/          # Vote, Comment
│   └── core/            # AuditLog, Certificate, shared utils
├── static/ / templates/ / fixtures/ / scripts/seed.py / tests/
└── acceptance-report.txt

DATA MODEL: [paste section 1 above in full on the first session; later sessions
can reference "as already implemented" instead of repasting]

requirements.txt starter: django, djangorestframework, drf-spectacular,
psycopg2-binary, gunicorn, django-environ, numpy, scipy, pytest, pytest-django,
whitenoise
```

---

## 4. Phase 1 Prompt — T1 Core (paste after the Context Header)

```
Phase 1 of 4. Build T1 (Core) completely and correctly — a submission that
doesn't clear T1 isn't judged at all, so this is the floor everything else
stands on. Don't touch judging/scoring — that's Phase 2.

BUILD, IN THIS ORDER:
0. Before any model code: curl the real spec files into the repo —
   https://dogfoodhack.com/spec/{spec.md,run.py,fixtures.json,example.dogfood.toml}
   — and read run.py's actual checks (see Section 0.5). Don't guess the
   schema or the .dogfood.toml format.
1. Project scaffold per the repo layout. Dockerfile + docker-compose.yml
   (web + db services) with an entrypoint that runs `migrate` (never
   `makemigrations` — those are committed source files, generated during
   development) then seed automatically on `up`. Vendor htmx.min.js,
   alpine.min.js, and a plain hand-written CSS file into static/ now,
   served via whitenoise — no CDN script or link tags anywhere, ever.
2. Custom User model + EventMembership BEFORE the first migration — this is
   painful to retrofit later.
3. Session-based auth: signup/login/logout. No third-party auth provider.
4. Event + Track + Prize models, organizer-only create/edit views,
   configurable dates.
5. Team + TeamMembership + InviteLink: create team, generate invite link,
   join via link, leave team.
6. Submission: draft/edit until deadline, with deadline enforcement that
   actually blocks writes server-side after the deadline (test this
   explicitly, don't just hide the edit button).
7. Public gallery: list + detail, search by title/tags, filter by track,
   shows only submitted (not draft) projects.
8. scripts/seed.py: loads the REAL fixtures.json curled in step 0 — don't
   invent dummy data — seeds organizer/admin + **two** judge accounts
   (judge_a, judge_b: peer-isolation needs two) + participants/teams/
   projects from it, uses the fixture event's real `submissions_close`
   timestamp verbatim, creates a DRF auth Token per account, and **prints
   ready-to-paste `.dogfood.toml` `[auth]` lines to stdout** when it runs.
   Document the same accounts/tokens in README.
9. Tests: auth flow, deadline enforcement, invite-link join, gallery
   search/filter, and one role-isolation smoke test (participant blocked
   from organizer-only endpoints via the DRF test client).

DELIVER: `docker compose up` → seeded, fully working portal covering all of
the above. Report back what's implemented, test coverage, and any deviation
from this spec and why.
```

---

## 5. Phase 2 Prompt — T2 Judging (highest-weighted phase — Judging Integrity is 25% of score)

```
Phase 2 of 4. T1 must already be fully working before you start this.

HARD REQUIREMENT: role isolation must be PROVEN, not assumed. A judge must
never see another judge's scores. A track judge must never see another
track. If it only works because the UI hides a button, it does not work.
Every isolation rule in the matrix below needs a test using the raw API
client (attempt the forbidden access, assert 403/404):
[paste Section 2 matrix]

BUILD, IN THIS ORDER:
1. RubricCriterion — organizer-configurable weight and max_score per event,
   not hardcoded.
2. Judge invitation → EventMembership(role=judge).
3. Batch assignment: every submission gets exactly `reviews_per_submission`
   assignments (default 3), load-balanced across judges, a judge is never
   assigned their own team's submission. Test: every submission has exactly
   N assignments, no judge is over-assigned by more than 1 vs. the mean, no
   self-assignment.
4. Scoring API — this is literally what run.py hits, see Section 0.5 for
   the exact contract: a judge_scores endpoint returns the requesting
   judge's own scores at 200; hitting it via a `?judge=<other>` query
   param, or as a participant at all, returns 401/403. **Use DRF
   TokenAuthentication on these routes, not session cookies** — the
   checker sends a bearer token, not a browser session, and this also
   sidesteps CSRF on the Phase 1 submit-after-deadline check. organizer/
   admin read everything. Every score write goes to AuditLog.
5. Judge progress dashboard (organizer view): assigned/completed counts per
   judge, HTMX-polled every ~10s — no websockets needed.
6. Cross-judge normalization: z-score each judge's raw scores
   (scipy.stats.zscore or equivalent) across their assignments, then compute
   the final weighted ranking from normalized scores × rubric weights. Store
   raw AND normalized together in a NormalizationRun snapshot — this
   snapshot doubles as the Normalization Proof bonus, don't rebuild it in
   Phase 3.
7. CSV export: submissions, raw scores, normalized scores, judge progress —
   organizer/admin only.

TESTS: full role-isolation matrix, assignment correctness, and hand-verify
one small normalization example against the numpy/scipy output so the
numbers in JUDGING.md are trustworthy.

DELIVER: T1 still fully working + T2 fully working, docker compose still one
command. Report back what's implemented and paste the actual before/after
normalization numbers from a test run.
```

---

## 6. Phase 3 Prompt — T3 + Bonuses (only start if T1+T2 are clean)

```
Phase 3 of 4. Do not start this with T2 gaps still open — a clean T2 beats a
broken T4 on every axis the organizers scored on.

Ship whichever of these you finish COMPLETE — don't leave four things half-
done:
1. Community voting: configurable access, results hidden from everyone but
   organizers during the voting window, randomized project order per
   ballot, rate limiting + duplicate detection (Vote unique on
   submission+voter_ref), everything audit-logged.
2. Comments on gallery projects.
3. Threat Model (+3, cheap — do this even if short on time): THREAT-
   MODEL.md covering Sybil voting, ballot stuffing, submission scraping,
   judge collusion, deadline gaming. Name the actual mitigation in code for
   each, and say plainly which ones you didn't fully solve.
4. Normalization Proof (+5): should already be provable from Phase 2's
   NormalizationRun snapshots — write it up in JUDGING.md with real
   before/after numbers, don't write new code for it.
5. API-First (+3, only if time remains): drf-spectacular OpenAPI schema at
   /api/schema/ covering every UI action.
6. Pairwise Mode (+5, hardest — only attempt if everything above is done):
   PairwiseComparison model + judge compare UI + Bradley-Terry MLE ranking
   via scipy.optimize.
```

---

## 7. Phase 4 Prompt — Acceptance, Docs, Video (final hours — do this regardless of tier reached)

```
Phase 4 of 4.
1. Run the official acceptance suite against the running docker compose up
   portal. Fix failures in already-claimed tiers BEFORE writing new feature
   code. Commit acceptance-report.txt, unedited, to the repo root.
2. .dogfood.toml: declare only the tiers actually passing. Overclaiming
   costs more than the tier was worth.
3. README.md: what it does, exactly how to run it (docker compose up,
   nothing else), demo credentials, and honest gaps stated plainly.
4. ARCHITECTURE.md: system shape, app boundaries, why Django+DRF+HTMX over
   an SPA (solo build velocity + Adoptability is 20% of score), how role
   isolation is enforced at the permission-class layer.
5. DATA-MODEL.md: full schema (from Section 1 above), CSV import/export
   paths.
6. JUDGING.md: assignment strategy, scoring methodology, the normalization
   formula with a worked before/after example, and explicitly address "the
   judge who rates everything a 3" — that's exactly what per-judge z-score
   normalization corrects for, show it with real numbers.
7. Final sanity check: clean checkout, network access disabled, docker
   compose up from scratch — confirm nothing quietly depends on an external
   call.
8. 5-minute demo video, one full lifecycle: create event → team forms →
   submits → judges score → normalization runs → results publish. Script it,
   don't improvise on camera.
```

---

## What happens where

**Here (this chat):** architecture calls, reviewing what Opus reports back, deciding when a phase is actually done vs. needs another pass, writing/adjusting the next prompt, deadline math.

**On Opus:** everything in the four prompts above — actual code, actual tests, actual commits.
