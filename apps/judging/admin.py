from django.contrib import admin
from .models import JudgeAssignment, JudgeTrack, NormalizationRun, Rubric, RubricCriterion, Score

admin.site.register(Rubric)
admin.site.register(RubricCriterion)
admin.site.register(JudgeAssignment)
admin.site.register(JudgeTrack)
admin.site.register(Score)
admin.site.register(NormalizationRun)
