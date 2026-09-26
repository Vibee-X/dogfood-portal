from django.contrib import admin
from .models import Team, TeamMembership, InviteLink

admin.site.register(Team)
admin.site.register(TeamMembership)
admin.site.register(InviteLink)
