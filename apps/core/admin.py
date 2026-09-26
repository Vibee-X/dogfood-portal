from django.contrib import admin
from .models import AuditLog, Certificate

admin.site.register(AuditLog)
admin.site.register(Certificate)
