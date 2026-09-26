from django.contrib import admin
from .models import Rubric, RubricCriterion, JudgeAssignment, Score, NormalizationRun

admin.site.register(Rubric)
admin.site.register(RubricCriterion)
admin.site.register(JudgeAssignment)
admin.site.register(Score)
admin.site.register(NormalizationRun)
