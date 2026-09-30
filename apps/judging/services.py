"""Server-side judging rules shared by the API, dashboard, and seed command."""
from collections import Counter, defaultdict, deque
from itertools import combinations

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import EventMembership
from apps.core.models import AuditLog
from apps.teams.models import TeamMembership
from .models import (
    JudgeAssignment,
    JudgeTrack,
    NormalizationRun,
    PairwiseComparison,
    RubricCriterion,
    Score,
)


class AssignmentError(ValueError):
    """The event's eligible judges cannot satisfy an assignment batch."""


class PairwisePermissionError(PermissionError):
    """The caller is not permitted to access or complete this comparison."""


class PairwiseStateError(ValueError):
    """The pair is already completed or does not have a valid next state."""


class PairwiseValidationError(ValueError):
    """A requested pair or winner violates pairwise business rules."""


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


def judge_can_compare_pair(judge, event, submission_a, submission_b):
    """A pair is valid only when both projects remain assigned and in scope."""
    if submission_a.pk == submission_b.pk:
        return False
    if submission_a.event_id != event.pk or submission_b.event_id != event.pk:
        return False
    if submission_a.status != "submitted" or submission_b.status != "submitted":
        return False
    if not (
        judge_can_review_submission(judge, event, submission_a)
        and judge_can_review_submission(judge, event, submission_b)
    ):
        return False
    assigned_ids = set(
        JudgeAssignment.objects.filter(
            event=event,
            judge=judge,
            submission_id__in=[submission_a.pk, submission_b.pk],
        ).values_list("submission_id", flat=True)
    )
    return assigned_ids == {submission_a.pk, submission_b.pk}


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


@transaction.atomic
def generate_pairwise_comparisons(event, *, actor=None):
    """Create all currently authorized judge pairs in deterministic order.

    Pair candidates are combinations of a judge's existing, valid
    ``JudgeAssignment`` submissions. This preserves track scope and the
    no-self-team rule already enforced by assignment generation. Existing rows
    are retained, so repeat calls are idempotent and never erase decisions.
    """
    assignments = (
        JudgeAssignment.objects.select_related("judge", "submission", "submission__team")
        .filter(event=event, submission__status="submitted")
        .order_by("judge_id", "submission_id", "pk")
    )
    submissions_by_judge = defaultdict(dict)
    for assignment in assignments:
        if judge_can_review_submission(assignment.judge, event, assignment.submission):
            submissions_by_judge[assignment.judge_id][assignment.submission_id] = assignment.submission

    existing_pairs = set(
        PairwiseComparison.objects.select_for_update()
        .filter(event=event)
        .values_list("judge_id", "submission_a_id", "submission_b_id")
    )
    created = 0
    candidate_count = 0
    for judge_id in sorted(submissions_by_judge):
        submission_ids = sorted(submissions_by_judge[judge_id])
        for submission_a_id, submission_b_id in combinations(submission_ids, 2):
            candidate_count += 1
            pair_key = (judge_id, submission_a_id, submission_b_id)
            if pair_key in existing_pairs:
                continue
            comparison = PairwiseComparison(
                event=event,
                judge_id=judge_id,
                submission_a_id=submission_a_id,
                submission_b_id=submission_b_id,
            )
            comparison.save()
            AuditLog.objects.create(
                actor=actor,
                action="pairwise.comparison.assigned",
                target_type="PairwiseComparison",
                target_id=str(comparison.pk),
                metadata={
                    "event": event.slug,
                    "judge_id": judge_id,
                    "submission_a_id": comparison.submission_a_id,
                    "submission_b_id": comparison.submission_b_id,
                },
            )
            existing_pairs.add(pair_key)
            created += 1
    return {
        "created": created,
        "existing": len(existing_pairs) - created,
        "candidate_pairs": candidate_count,
        "judges": len(submissions_by_judge),
    }


def next_pairwise_comparison(event, judge):
    """Return the first remaining valid pair for this judge, if any."""
    if event_role(judge, event) != EventMembership.Role.JUDGE:
        raise PairwisePermissionError("Only active event judges can compare projects.")
    comparisons = (
        PairwiseComparison.objects.filter(event=event, judge=judge, winner__isnull=True)
        .select_related("submission_a__track", "submission_b__track")
        .order_by("submission_a_id", "submission_b_id", "pk")
    )
    for comparison in comparisons:
        if judge_can_compare_pair(judge, event, comparison.submission_a, comparison.submission_b):
            return comparison
    return None


@transaction.atomic
def record_pairwise_winner(event, judge, comparison_id, winner_id, *, actor=None):
    """Persist one judge-owned binary decision and its audit record."""
    comparison = (
        PairwiseComparison.objects.select_for_update()
        .select_related("submission_a", "submission_b", "event")
        .filter(pk=comparison_id, event=event)
        .first()
    )
    if not comparison or comparison.judge_id != judge.pk:
        raise PairwisePermissionError("You can only complete your own pairwise comparison.")
    if event_role(judge, event) != EventMembership.Role.JUDGE or not judge_can_compare_pair(
        judge, event, comparison.submission_a, comparison.submission_b
    ):
        raise PairwisePermissionError("This comparison is no longer authorized for this judge.")
    if comparison.winner_id:
        raise PairwiseStateError("This comparison is already complete.")
    if winner_id not in {comparison.submission_a_id, comparison.submission_b_id}:
        raise PairwiseValidationError("The winner must be one of the assigned pair submissions.")
    comparison.winner_id = winner_id
    try:
        comparison.save(update_fields=["winner"])
    except ValidationError as exc:
        raise PairwiseValidationError(exc.message_dict) from exc
    AuditLog.objects.create(
        actor=actor or judge,
        action="pairwise.comparison.completed",
        target_type="PairwiseComparison",
        target_id=str(comparison.pk),
        metadata={
            "event": event.slug,
            "submission_a_id": comparison.submission_a_id,
            "submission_b_id": comparison.submission_b_id,
            "winner_id": comparison.winner_id,
        },
    )
    return comparison


def _connected_pairwise_components(submission_ids, comparisons):
    """Return connected submission-id components in deterministic order."""
    adjacency = {submission_id: set() for submission_id in submission_ids}
    for comparison in comparisons:
        adjacency[comparison.submission_a_id].add(comparison.submission_b_id)
        adjacency[comparison.submission_b_id].add(comparison.submission_a_id)

    components = []
    visited = set()
    for start in sorted(submission_ids):
        if start in visited or not adjacency[start]:
            continue
        component = []
        queue = deque([start])
        visited.add(start)
        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbor in sorted(adjacency[current]):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        components.append(sorted(component))
    return components


def bradley_terry_ranking(event):
    """Return deterministic, component-scoped Bradley--Terry estimates.

    A component's smallest submission ID is fixed at log-strength zero to make
    the otherwise shift-invariant model identifiable. L-BFGS-B bounds every
    free log-strength to [-20, 20], yielding a finite constrained MLE even for
    a completely one-sided (separated) comparison history. Components are not
    comparable to each other; submissions without comparisons are explicitly
    returned as unranked.
    """
    submissions = list(
        event.submissions.filter(status="submitted").order_by("pk").only("pk", "title", "fixture_id")
    )
    submissions_by_id = {submission.pk: submission for submission in submissions}
    completed = list(
        PairwiseComparison.objects.filter(
            event=event,
            winner__isnull=False,
            submission_a__status="submitted",
            submission_b__status="submitted",
        )
        .select_related("submission_a", "submission_b", "winner")
        .order_by("pk")
    )
    comparison_count = Counter()
    wins = Counter()
    losses = Counter()
    for comparison in completed:
        comparison_count[comparison.submission_a_id] += 1
        comparison_count[comparison.submission_b_id] += 1
        loser_id = (
            comparison.submission_b_id
            if comparison.winner_id == comparison.submission_a_id
            else comparison.submission_a_id
        )
        wins[comparison.winner_id] += 1
        losses[loser_id] += 1

    rows_by_submission = {
        submission.pk: {
            "submission_id": submission.pk,
            "fixture_id": submission.fixture_id,
            "title": submission.title,
            "component_id": None,
            "rank_in_component": None,
            "strength": None,
            "win_probability_vs_component_anchor": None,
            "comparisons": comparison_count[submission.pk],
            "wins": wins[submission.pk],
            "losses": losses[submission.pk],
        }
        for submission in submissions
    }
    components = []
    for component_number, submission_ids in enumerate(
        _connected_pairwise_components(list(submissions_by_id), completed), start=1
    ):
        component_set = set(submission_ids)
        component_comparisons = [
            comparison
            for comparison in completed
            if comparison.submission_a_id in component_set and comparison.submission_b_id in component_set
        ]
        anchor_id = submission_ids[0]
        free_ids = submission_ids[1:]

        def negative_log_likelihood(values):
            theta = {anchor_id: 0.0, **dict(zip(free_ids, values))}
            return float(sum(
                np.logaddexp(0.0, -(theta[comparison.winner_id] - theta[
                    comparison.submission_b_id
                    if comparison.winner_id == comparison.submission_a_id
                    else comparison.submission_a_id
                ]))
                for comparison in component_comparisons
            ))

        def gradient(values):
            theta = {anchor_id: 0.0, **dict(zip(free_ids, values))}
            gradients = {submission_id: 0.0 for submission_id in free_ids}
            for comparison in component_comparisons:
                winner_id = comparison.winner_id
                loser_id = (
                    comparison.submission_b_id
                    if winner_id == comparison.submission_a_id
                    else comparison.submission_a_id
                )
                probability = float(expit(theta[winner_id] - theta[loser_id]))
                if winner_id in gradients:
                    gradients[winner_id] += probability - 1.0
                if loser_id in gradients:
                    gradients[loser_id] += 1.0 - probability
            return np.array([gradients[submission_id] for submission_id in free_ids], dtype=float)

        optimizer = minimize(
            negative_log_likelihood,
            x0=np.zeros(len(free_ids), dtype=float),
            jac=gradient,
            method="L-BFGS-B",
            bounds=[(-20.0, 20.0)] * len(free_ids),
        )
        if not optimizer.success or not np.all(np.isfinite(optimizer.x)):
            # The bounded objective is expected to converge for finite inputs.
            # Explicitly surface an exceptional optimizer failure rather than
            # manufacturing a ranking value.
            raise RuntimeError(f"Bradley-Terry optimization failed: {optimizer.message}")
        theta = {anchor_id: 0.0, **dict(zip(free_ids, [float(value) for value in optimizer.x]))}
        component_id = f"component-{component_number}"
        ordered_ids = sorted(submission_ids, key=lambda submission_id: (-theta[submission_id], submission_id))
        for rank, submission_id in enumerate(ordered_ids, start=1):
            rows_by_submission[submission_id].update({
                "component_id": component_id,
                "rank_in_component": rank,
                "strength": float(np.exp(theta[submission_id])),
                "win_probability_vs_component_anchor": float(expit(theta[submission_id])),
            })
        components.append({
            "id": component_id,
            "submission_ids": ordered_ids,
            "anchor_submission_id": anchor_id,
            "comparison_count": len(component_comparisons),
            "optimizer_success": bool(optimizer.success),
            "objective": float(optimizer.fun),
        })

    ranked_rows = [
        rows_by_submission[submission_id]
        for component in components
        for submission_id in component["submission_ids"]
    ]
    ranked_ids = {row["submission_id"] for row in ranked_rows}
    ranked_rows.extend(
        rows_by_submission[submission_id]
        for submission_id in sorted(rows_by_submission) if submission_id not in ranked_ids
    )
    return {
        "event": event.slug,
        "method": "bradley-terry-bounded-mle-v1",
        "components": components,
        "rankings": ranked_rows,
    }


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
    z=0 for every review, which never divides by zero and stops a constant
    scale from ordering projects. Those zeros are still averaged into project
    means, pulling a project toward the event average and diluting the other
    judges' z-scores for it (see JUDGING.md, "Known weaknesses of this method").
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
