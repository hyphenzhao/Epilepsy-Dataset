from django import forms
from .models import Patient, UserProfile, UserRole
from django.contrib.auth import get_user_model

User = get_user_model()


class PatientForm(forms.ModelForm):
    birthday = forms.DateField(
        label="生日",
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "form-control",
            }
        ),
    )
    admission_date = forms.DateField(
        label="入院时间",
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "form-control",
            }
        ),
    )

    class Meta:
        model = Patient
        fields = [
            "name",
            "gender",
            "birthday",
            "handedness",
            "department",
            "bed_number",
            "admission_date",
        ]
        labels = {
            "name": "患者姓名",
            "gender": "性别",
            "handedness": "左右利手",
            "department": "科室",
            "bed_number": "床号",
            "admission_date": "入院时间",
        }

class UserWithRoleForm(forms.ModelForm):
    role = forms.ChoiceField(
        label="角色",
        choices=UserRole.choices,
    )

    class Meta:
        model = User
        fields = ["username", "email", "is_active"]
        labels = {
            "username": "用户名",
            "email": "邮箱",
            "is_active": "启用",
        }

    def save(self, commit=True):
        user = super().save(commit=commit)
        role = self.cleaned_data["role"]
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.role = role
        profile.save()
        return user
