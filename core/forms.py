from django import forms
from .models import Visit, Gender, TestCatalog, SampleType


class VisitRegistrationForm(forms.ModelForm):
    tests = forms.ModelMultipleChoiceField(
        queryset=TestCatalog.objects.filter(is_active=True),
        widget=forms.CheckboxSelectMultiple,
        required=True,
        help_text="Select one or more tests for this visit"
    )

    class Meta:
        model = Visit
        fields = ['patient_name', 'age', 'gender', 'phone', 'address', 'referred_by', 'notes']
        widgets = {
            'patient_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Full Name'}),
            'age': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Age in years', 'min': 1, 'max': 120}),
            'gender': forms.Select(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '10-digit mobile number'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Optional address'}),
            'referred_by': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Dr. Name or Self Referred'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Any internal clinical notes'}),
        }
