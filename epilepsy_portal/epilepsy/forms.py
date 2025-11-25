from django import forms
from .models import Patient, UserProfile, UserRole
from django.contrib.auth import get_user_model

User = get_user_model()

class PatientForm(forms.ModelForm):
    # 日期字段：生日、入院时间、评估日期 —— 日历选择
    birthday = forms.DateField(
        label="生日",
        required=True,
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "form-control",
            }
        ),
    )

    admission_date = forms.DateField(
        label="入院时间",
        required=True,
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "form-control",
            }
        ),
    )

    evaluation_date = forms.DateField(
        label="评估日期",
        required=False,
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "form-control",
            }
        ),
    )

    # 既往不良病史：多选 + 勾选框
    past_medical_history = forms.MultipleChoiceField(
        label="既往不良病史",
        required=False,
        choices=Patient.PAST_MEDICAL_HISTORY_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )

    # 其他病史：多选 + 勾选框
    other_medical_history = forms.MultipleChoiceField(
        label="其他病史",
        required=False,
        choices=Patient.OTHER_MEDICAL_HISTORY_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Patient
        fields = [
            # 基本信息
            "name",
            "gender",
            "birthday",
            "handedness",
            "department",
            "bed_number",
            "admission_date",

            # 病史
            "pregnancy_birth_history",
            "education_level",
            "occupation",
            "past_medical_history",
            "other_medical_history",
            "family_history",
            "first_seizure_age",
            "first_seizure_description",
            "medication_history",

            # 发作症状学
            "aura",
            "typical_seizure_time",
            "typical_seizure_semiology",
            "seizure_duration_seconds",
            "seizure_duration_minutes",
            "seizure_freq_per_day",
            "seizure_freq_per_week",
            "seizure_freq_per_month",
            "seizure_freq_per_year",

            # 神经系统检查
            "neuro_exam",
            "neuro_exam_description",

            # 认知和精神量表
            "moca_score",
            "hama_score",
            "hamd_score",
            "bai_score",
            "bdi_score",
            "epilepsy_scale_score",

            # 视频头皮 EEG
            "eeg_interictal",
            "eeg_ictal",
            "eeg_clinical_correlation",

            # 影像学检查
            "mri_brief",
            "mri_link",
            "pet_brief",
            "pet_link",

            # 一期无创评估
            "first_stage_lateralization",
            "first_stage_region",
            "first_stage_location",

            # SEEG
            "seeg_interictal_overall",
            "seeg_group1",
            "seeg_group2",
            "seeg_group3",
            "seeg_ictal",

            # 二期有创评估
            "second_stage_core_zone",
            "second_stage_hypothesis_zone",

            # 外科切除计划
            "resection_plan_convex",
            "resection_plan_concave",

            # 评估人/日期
            "evaluator",
            "evaluation_date",
        ]
        labels = {
            "name": "患者姓名",
            "gender": "性别",
            "handedness": "左右利手",
            "department": "科室",
            "bed_number": "床号",
            "admission_date": "入院时间",

            "pregnancy_birth_history": "母孕出生史",
            "education_level": "受教育程度",
            "occupation": "职业",
            "past_medical_history": "既往不良病史",
            "other_medical_history": "其他病史",
            "family_history": "家族病史",
            "first_seizure_age": "首次发作年龄（岁）",
            "first_seizure_description": "首次发作简要表现形式",
            "medication_history": "药物治疗过程",

            "aura": "先兆",
            "typical_seizure_time": "惯常发作时间",
            "typical_seizure_semiology": "惯常发作表现形式",
            "seizure_duration_seconds": "发作持续时间（秒）",
            "seizure_duration_minutes": "发作持续时间（分钟）",
            "seizure_freq_per_day": "发作频率（次/天）",
            "seizure_freq_per_week": "发作频率（次/周）",
            "seizure_freq_per_month": "发作频率（次/月）",
            "seizure_freq_per_year": "发作频率（次/年）",

            "neuro_exam": "神经系统检查",
            "neuro_exam_description": "神经系统检查异常描述",

            "moca_score": "MoCA 评分",
            "hama_score": "HAMA 评分",
            "hamd_score": "HAMD 评分",
            "bai_score": "BAI 评分",
            "bdi_score": "BDI 评分",
            "epilepsy_scale_score": "癫痫量表评分",

            "eeg_interictal": "EEG 发作间期放电",
            "eeg_ictal": "EEG 发作期放电",
            "eeg_clinical_correlation": "EEG 同步发作临床症状",

            "mri_brief": "MRI 简要描述",
            "mri_link": "MRI 图像下载链接",
            "pet_brief": "PET 简要描述",
            "pet_link": "PET 图像下载链接",

            "first_stage_lateralization": "一期无创评估定侧",
            "first_stage_region": "一期无创评估定区域",
            "first_stage_location": "一期无创评估定具体部位",

            "seeg_interictal_overall": "SEEG 发作间期放电总体描述",
            "seeg_group1": "SEEG 发作间期 Group 1",
            "seeg_group2": "SEEG 发作间期 Group 2",
            "seeg_group3": "SEEG 发作间期 Group 3",
            "seeg_ictal": "SEEG 发作期放电",

            "second_stage_core_zone": "二期有创评估核心区域",
            "second_stage_hypothesis_zone": "二期有创评估假设区域",

            "resection_plan_convex": "外科切除计划-凸面",
            "resection_plan_concave": "外科切除计划-凹面",

            "evaluator": "评估人",
            "evaluation_date": "评估日期",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 初始化多选框数据（模型里存的是用逗号连接的字符串）
        if self.instance and self.instance.pk:
            self.initial["past_medical_history"] = (
                self.instance.past_medical_history.split(",")
                if self.instance.past_medical_history
                else []
            )
            self.initial["other_medical_history"] = (
                self.instance.other_medical_history.split(",")
                if self.instance.other_medical_history
                else []
            )

    def clean_past_medical_history(self):
        data = self.cleaned_data.get("past_medical_history", [])
        # 存回模型时用逗号连接
        return ",".join(data)

    def clean_other_medical_history(self):
        data = self.cleaned_data.get("other_medical_history", [])
        return ",".join(data)


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
