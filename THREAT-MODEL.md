# Threat Model

This document records the protections actually present in Dogfood Portal. It
does not treat a UI control as an authorization boundary.

## Sybil voting

**Threat.** One person creates many identities to cast more votes.

**Attack surface.** Public voting permits an anonymous browser ballot; an
authenticated policy permits any account; a participant policy permits any
active participant account.

**Implemented mitigation.** The organizer selects `public`, `authenticated`,
or `participants` for each event. An anonymous ballot receives a
server-generated, signed, HttpOnly cookie instead of submitting an arbitrary
voter identifier. An authenticated ballot uses the account primary key.

**Remaining gap.** The application does not perform identity verification,
email verification, device attestation, CAPTCHA, or account-age checks. A user
can clear an anonymous cookie or create accounts, so this is mitigation by
event policy rather than Sybil resistance.

## Ballot stuffing

**Threat.** A voter repeats requests to inflate a particular project's total.

**Attack surface.** `POST /api/voting/votes` accepts a submission ID while the
voting window is open.

**Implemented mitigation.** The `Vote` table has a database
`UniqueConstraint(submission, voter_ref)`, so concurrent or direct duplicate
inserts cannot create a second vote from the same ballot identity. The API also
uses a cache-backed, per-event/per-voter-reference attempt limit and records
each successful vote in `AuditLog`.

**Remaining gap.** The default cache may be process-local unless deployment
configures a shared cache, so limits are not a distributed abuse-control
system. The rate limiter does not stop a voter who changes ballot identity.

## Submission scraping

**Threat.** A caller enumerates drafts, unpublished events, or the public
ballot catalogue.

**Attack surface.** Gallery detail pages, `GET /api/voting/ballot`, and
`GET /api/projects/<id>/comments` expose submission metadata.

**Implemented mitigation.** Voting ballots and comment APIs query only
submitted submissions whose event is published. The ballot endpoint additionally
requires both an active voting window and the configured voting policy.

**Remaining gap.** Submitted projects in a published event are intentionally
public through the gallery; there is no crawler detection, response throttling
for reads, robots policy, or private-project mode.

## Judge collusion

**Threat.** Judges coordinate scores or attempt to read peers' scores before
they are authorized.

**Attack surface.** The judging score API and assignment records expose review
work.

**Implemented mitigation.** T2 filters judge reads to their own assignments,
rejects peer judge query parameters, enforces assignment and criterion event
relationships on writes, and audit-logs score writes. Organizer/admin access is
scoped to their event.

**Remaining gap.** The portal does not detect statistical collusion, conceal
scores from an event organizer, or require independent review attestation.
Normalization corrects scale differences, not coordinated intent.

## Deadline gaming

**Threat.** A participant submits after close or races a deadline boundary;
an organizer or voter attempts to act outside a configured voting window.

**Attack surface.** Submission creation/editing and the vote API use time-based
event fields.

**Implemented mitigation.** Submission writes use the event submission deadline
in server-side business logic. Voting checks the server's timezone-aware current
time and only permits `voting_start <= now < voting_end`; UI visibility is not
trusted.

**Remaining gap.** There is no external trusted timestamp, grace-period policy,
or automatic audit alert for organizers changing event dates. Administrators
with event-edit authority can still alter a deadline configuration.
