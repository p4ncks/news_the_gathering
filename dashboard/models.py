"""Reader suggestions stay private until a curator reviews them."""
from django.db import models
from django.utils import timezone


class OutletSuggestion(models.Model):
    name = models.CharField(max_length=140)
    website = models.URLField(max_length=500)
    country = models.CharField(max_length=100)
    reason = models.TextField(blank=True, max_length=1000)
    submitted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-submitted_at", "-id"]

    def __str__(self):
        return self.name
