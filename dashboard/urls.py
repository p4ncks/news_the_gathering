from django.urls import path

from . import views
from .suggestions import suggest_outlet

app_name = "dashboard"
urlpatterns = [
    path("", views.index, name="index"),
    path("suggest-outlet/", suggest_outlet, name="suggest_outlet"),
    path("health/", views.health, name="health"),
    path("ready/", views.ready, name="ready"),
    path("articles/<slug:source_id>/<int:article_id>/", views.article, name="article"),
]
