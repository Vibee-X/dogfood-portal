import secrets

from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.models import EventMembership
from apps.events.models import Event
from apps.submissions.models import Submission
from apps.teams.models import TeamMembership
from .models import AuditLog, Certificate


PARTICIPATION_CERTIFICATE = "participation"


def home(request):
    """Home page — redirects to the project gallery."""
    return redirect("submissions:gallery")


def _issue_participation_certificate(user, event):
    """Return one stable certificate for an active member of an event."""
    with transaction.atomic():
        membership = EventMembership.objects.select_for_update().filter(
            user=user,
            event=event,
            status=EventMembership.Status.ACTIVE,
        ).first()
        if membership is None:
            return None, False

        certificate = Certificate.objects.filter(
            event=event,
            user=user,
            type=PARTICIPATION_CERTIFICATE,
        ).order_by("pk").first()
        if certificate:
            return certificate, False

        team_membership = TeamMembership.objects.filter(
            user=user,
            team__event=event,
        ).select_related("team").order_by("team_id").first()
        team = team_membership.team if team_membership else None

        # The verification code is stored with a database uniqueness constraint.
        # Retrying inside a savepoint also handles the vanishingly rare collision.
        for _ in range(3):
            try:
                with transaction.atomic():
                    certificate = Certificate.objects.create(
                        event=event,
                        user=user,
                        team=team,
                        type=PARTICIPATION_CERTIFICATE,
                        verification_code=secrets.token_urlsafe(18),
                    )
            except IntegrityError:
                continue

            AuditLog.objects.create(
                actor=user,
                action="certificate.issued",
                target_type="certificate",
                target_id=str(certificate.pk),
                metadata={
                    "event_id": event.pk,
                    "certificate_type": PARTICIPATION_CERTIFICATE,
                    "verification_code": certificate.verification_code,
                },
            )
            return certificate, True

    raise RuntimeError("Unable to issue a unique certificate verification code.")


@login_required
@require_POST
def issue_participation_certificate(request, event_slug):
    """Issue an authenticated event member's printable certificate."""
    event = get_object_or_404(Event, slug=event_slug)
    certificate, _ = _issue_participation_certificate(request.user, event)
    if certificate is None:
        return HttpResponseForbidden("Only active event members can request certificates.")
    return redirect("core:certificate_verify", verification_code=certificate.verification_code)


@require_GET
def certificate_verify(request, verification_code):
    """Public verification page for an opaque certificate code."""
    certificate = get_object_or_404(
        Certificate.objects.select_related("event", "user", "team"),
        verification_code=verification_code,
        type=PARTICIPATION_CERTIFICATE,
    )
    submitted_projects = Submission.objects.none()
    if certificate.team_id:
        submitted_projects = certificate.team.submissions.filter(
            event=certificate.event,
            status=Submission.Status.SUBMITTED,
        ).order_by("title")
    return render(request, "core/certificate_verify.html", {
        "certificate": certificate,
        "submitted_projects": submitted_projects,
    })
