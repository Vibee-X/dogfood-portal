#!/usr/bin/env python
"""
Read-only normalization report for Dogfood Portal.

Reads the latest NormalizationRun snapshot for an event and prints:
  (a) per project: raw weighted mean, normalized mean, raw rank,
      normalized rank and rank change;
  (b) the biggest movers;
  (c) judges whose z-scores were set to 0, and why;
  (d) how incomplete reviews were treated;
  (e) data notes (duplicate titles);
  (f) a verification that recomputes every figure from the raw criterion
      values stored in the snapshot, plus a drift check against live scores.

Usage (from the repository root):
    python scripts/normalization_report.py [--event evt_01] [--top 8]

This script is not imported by the application and never writes: the
database connection is switched to read-only before any query runs.
"""
import argparse
import math
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.db import connection  # noqa: E402

from apps.events.models import Event  # noqa: E402
from apps.judging.models import JudgeAssignment, NormalizationRun, RubricCriterion  # noqa: E402

TOLERANCE = 1e-9


def make_connection_read_only():
    """Refuse writes at the database level for the rest of this process."""
    with connection.cursor() as cursor:
        if connection.vendor == "sqlite":
            cursor.execute("PRAGMA query_only = ON")
        elif connection.vendor == "postgresql":
            cursor.execute("SET default_transaction_read_only = on")
        else:
            sys.exit(f"Refusing to run: no read-only guard for {connection.vendor}.")


def competition_ranks(projects, key):
    """Rank descending by key; equal values (within TOLERANCE) share a rank (1, 2, 2, 4)."""
    ordered = sorted(projects, key=lambda p: (-p[key], p["fixture"], p["submission_id"]))
    ranks, rank, previous = {}, 0, None
    for position, project in enumerate(ordered, start=1):
        if previous is None or abs(project[key] - previous) > TOLERANCE:
            rank, previous = position, project[key]
        ranks[project["submission_id"]] = rank
    return ranks


def pstdev(values):
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


def raw_from_criteria(criteria):
    observed = sum(c["weight"] for c in criteria)
    return sum(c["weight"] * c["value"] / c["max_score"] for c in criteria) / observed


def recompute(snapshot):
    """Independently recompute weighted-zscore-v1 from the stored raw values."""
    included = [a for a in snapshot["assignments"] if a["criteria"]]
    raw = {a["assignment_id"]: raw_from_criteria(a["criteria"]) for a in included}
    by_judge = defaultdict(list)
    for a in included:
        by_judge[a["judge_id"]].append(a["assignment_id"])
    z = {}
    for ids in by_judge.values():
        values = [raw[i] for i in ids]
        mean, sd = sum(values) / len(values), pstdev(values)
        for i in ids:
            z[i] = 0.0 if len(values) < 2 or math.isclose(sd, 0.0, abs_tol=1e-8) else (raw[i] - mean) / sd
    by_project = defaultdict(list)
    for a in included:
        by_project[str(a["submission_id"])].append(a["assignment_id"])
    projects = {
        sid: (
            sum(raw[i] for i in ids) / len(ids),
            sum(z[i] for i in ids) / len(ids),
        )
        for sid, ids in by_project.items()
    }
    return raw, z, projects


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--event", default="evt_01", help="event slug (default: evt_01, the seeded fixture event)")
    parser.add_argument("--top", type=int, default=8, help="number of biggest movers to list (default: 8)")
    parser.add_argument("--project", action="append", default=[], metavar="FIXTURE_ID",
                        help="also print a per-review breakdown for this fixture id (repeatable)")
    args = parser.parse_args()

    make_connection_read_only()

    event = Event.objects.filter(slug=args.event).first()
    if event is None:
        sys.exit(f"No event with slug {args.event!r}. Seed the database first (python scripts/seed.py).")
    run = NormalizationRun.objects.filter(event=event).order_by("-computed_at", "-pk").first()
    if run is None:
        sys.exit(f"Event {args.event!r} has no NormalizationRun snapshot.")

    snap = run.snapshot
    assignments = snap["assignments"]
    judge_stats = snap["judge_statistics"]
    criteria = snap["criteria"]
    names = dict(get_user_model().objects.values_list("username", "display_name"))

    projects = [
        {
            "submission_id": p["submission_id"],
            "fixture": p["submission_fixture_id"] or "",
            "title": p["submission"],
            "reviews": p["review_count"],
            "raw": p["raw_weighted_mean"],
            "norm": p["normalized_mean"],
        }
        for p in snap["projects"].values()
    ]
    raw_rank = competition_ranks(projects, "raw")
    norm_rank = competition_ranks(projects, "norm")
    for p in projects:
        p["raw_rank"] = raw_rank[p["submission_id"]]
        p["norm_rank"] = norm_rank[p["submission_id"]]
        p["change"] = p["raw_rank"] - p["norm_rank"]  # positive = moved up

    judges_reviewing = {a["judge_id"] for a in assignments if a["criteria"]}
    print("=" * 78)
    print(f"Normalization report: {event.name} ({event.slug})")
    print(f"Run #{run.pk}, method {run.method}, computed {run.computed_at:%Y-%m-%d %H:%M:%S %Z}")
    print(f"Database: {connection.vendor} (opened read-only)")
    print(
        f"{len(projects)} ranked projects, {len(judges_reviewing)} judges with reviews, "
        f"{sum(1 for a in assignments if a['criteria'])} included reviews, "
        f"{sum(len(a['criteria']) for a in assignments)} criterion values"
    )
    # Name order: criterion ids and creation order can differ between installs.
    print("Criteria: " + ", ".join(
        f"{c['name']} (weight {c['weight']:g}, max {c['max_score']})" for c in sorted(criteria, key=lambda c: c["name"])
    ))
    print("Raw = weighted fraction of the maximum (0..1). Normalized = mean per-judge z-score.")
    print("Ranks: 1 = best; ties share the best rank. Change = raw rank - normalized rank (+ = moved up).")

    # (a) per-project table
    print("\n(a) Per-project results, ordered by normalized rank")
    header = f"{'norm':>4} {'raw':>4} {'change':>6}  {'fixture':<8} {'project':<22} {'reviews':>7} {'raw mean':>9} {'norm mean':>10}"
    print(header)
    print("-" * len(header))
    for p in sorted(projects, key=lambda p: (p["norm_rank"], p["raw_rank"], p["fixture"])):
        print(
            f"{p['norm_rank']:>4} {p['raw_rank']:>4} {p['change']:>+6}  {p['fixture']:<8} "
            f"{p['title'][:22]:<22} {p['reviews']:>7} {p['raw']:>9.4f} {p['norm']:>+10.4f}"
        )

    # (b) biggest movers
    movers = sorted(projects, key=lambda p: (-abs(p["change"]), -p["change"], p["fixture"]))[: args.top]
    print(f"\n(b) Biggest movers (top {args.top} by |rank change|)")
    for p in movers:
        direction = "up" if p["change"] > 0 else "down" if p["change"] < 0 else "unchanged"
        print(
            f"  {p['fixture']:<7} {p['title'][:22]:<22} raw #{p['raw_rank']:<3} -> normalized #{p['norm_rank']:<3} "
            f"({direction} {abs(p['change'])}; raw {p['raw']:.4f}, norm {p['norm']:+.4f}, {p['reviews']} reviews)"
        )
    unchanged = sum(1 for p in projects if p["change"] == 0)
    print(f"  Projects whose rank did not change: {unchanged} of {len(projects)}")

    # (c) judges whose z-scores were forced to 0
    judge_names = {a["judge_id"]: a["judge"] for a in assignments}
    print("\n(c) Judges whose z-scores were set to 0 (snapshot fallback = zero_variance)")
    per_judge = Counter(s["review_count"] for s in judge_stats.values())
    counts = sorted(s["review_count"] for s in judge_stats.values())
    middle = len(counts) // 2
    median = counts[middle] if len(counts) % 2 else (counts[middle - 1] + counts[middle]) / 2
    print(
        "  Reviews per judge: "
        + ", ".join(f"{n}: {j} judge{'s' if j != 1 else ''}" for n, j in sorted(per_judge.items()))
        + f" (median {median:g})"
    )
    fallback_judges = [(jid, s) for jid, s in judge_stats.items() if s["fallback"]]
    for jid, s in sorted(fallback_judges, key=lambda item: judge_names[int(item[0])]):
        username = judge_names[int(jid)]
        if s["review_count"] < 2:
            reason = f"too few reviews (n = {s['review_count']}; a z-score needs at least 2)"
        else:
            reason = f"constant scorer (all {s['review_count']} reviews scored {s['mean']:.4f}; population stddev 0)"
        print(f"  {username} ({names.get(username) or username}): {reason}")
    if not fallback_judges:
        print("  none")
    means = sorted((s["mean"], judge_names[int(jid)]) for jid, s in judge_stats.items() if not s["fallback"])
    if means:
        print(
            f"  Scale spread the z-scores remove: among the {len(means)} judges with usable z-scores, "
            f"mean raw score ranges from {means[0][0]:.4f} ({means[0][1]}) to {means[-1][0]:.4f} ({means[-1][1]})."
        )

    # (d) incomplete reviews
    n_criteria = len(criteria)
    excluded = [a for a in assignments if not a["criteria"]]
    partial = [a for a in assignments if a["criteria"] and len(a["criteria"]) < n_criteria]
    review_counts = Counter(p["reviews"] for p in projects)
    target = event.reviews_per_submission
    print("\n(d) Incomplete reviews")
    print(f"  Assignments in snapshot: {len(assignments)}")
    print(f"  Assignments with no scores (excluded, not zero-filled): {len(excluded)}")
    print(f"  Partial reviews (fewer than {n_criteria} criteria; scored on observed weights only): {len(partial)}")
    print(
        "  Reviews per project: "
        + ", ".join(f"{count} review{'s' if count != 1 else ''}: {num} project{'s' if num != 1 else ''}"
                    for count, num in sorted(review_counts.items()))
    )
    below = sorted((p for p in projects if p["reviews"] < target), key=lambda p: p["fixture"])
    print(f"  Below the event target of {target} reviews: {len(below)} project(s)"
          + (": " + ", ".join(p["fixture"] for p in below) if below else ""))
    print("  A missing review lowers only the number of z-scores averaged for that project;")
    print("  no missing value is treated as zero.")

    # (e) data notes
    print("\n(e) Data notes")
    by_title = defaultdict(list)
    for p in projects:
        by_title[p["title"].strip().casefold()].append(p)
    duplicates = [group for group in by_title.values() if len(group) > 1]
    for group in duplicates:
        print(
            f"  Duplicate title {group[0]['title']!r}: "
            + ", ".join(f"{p['fixture']} (normalized #{p['norm_rank']})" for p in sorted(group, key=lambda p: p["fixture"]))
            + " are ranked as separate projects; the portal does not flag this."
        )
    if not duplicates:
        print("  No duplicate project titles.")

    # Optional per-review breakdowns
    for fixture_id in args.project:
        rows = [a for a in assignments if a["submission_fixture_id"] == fixture_id and a["criteria"]]
        if not rows:
            print(f"\nNo included reviews for {fixture_id!r}.")
            continue
        p = next(p for p in projects if p["fixture"] == fixture_id)
        print(f"\nBreakdown for {fixture_id} {p['title']!r} (raw #{p['raw_rank']}, normalized #{p['norm_rank']})")
        print("  scores are listed per criterion in name order: "
              + "/".join(sorted(c["name"] for c in criteria)))
        print(f"  {'judge':<18} {'scores':<9} {'raw':>6} {'judge mean':>10} {'judge sd':>8} {'n':>3} {'z':>8}")
        for a in sorted(rows, key=lambda a: a["judge"]):
            s = judge_stats[str(a["judge_id"])]
            values = "/".join(str(c["value"]) for c in sorted(a["criteria"], key=lambda c: c["criterion"]))
            print(
                f"  {a['judge']:<18} {values:<9} {a['raw_weighted_score']:>6.4f} {s['mean']:>10.4f} "
                f"{s['population_stddev']:>8.4f} {s['review_count']:>3} {a['normalized_score']:>+8.4f}"
                + ("  (fallback)" if s["fallback"] else "")
            )
        print(f"  project: raw mean {p['raw']:.4f}, normalized mean {p['norm']:+.4f}")

    # (f) verification
    print("\n(f) Verification")
    raw, z, recomputed_projects = recompute(snap)
    worst = 0.0
    for a in assignments:
        if a["criteria"]:
            worst = max(worst, abs(raw[a["assignment_id"]] - a["raw_weighted_score"]),
                        abs(z[a["assignment_id"]] - a["normalized_score"]))
    for sid, (raw_mean, norm_mean) in recomputed_projects.items():
        stored = snap["projects"][sid]
        worst = max(worst, abs(raw_mean - stored["raw_weighted_mean"]), abs(norm_mean - stored["normalized_mean"]))
    consistent = worst <= 1e-9 and set(recomputed_projects) == set(snap["projects"])
    print(f"  Recomputed raw scores, z-scores and project means from the snapshot's stored criterion values:")
    print(f"  max absolute difference {worst:.2e} -> {'OK' if consistent else 'MISMATCH'}")

    active = {c.pk: c for c in RubricCriterion.objects.filter(rubric__event=event, rubric__is_active=True)}
    live = {}
    for assignment in JudgeAssignment.objects.filter(event=event).prefetch_related("scores"):
        values = [
            {"weight": active[s.criterion_id].weight, "value": s.value, "max_score": active[s.criterion_id].max_score}
            for s in assignment.scores.all() if s.criterion_id in active
        ]
        live[assignment.pk] = raw_from_criteria(values) if values else None
    snap_raw = {a["assignment_id"]: a["raw_weighted_score"] for a in assignments}
    drift = [
        aid for aid in set(live) | set(snap_raw)
        if (live.get(aid) is None) != (snap_raw.get(aid) is None)
        or (live.get(aid) is not None and abs(live[aid] - snap_raw[aid]) > TOLERANCE)
    ]
    if drift:
        print(f"  Live scores differ from this snapshot for {len(drift)} assignment(s); "
              "run a new normalization to include them.")
    else:
        print("  Live scores in the database match the snapshot exactly (no drift since it was computed).")
    print("=" * 78)
    return 0 if consistent else 1


if __name__ == "__main__":
    sys.exit(main())
