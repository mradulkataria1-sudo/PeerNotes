from django import forms
from django.conf import settings
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import Comment, Note, Report


class NoteForm(forms.ModelForm):
    class Meta:
        model = Note
        fields = ["title", "subject", "file", "description", "tags"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "tags": forms.TextInput(attrs={"placeholder": "unit-3, midterm, handwritten"}),
        }

    def clean_file(self):
        f = self.cleaned_data.get("file")
        limit = settings.MAX_NOTE_UPLOAD_MB * 1024 * 1024
        if f and hasattr(f, "size") and f.size > limit:
            raise forms.ValidationError(f"File is too large. The limit is {settings.MAX_NOTE_UPLOAD_MB} MB.")
        return f

    def clean_tags(self):
        tags = [t.strip().lower() for t in self.cleaned_data.get("tags", "").split(",") if t.strip()]
        return ", ".join(dict.fromkeys(tags))  # de-duplicate, keep order


class CommentForm(forms.ModelForm):
    class Meta:
        model = Comment
        fields = ["text"]
        widgets = {"text": forms.Textarea(attrs={"rows": 2, "placeholder": "Ask a question or add a correction"})}


class SignUpForm(UserCreationForm):
    email = forms.EmailField(required=False)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")


class ReportForm(forms.ModelForm):
    class Meta:
        model = Report
        fields = ["reason", "details"]
        widgets = {"details": forms.Textarea(attrs={"rows": 3, "maxlength": 500})}

    def clean_details(self):
        return self.cleaned_data.get("details", "").strip()
