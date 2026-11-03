from django import forms

from .models import LearningSettings, Word


class WordForm(forms.ModelForm):
    """Only word content is editable; learning state stays server-controlled."""

    class Meta:
        model = Word
        fields = ["chunk", "meaning", "example"]
        labels = {"chunk": "Chunk", "meaning": "Meaning", "example": "Example"}
        help_texts = {
            "chunk": "A word or phrase you want to learn.",
            "meaning": "Optional — explain it in your own words.",
            "example": "Optional — put it in a sentence.",
        }
        widgets = {
            "chunk": forms.TextInput(attrs={"placeholder": "e.g. take your time", "autofocus": True}),
            "meaning": forms.Textarea(attrs={"rows": 3}),
            "example": forms.Textarea(attrs={"rows": 4}),
        }


class LearningSettingsForm(forms.ModelForm):
    class Meta:
        model = LearningSettings
        fields = ["review_target"]
        labels = {"review_target": "Successful reviews to learn a word"}
        help_texts = {"review_target": "Choose 1–10. A learned word moves to your archive."}
        widgets = {"review_target": forms.NumberInput(attrs={"min": 1, "max": 10, "inputmode": "numeric"})}


class StudyActionForm(forms.Form):
    response_token = forms.UUIDField()


class StudyAnswerForm(StudyActionForm):
    answer = forms.ChoiceField(choices=[("know", "Know"), ("dont_know", "Don't know"), ("repeat", "Repeat")])
