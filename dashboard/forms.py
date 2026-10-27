"""Small, explicit form for proposing a news outlet; URLs are never fetched."""
from urllib.parse import urlsplit

from django import forms
from django.utils.translation import gettext_lazy as _


class OutletSuggestionForm(forms.Form):
    name = forms.CharField(label=_("Outlet name"), max_length=140)
    website = forms.URLField(label=_("Website"), max_length=500, assume_scheme="https")
    country = forms.CharField(label=_("Country or region"), max_length=100, required=False)
    reason = forms.CharField(label=_("Why would you like to see it here?"),
                             required=False, max_length=1000,
                             widget=forms.Textarea(attrs={"rows": 3}),
                             help_text=_("Optional. Please avoid personal information."))
    # A passive bot trap. It is not a public field or an additional reader step.
    website_confirmation = forms.CharField(required=False, widget=forms.HiddenInput)
    return_query = forms.CharField(required=False, max_length=4000, widget=forms.HiddenInput)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("website_confirmation"):
            raise forms.ValidationError(_("Unable to send this suggestion. Please reload the page and try again."))
        return cleaned

    def clean_website(self):
        website = self.cleaned_data["website"]
        parsed = urlsplit(website)
        if parsed.scheme not in {"http", "https"} or parsed.username is not None or parsed.password is not None:
            raise forms.ValidationError(_("Enter a public website address using http or https, without login details."))
        return website
