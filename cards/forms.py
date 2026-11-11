from django import forms

from .models import LearningSettings, Word
from .duplicates import existing_obverses, obverse_key


class WordForm(forms.ModelForm):
    """Only word content is editable; learning state stays server-controlled."""

    def __init__(self, *args, check_duplicates=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.check_duplicates = check_duplicates

    def clean_chunk(self):
        chunk = self.cleaned_data["chunk"]
        if self.check_duplicates and obverse_key(chunk) in existing_obverses(self.instance.pk):
            raise forms.ValidationError("A card with this Obverse already exists, including learned cards.")
        return chunk

    class Meta:
        model = Word
        fields = ["chunk", "meaning", "example", "show_reverse_immediately"]
        labels = {"chunk": "Obverse (a word or phrase you want to learn)", "meaning": "Reverse", "example": "Extra",
                  "show_reverse_immediately": "Start with reverse"}
        widgets = {
            "chunk": forms.TextInput(attrs={"placeholder": "e.g. take your time", "autofocus": True}),
            "meaning": forms.Textarea(attrs={"rows": 3}),
            "example": forms.Textarea(attrs={"rows": 4}),
            "show_reverse_immediately": forms.CheckboxInput(attrs={"role": "switch"}),
        }


class LearningSettingsForm(forms.ModelForm):
    class Meta:
        model = LearningSettings
        fields = ["review_target"]
        labels = {"review_target": "Successful reviews to learn a word"}
        help_texts = {"review_target": "Choose 1–10. Words that reach this target become Learned."}
        widgets = {"review_target": forms.NumberInput(attrs={"min": 1, "max": 10, "inputmode": "numeric"})}


class StudyActionForm(forms.Form):
    response_token = forms.UUIDField()


class BulkWordActionForm(forms.Form):
    action = forms.ChoiceField(choices=[("reset", "Reset"), ("delete", "Delete")])
    word_ids = forms.ModelMultipleChoiceField(queryset=Word.objects.all())


class WordImportForm(forms.Form):
    file = forms.FileField(
        label="CSV file",
        widget=forms.ClearableFileInput(attrs={"accept": ".csv,text/csv"}),
        help_text="UTF-8 CSV, up to 2 MB and 1,000 words.",
    )

    def clean_file(self):
        file = self.cleaned_data["file"]
        if not file.name.lower().endswith(".csv"):
            raise forms.ValidationError("Choose a .csv file.")
        if file.size > 2 * 1024 * 1024:
            raise forms.ValidationError("Choose a CSV smaller than 2 MB.")
        return file


class StudyAnswerForm(StudyActionForm):
    answer = forms.ChoiceField(choices=[("know", "Know"), ("dont_know", "Don't know"), ("repeat", "Repeat")])
