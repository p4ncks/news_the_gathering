"""Shared storage models for the collector and Django reader.

Source definitions stay in sources.py. A fixed-size URL digest keeps very long
URLs out of the article uniqueness index.
"""
from django.db import models
from django.utils import timezone


class Article(models.Model):
    source_id = models.CharField(max_length=64)
    url = models.TextField()
    url_hash = models.CharField(max_length=64)
    title = models.TextField()
    summary = models.TextField(default="", blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    collected_at = models.DateTimeField(default=timezone.now)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now, db_index=True)
    body = models.TextField(default="", blank=True)
    body_fetched_at = models.DateTimeField(null=True, blank=True)
    body_metadata_hash = models.CharField(max_length=64, default="", blank=True)

    class Meta:
        db_table = "articles"
        constraints = [
            models.UniqueConstraint(fields=["source_id", "url_hash"], name="article_source_url_hash"),
        ]


class SourceStatus(models.Model):
    source_id = models.CharField(max_length=64, primary_key=True)
    attempted_at = models.DateTimeField(default=timezone.now)
    succeeded_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(default="", blank=True)

    class Meta:
        db_table = "source_status"


class CurrentHeadline(models.Model):
    source_id = models.CharField(max_length=64)
    rank = models.PositiveSmallIntegerField()
    article = models.ForeignKey(Article, on_delete=models.PROTECT, related_name="current_headlines")

    class Meta:
        db_table = "headlines"
        ordering = ["rank"]
        constraints = [
            models.UniqueConstraint(fields=["source_id", "rank"], name="headline_source_rank"),
            models.CheckConstraint(condition=models.Q(rank__gte=1, rank__lte=10), name="headline_rank_1_to_10"),
        ]


class Edition(models.Model):
    source_id = models.CharField(max_length=64, db_index=True)
    collected_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "editions"
        ordering = ["-collected_at", "-id"]


class EditionHeadline(models.Model):
    edition = models.ForeignKey(Edition, on_delete=models.CASCADE, related_name="headlines")
    article = models.ForeignKey(Article, on_delete=models.PROTECT, related_name="edition_headlines")
    rank = models.PositiveSmallIntegerField()
    # These values intentionally do not change when the current article changes.
    title = models.TextField()
    summary = models.TextField(default="", blank=True)
    url = models.TextField()
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "edition_headlines"
        ordering = ["rank"]
        constraints = [
            models.UniqueConstraint(fields=["edition", "rank"], name="edition_headline_rank"),
            models.CheckConstraint(condition=models.Q(rank__gte=1, rank__lte=10), name="edition_rank_1_to_10"),
        ]


class CollectionRun(models.Model):
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(default=timezone.now, db_index=True)
    sources_attempted = models.PositiveIntegerField(default=0)
    sources_succeeded = models.PositiveIntegerField(default=0)
    sources_failed = models.PositiveIntegerField(default=0)
    diagnostics = models.JSONField(default=list)

    class Meta:
        db_table = "collection_runs"
        ordering = ["-finished_at", "-id"]
