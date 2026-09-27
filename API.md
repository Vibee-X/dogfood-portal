# API

`GET /api/schema/` serves the generated OpenAPI 3 document. Request JSON with
`Accept: application/vnd.oai.openapi+json` or `?format=json`. The schema is
public documentation; it does not grant access to the operations it describes.

Unless an endpoint below is explicitly public, use DRF token authentication:

```http
Authorization: Token <key>
```

Event roles are evaluated server-side from an active `EventMembership`; a
query parameter cannot widen a caller's event or role scope.

## Submission and judging

| Endpoint | Methods | Access actually enforced |
| --- | --- | --- |
| `/projects/new` | `POST` | Authenticated team member before the event submission deadline. |
| `/api/judge/scores` | `GET`, `POST`, `PUT`, `PATCH` | A judge reads and writes only own assigned scores; organizer/admin may read event scores; participants are denied. |
| `/api/judging/rubrics` | `GET`, `POST` | Organizer/admin for the requested event. |
| `/api/judging/rubrics/{rubric_id}/criteria` | `POST`, `PATCH` | Organizer/admin of the rubric event. |
| `/api/judging/judges/invite` | `POST` | Organizer/admin of the requested event. |
| `/api/judging/assignments/generate` | `POST` | Organizer/admin of the requested event. |
| `/api/judging/progress` | `GET` | Organizer/admin of the requested event. |
| `/api/judging/normalization/run` | `POST` | Organizer/admin of the requested event. |
| `/api/export.csv` | `GET` | Organizer/admin of the requested event. |
| `/api/judging/pairwise/generate` | `POST` | Organizer/admin; generates deterministic pairs only from current authorized judge assignments. |
| `/api/judging/pairwise/next` | `GET` | Active judge; returns only the caller's next pending authorized pair. |
| `/api/judging/pairwise/comparisons` | `GET`, `POST` | Judge reads/completes only own pairs; organizer/admin can inspect their event's pairs. |
| `/api/judging/pairwise/rankings` | `GET` | Organizer/admin; component-scoped Bradley--Terry estimates. |

Most judging endpoints accept `event=<event-slug>`; if absent they retain the
existing single-event/first-event compatibility behavior. Score filters can
narrow an already authorized queryset but never select a peer judge's data.

## Community voting and comments

| Endpoint | Methods | Access actually enforced |
| --- | --- | --- |
| `/api/voting/ballot` | `GET` | Published event, open voting window, and that event's configured `public`, `authenticated`, or `participants` policy. |
| `/api/voting/votes` | `POST` | Same ballot conditions; submitted projects only. One database-enforced vote per submission and ballot identity. |
| `/api/voting/results` | `GET` | Organizer/admin during voting; anyone after the configured window ends, for a published event. |
| `/api/projects/{submission_id}/comments` | `GET` | Public read of visible comments on submitted public projects. |
| `/api/projects/{submission_id}/comments` | `POST` | Active member of the submission event. |
| `/api/comments/{comment_id}` | `PATCH` | The comment author only. |
| `/api/comments/{comment_id}/moderation` | `POST` | Organizer/admin of the comment's event. |

Anonymous public voting uses a server-generated, signed browser ballot cookie;
the API does not accept a caller-supplied voter identity. Hidden comments are
excluded from public reads. Only organizer/admin callers using
`include_hidden=1` can retrieve them.
