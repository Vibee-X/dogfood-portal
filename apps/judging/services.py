"""Server-side judging rules shared by the API, dashboard, and seed command."""
from collections import Counter, defaultdict

import numpy as np
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import EventMembership
from apps.teams.models import TeamMembership
from .models import JudgeAssignment, JudgeTrack, NormalizationRun, RubricCriterion, Score


class AssignmentError(ValueError):
    """The event's eligible judges cannot satisfy an assignment batch."""


def event_role(user, event):
    membership = EventMembership.objects.filter(
        user=user,
        event=event,
        status=EventMembership.Status.ACTIVE,
    ).only("role").first()
    return membership.role if membership else None


def is_organizer(user, event):
    return event_role(user, event) in {
        EventMembership.Role.ORGANIZER,
        EventMembership.Role.ADMIN,
    }


def judge_track_ids(event, judge):
    return set(
        JudgeTrack.objects.filter(event=event, judge=judge).values_list("track_id", flat=True)
    )


def judge_can_review_submission(judge, event, submission):
    """True only for an active event judge who is eligible for this project."""
    if event_role(judge, event) != EventMembership.Role.JUDGE:
        return False
    if submission.event_id != event.id:
        return False
    if TeamMembership.objects.filter(team=submission.team, user=judge).exists():
        return False

    scoped_tracks = judge_track_ids(event, judge)
    # A judge with no JudgeTrack rows is an event-wide judge. Otherwise a
    # submission must be in one of their explicitly assigned event tracks.
    return not scoped_tracks or submission.track_id in scoped_tracks


def _eligible_judges(event, submission):
    judge_ids = EventMembership.objects.filter(
        event=event,
        role=EventMembership.Role.JUDGE,
        status=EventMembership.Status.ACTIVE,
    ).values_list("user_id", flat=True)
    from apps.accounts.models import User

    judges = User.objects.filter(pk__in=judge_ids).order_by("pk")
    return [judge for judge in judges if judge_can_review_submission(judge, event, submission)]


@transaction.atomic
def generate_assignments(event):
    """Generate a deterministic, idempotent, balanced assignment batch.

    Submitted projects are processed in primary-key order. Among each
    project's eligible judges, the least-loaded judge wins; primary key breaks
    ties. Track scope and self-review exclusions take precedence over balance.
    Existing assignments are retained, so a repeat call makes no changes once
    every project has the configured number of reviews.
    """
    target = event.reviews_per_submission
    if target < 1:
        raise AssignmentError("reviews_per_submission must be at least one.")

    submissions = list(
        event.submissions.filter(status="submitted").select_related("team", "track").order_by("pk")
    )
    if not submissions:
        return {"created": 0, "existing": 0, "target": target}

    existing = list(
        JudgeAssignment.objects.select_for_update()
        .filter(event=event, submission__in=submissions)
        .select_related("submission", "judge")
    )
    by_submission = defaultdict(list)
    loads = Counter()
    for assignment in existing:
        by_submission[assignment.submission_id].append(assignment)
        loads[assignment.judge_id] += 1

    for submission in submissions:
        if len(by_submission[submission.pk]) > target:
            raise AssignmentError(
                f"{submission.title!r} already has more than {target} assignments; "
                "completed review history was left untouched."
            )
        eligible = _eligible_judges(event, submission)
        if len(eligible) < target:
            raise AssignmentError(
                f"{submission.title!r} has only {len(eligible)} eligible judges for {target} reviews."
            )

    created = 0
    batch_id = f"assignment-v1-{event.pk}-{target}"
    for submission in submissions:
        assigned_ids = {assignment.judge_id for assignment in by_submission[submission.pk]}
        missing = target - len(assigned_ids)
        candidates = [
            judge for judge in _eligible_judges(event, submission)
            if judge.pk not in assigned_ids
        ]
        if len(candidates) < missing:
            raise AssignmentError(
                f"{submission.title!r} lacks enough unassigned eligible judges."
            )

        for _ in range(missing):
            judge = min(candidates, key=lambda candidate: (loads[candidate.pk], candidate.pk))
            assignment = JudgeAssignment(
                event=event,
                judge=judge,
                submission=submission,
                batch_id=batch_id,
            )
            assignment.full_clean()
            assignment.save()
            loads[judge.pk] += 1
            candidates.remove(judge)
            created += 1

    return {"created": created, "existing": len(existing), "target": target}


def judge_progress(event):
    """Return deterministic per-judge completion counts for organizers."""
    judge_memberships = EventMembership.objects.filter(
        event=event,
        role=EventMembership.Role.JUDGE,
        status=EventMembership.Status.ACTIVE,
    ).select_related("user").order_by("user__username")
    assignments = JudgeAssignment.objects.filter(event=event).only("judge_id", "status")
    counts = defaultdict(lambda: Counter())
    for assignment in assignments:
        counts[assignment.judge_id]["assigned"] += 1
        if assignment.status == JudgeAssignment.Status.COMPLETED:
            counts[assignment.judge_id]["completed"] += 1

    progress = []
    for membership in judge_memberships:
        assigned = counts[membership.user_id]["assigned"]
        completed = counts[membership.user_id]["completed"]
        progress.append({
            "judge_id": membership.user_id,
            "judge": membership.user.username,
            "assigned": assigned,
            "completed": completed,
            "remaining": assigned - completed,
            "completion_percentage": round((completed / assigned * 100) if assigned else 0, 2),
        })
    return progress


def _assignment_raw_score(assignment, criteria):
    """Compute a weighted 0..1 score from criteria actually submitted."""
    criterion_by_id = {criterion.pk: criterion for criterion in criteria}
    values = []
    for score in assignment.scores.select_related("criterion").filter(criterion__in=criteria):
        criterion = criterion_by_id[score.criterion_id]
        values.append({
            "criterion_id": criterion.pk,
            "criterion": criterion.name,
            "weight": float(criterion.weight),
            "max_score": criterion.max_score,
            "value": score.value,
        })
    if not values:
        return None, values, 0.0

    observed_weight = sum(item["weight"] for item in values)
    raw = sum(
        item["weight"] * (item["value"] / item["max_score"])
        for item in values
    ) / observed_weight
    return float(raw), values, float(observed_weight)


@transaction.atomic
def run_normalization(event):
    """Persist a reproducible weighted, per-judge z-score snapshot.

    A partially completed review uses only its observed criterion weights. An
    assignment without any score is omitted from the ranking. A judge with
    fewer than two observed reviews or zero population standard deviation gets
    z=0 for every review: their constant scale contributes no artificial rank
    signal and never divides by zero.
    """
    criteria = list(
        RubricCriterion.objects.filter(rubric__event=event, rubric__is_active=True)
        .select_related("rubric")
        .order_by("rubric_id", "pk")
    )
    assignments = list(
        JudgeAssignment.objects.filter(event=event)
        .select_related("judge", "submission", "submission__track")
        .prefetch_related("scores__criterion")
        .order_by("judge_id", "submission_id", "pk")
    )

    records = []
    per_judge = defaultdict(list)
    for assignment in assignments:
        raw, values, observed_weight = _assignment_raw_score(assignment, criteria)
        record = {
            "assignment_id": assignment.pk,
            "judge_id": assignment.judge_id,
            "judge": assignment.judge.username,
            "submission_id": assignment.submission_id,
            "submission_fixture_id": assignment.submission.fixture_id,
            "submission": assignment.submission.title,
            "track_id": assignment.submission.track_id,
            "assignment_status": assignment.status,
            "criteria": values,
            "observed_weight": observed_weight,
            "raw_weighted_score": raw,
            "included": raw is not None,
        }
        records.append(record)
        if raw is not None:
            per_judge[assignment.judge_id].append(record)

    judge_stats = {}
    for judge_id, judge_records in per_judge.items():
        raw_values = np.array([record["raw_weighted_score"] for record in judge_records], dtype=float)
        mean = float(np.mean(raw_values))
        stddev = float(np.std(raw_values, ddof=0))
        fallback = len(raw_values) < 2 or np.isclose(stddev, 0.0)
        judge_stats[str(judge_id)] = {
            "mean": mean,
            "population_stddev": stddev,
            "review_count": len(raw_values),
            "fallback": "zero_variance" if fallback else None,
        }
        for record in judge_records:
            record["normalized_score"] = 0.0 if fallback else float(
                (record["raw_weighted_score"] - mean) / stddev
            )

    projects = defaultdict(list)
    for record in records:
        if record["included"]:
            projects[str(record["submission_id"])].append(record)

    project_summary = {}
    for submission_id, project_records in projects.items():
        project_summary[submission_id] = {
            "submission_id": int(submission_id),
            "submission_fixture_id": project_records[0]["submission_fixture_id"],
            "submission": project_records[0]["submission"],
            "review_count": len(project_records),
            "raw_weighted_mean": float(np.mean([
                record["raw_weighted_score"] for record in project_records
            ])),
            "normalized_mean": float(np.mean([
                record["normalized_score"] for record in project_records
            ])),
        }

    snapshot = {
        "version": 1,
        "formula": {
            "raw": "sum(weight * (value / max_score)) / sum(observed weight)",
            "normalized": "(raw_weighted_score - judge_mean) / judge_population_stddev",
            "project": "mean(normalized_score for included reviews)",
            "incomplete_reviews": "use only observed criterion weights; omit reviews with no scores",
            "zero_variance": "normalized_score = 0 when n < 2 or population_stddev = 0",
        },
        "criteria": [
            {
                "id": criterion.pk,
                "rubric": criterion.rubric.name,
                "name": criterion.name,
                "weight": float(criterion.weight),
                "max_score": criterion.max_score,
            }
            for criterion in criteria
        ],
        "judge_statistics": judge_stats,
        "assignments": records,
        "projects": project_summary,
    }
    return NormalizationRun.objects.create(
        event=event,
        method="weighted-zscore-v1",
        snapshot=snapshot,
    )
