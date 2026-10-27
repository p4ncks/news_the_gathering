"""Receive suggestions locally without fetching or publicly listing submissions."""
import logging
from urllib.parse import parse_qsl, urlencode

from django.db import DatabaseError
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from news import database

from .forms import OutletSuggestionForm
from .models import OutletSuggestion

LOG = logging.getLogger(__name__)
SELECTION_PARAMETERS = {"filtered", "q", "continent", "outlet", "view", "show_empty", "enhanced"}


def selection_query(value):
    """Keep only dashboard filters, so the return destination always stays local."""
    try:
        pairs = parse_qsl(value, keep_blank_values=True, max_num_fields=100)
    except ValueError:
        return ""
    return urlencode([(key, value) for key, value in pairs if key in SELECTION_PARAMETERS])


def suggestion_context(request):
    return {
        "suggestion_form": OutletSuggestionForm(initial={"return_query": selection_query(request.GET.urlencode())}),
        "suggestion_received": request.GET.get("suggested") == "1",
    }


@require_POST
@never_cache
def suggest_outlet(request):
    form = OutletSuggestionForm(request.POST)
    unavailable = False
    if form.is_valid():
        try:
            with database._using() as alias:
                OutletSuggestion.objects.using(alias).create(**{
                    name: form.cleaned_data[name] for name in ("name", "website", "country", "reason")
                })
        except (DatabaseError, OSError):
            LOG.exception("Could not save outlet suggestion")
            form.add_error(None, _("Suggestions are temporarily unavailable. Please try again later."))
            unavailable = True
        if not form.errors:
            query = selection_query(form.cleaned_data["return_query"])
            destination = reverse("dashboard:index") + "?" + (query + "&" if query else "") + "suggested=1#suggest-outlet"
            return HttpResponseRedirect(destination, status=303)

    # The form keeps the reader's values and clear errors without storing drafts
    # or putting submitted names/reasons into a redirect URL.
    for name, errors in form.errors.items():
        if name in form.fields:
            form.fields[name].widget.attrs.update({"aria-invalid": "true", "aria-describedby": "suggest-" + name + "-errors"})
    back_to_desk = reverse("dashboard:index") + "?" + selection_query(request.POST.get("return_query", "")) + "#suggest-outlet"
    return render(request, "dashboard/suggestion_error.html", {
        "suggestion_form": form,
        "suggestion_open": True,
        "back_to_desk": back_to_desk,
        "language_next": back_to_desk,
    }, status=503 if unavailable else 200)
