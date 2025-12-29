from django import forms
from .models import *
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

    # EEG 发作间期癫痫样放电：多选 + 勾选框
    eeg_interictal_state = forms.MultipleChoiceField(
        label="状态",
        required=False,
        choices=Patient.EEG_INTERICTAL_STATE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )

    eeg_interictal_location = forms.MultipleChoiceField(
        label="部位",
        required=False,
        choices=Patient.EEG_INTERICTAL_LOCATION_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )
    eeg_interictal_focal_lobe = forms.ChoiceField(
    label="局灶部位（叶）",
    required=False,
    choices=Patient.FOCAL_LOBE_CHOICES,
)
    eeg_interictal_laterality = forms.ChoiceField(
    label="偏侧方向",
    required=False,
    choices=Patient.EEG_INTERICTAL_LATERALITY_CHOICES,
)
    
    eeg_interictal_morph = forms.MultipleChoiceField(
        label="波幅、波形",
        required=False,
        choices=Patient.EEG_INTERICTAL_MORPH_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )

    eeg_interictal_amount = forms.MultipleChoiceField(
        label="数量",
        required=False,
        choices=Patient.EEG_INTERICTAL_AMOUNT_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )

    eeg_interictal_pattern = forms.MultipleChoiceField(
        label="出现方式",
        required=False,
        choices=Patient.EEG_INTERICTAL_PATTERN_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )

    eeg_interictal_eye_relation = forms.MultipleChoiceField(
        label="眼状态相关",
        required=False,
        choices=Patient.EEG_INTERICTAL_EYE_RELATED_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )
    eeg_ictal_state = forms.MultipleChoiceField(
        label="状态",
        required=False,
        choices=Patient.EEG_INTERICTAL_STATE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )
    eeg_ictal_location = forms.MultipleChoiceField(
        label="部位",
        required=False,
        choices=Patient.EEG_INTERICTAL_LOCATION_CHOICES,
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
            "medical_record_number",
            "admission_date",
            "education_level",
            "imaging_number",
            "admission_diagnosis",
            "occupation",

            # 病史
            # "pregnancy_birth_history",
            # "education_level",
            # "occupation",
            "past_medical_history",
            "past_medical_history_other_text",
            "other_medical_history",
            "family_history",
            "first_seizure_age",
            "first_seizure_description",
            "medication_history",

            # 发作症状学
            "seizure_state",
            "aura",
            "aura_text",
            # "typical_seizure_time",
            # "typical_seizure_semiology",
            "initial_seizure_symptom",
            "evolution_symptom",
            "postictal_state",
            "seizure_duration_seconds",
            # "seizure_duration_minutes",
            "seizure_freq_per_day",
            # "seizure_freq_per_week",
            # "seizure_freq_per_month",
            # "seizure_freq_per_year",

            # 神经系统检查
            "neuro_exam",
            "neuro_exam_description",

            # 认知和精神量表
            "assessment_done",
            "moca_score",
            "hama_score",
            "hamd_score",
            "bai_score",
            "bdi_score",
            "epilepsy_scale_score",

            # 视频头皮 EEG
            "eeg_recording_electrodes",
            "eeg_recording_duration_days",
            "eeg_bg_occipital_rhythm",
            "eeg_eye_response",
            "eeg_symmetry",
            "eeg_awake_background",
            "eeg_hv_result",
            "eeg_hv_slow_wave_build",
            "eeg_hv_slow_wave_frequency",
            "eeg_hv_slow_wave_symmetry",
            "eeg_hv_epileptiform_discharge",
            "eeg_hv_discharge_laterality",
            "ips_result", 
            "frequency",
            "laterality",
            "eeg_sleep_period_overall",
            "eeg_sleep_hypersynchrony_slow_wave",
            "eeg_sleep_vertex_wave",
            "eeg_sleep_spindle",
            "eeg_sleep_k_complex",
            "eeg_sleep_post",
            "eeg_sleep_frontal_awake_rhythm",
            "eeg_sleep_other",
            "eeg_interictal_state",
            "eeg_interictal_location",
            "eeg_interictal_focal_lobe",
            "eeg_interictal_laterality",
            "eeg_interictal_morph",
            "eeg_interictal_amount",
            "eeg_interictal_pattern",
            "eeg_interictal_eye_relation",
            "eeg_interictal",
            "eeg_ictal_state",
            "eeg_ictal_location",
            "eeg_ictal_amount",
            "eeg_ictal",
            "eeg_relevance",
            "eeg_clinical_correlation",
            "eeg_file_link",


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
            "seeg_file_link",

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
        widgets = {
            'first_seizure_description': forms.Textarea(attrs={
                        "rows": 5,   # ⬅ 控制高度
                        "cols": 5,  # 可选：控制宽度
                        "class": "form-control",
                    }),
            'medication_history': forms.Textarea(attrs={
                        "rows": 5,   # ⬅ 控制高度
                        "cols": 5,  # 可选：控制宽度
                        "class": "form-control",
                    }),
                    'initial_seizure_symptom': forms.Textarea(attrs={
                        "rows": 5,   # ⬅ 控制高度
                        "cols": 5,  # 可选：控制宽度
                        "class": "form-control",
                    }),
                    'evolution_symptom': forms.Textarea(attrs={
                        "rows": 5,   # ⬅ 控制高度
                        "cols": 5,  # 可选：控制宽度
                        "class": "form-control",
                    }),
                    'postictal_state': forms.Textarea(attrs={
                    "rows": 5,   # ⬅ 控制高度
                    "cols": 5,  # 可选：控制宽度
                    "class": "form-control",
                    }),
                    'neuro_exam_description': forms.Textarea(attrs={
                        "rows": 5,   # ⬅ 控制高度
                        "cols": 5,  # 可选：控制宽度
                        "class": "form-control",
                    }),
                    'eeg_ictal': forms.Textarea(attrs={
                        "rows": 5,   # ⬅ 控制高度
                        "cols": 5,  # 可选：控制宽度
                        "class": "form-control",
                    }),
                    'eeg_relevance': forms.Textarea(attrs={
                        "rows": 5,   # ⬅ 控制高度
                        "cols": 5,  # 可选：控制宽度
                        "class": "form-control",
                    }),
                    'eeg_clinical_correlation': forms.Textarea(attrs={
                        "rows": 5,   # ⬅ 控制高度
                        "cols": 5,  # 可选：控制宽度
                        "class": "form-control",
                    }),
                    'mri_brief': forms.Textarea(attrs={
                        "rows": 5,   # ⬅ 控制高度
                        "cols": 5,  # 可选：控制宽度
                        "class": "form-control",
                    }),
                    'pet_brief': forms.Textarea(attrs={
                        "rows": 5,   # ⬅ 控制高度
                        "cols": 5,  # 可选：控制宽度
                        "class": "form-control",
                    }),
                    'eeg_bg_occipital_rhythm': forms.TextInput(attrs={
                    'class': 'form-control',
                    'style': 'width:120px; display:inline-block;',
                    }),
                        'seeg_interictal_overall': forms.Textarea(attrs={
                        "rows": 5,   # ⬅ 控制高度
                        "cols": 5,  # 可选：控制宽度
                        "class": "form-control",
                    }),
                        'seeg_ictal': forms.Textarea(attrs={
                        "rows": 5,   # ⬅ 控制高度
                        "cols": 5,  # 可选：控制宽度
                        "class": "form-control",
                    }),
            }
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
            "first_seizure_description": "首次发作症状",
            "medication_history": "药物治疗",

            "aura": "先兆",
            "seizure_state":"自然发作状态",
            # "typical_seizure_time": "惯常发作时间",
            # "typical_seizure_semiology": "惯常发作表现形式",
            "initial_seizure_symptom":"首发症状",
            "evolution_symptom":"演变症状",
            "postictal_state":"发作后状态",
            "seizure_duration_seconds": "发作持续时间",
            # "seizure_duration_minutes": "发作持续时间（分钟）",
            "seizure_freq_per_day": "发作频率",
            # "seizure_freq_per_week": "发作频率（次/周）",
            # "seizure_freq_per_month": "发作频率（次/月）",
            # "seizure_freq_per_year": "发作频率（次/年）",

            "neuro_exam": "神经系统检查",
            "neuro_exam_description": "神经系统检查异常描述",

            "moca_score": "MoCA 评分",
            "hama_score": "HAMA 评分",
            "hamd_score": "HAMD 评分",
            "bai_score": "BAI 评分",
            "bdi_score": "BDI 评分",
            "epilepsy_scale_score": "癫痫量表评分",

            "eeg_recording_electrodes": "EEG 记录电极",
            "eeg_recording_duration_days": "记录时间（天）",
            "eeg_bg_occipital_rhythm":"枕区优势节律（闭目安静状态）(Hz)",
            "eeg_eye_response": "睁/闭眼反应", 
            "eeg_symmetry": "对称性",
            "eeg_awake_background": "清醒期背景",
            "eeg_hv_result":"HV 结果",
            "eeg_hv_slow_wave_build": "HV 慢波建立",
            "eeg_hv_slow_wave_frequency": "HV 慢波频率(Hz)",
            "eeg_hv_slow_wave_symmetry": "HV 慢波对称性",
            "eeg_hv_epileptiform_discharge": "HV 诱发癫痫样放电",
            "eeg_hv_discharge_laterality": "HV 放电对侧性",
            "ips_result":"IPS结果",
            "frequency":"闪光频率(Hz)",
            "laterality":"侧别",
            "eeg_sleep_period_overall": "睡眠周期",
            "eeg_sleep_hypersynchrony_slow_wave": "思睡期超同步化慢波",
            "eeg_sleep_vertex_wave": "顶尖波",
            "eeg_sleep_spindle": "睡眠纺锤波",
            "eeg_sleep_k_complex": "K-综合波",
            "eeg_sleep_post": "POST",
            "eeg_sleep_frontal_awake_rhythm": "额区觉醒节律",
            "eeg_sleep_other": "睡眠周期 其他",
            "eeg_interictal_state": "状态",
            "eeg_interictal_location": "部位",
            "eeg_interictal_focal_lobe": "局灶部位（叶）", 
            "eeg_interictal_laterality":"偏侧方向",
            "eeg_interictal_morph": "波幅、波形",
            "eeg_interictal_amount": "数量",
            "eeg_interictal_pattern": "出现方式",
            "eeg_interictal_eye_relation": "眼状态相关",
            "eeg_ictal_state":"发作期状态（多选）",
            "eeg_ictal_location":"发作期部位（多选）",
            "eeg_ictal_amount":"数量",
            "eeg_interictal": "EEG 发作间期放电",
            "eeg_ictal": "EEG 发作期",
            "eeg_relevance": "EEG 相关性",
            "eeg_clinical_correlation": "EEG 同步发作临床症状",
            "eeg_file_link": "EEG 数据下载链接",

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
            "seeg_file_link": "SEEG 数据下载链接",

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
    
    def clean_eeg_interictal_state(self):
         data = self.cleaned_data.get("eeg_interictal_state", [])
         return ",".join(data)

    def clean_eeg_interictal_location(self):
        data = self.cleaned_data.get("eeg_interictal_location", [])
        return ",".join(data)

    def clean_eeg_interictal_morph(self):
        data = self.cleaned_data.get("eeg_interictal_morph", [])
        return ",".join(data)

    def clean_eeg_interictal_amount(self):
        data = self.cleaned_data.get("eeg_interictal_amount", [])
        return ",".join(data)

    def clean_eeg_interictal_pattern(self):
        data = self.cleaned_data.get("eeg_interictal_pattern", [])
        return ",".join(data)

    def clean_eeg_interictal_eye_relation(self):
        data = self.cleaned_data.get("eeg_interictal_eye_relation", [])
        return ",".join(data)
    
    def clean_eeg_ictal_state(self):
        data = self.cleaned_data.get("eeg_ictal_state", [])
        return ",".join(data)

    def clean_eeg_ictal_location(self):
        data = self.cleaned_data.get("eeg_ictal_location", [])
        return ",".join(data)



    def clean(self):
        cleaned = super().clean()

        locations = cleaned.get("eeg_interictal_location") or []
        focal_lobe = cleaned.get("eeg_interictal_focal_lobe")

        # 勾选了“局灶” → 必须选叶
        if "FOCAL" in locations and not focal_lobe:
            self.add_error(
                "eeg_interictal_focal_lobe",
                "选择“局灶”时必须指定额叶 / 顶叶 / 枕叶 / 颞叶"
            )

        # 没选“局灶” → 清空叶字段，避免脏数据
        if "FOCAL" not in locations:
            cleaned["eeg_interictal_focal_lobe"] = ""

        return cleaned

class UserWithRoleForm(forms.ModelForm):
    role = forms.ChoiceField(
        label="角色",
        choices=UserRole.choices,
    )

    # 新增两个密码字段
    password1 = forms.CharField(
        label="密码",
        required=False,
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )
    password2 = forms.CharField(
        label="确认密码",
        required=False,
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )

    class Meta:
        model = User
        fields = ["username", "email", "is_active"]
        labels = {
            "username": "用户名",
            "email": "邮箱",
            "is_active": "启用",
        }
        widgets = {
            "username": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # 编辑用户时，把当前 profile.role 填入表单初始值
        if self.instance and self.instance.pk:
            profile = getattr(self.instance, "profile", None)
            if profile:
                self.initial.setdefault("role", profile.role)
        else:
            # 新建用户默认角色
            self.initial.setdefault("role", UserRole.GUEST)
            # 新建用户必须填密码
            self.fields["password1"].required = True
            self.fields["password2"].required = True

    def clean(self):
        cleaned_data = super().clean()
        p1 = cleaned_data.get("password1")
        p2 = cleaned_data.get("password2")

        is_create = not (self.instance and self.instance.pk)

        # 新建必须有密码
        if is_create:
            if not p1 or not p2:
                raise forms.ValidationError("新建用户必须设置密码。")

        # 只要有任意一项填写，就按“修改密码”处理
        if p1 or p2:
            if p1 != p2:
                raise forms.ValidationError("两次输入的密码不一致。")
            if len(p1) < 8:
                raise forms.ValidationError("密码长度至少为 8 位。")

        return cleaned_data

    def save(self, commit=True):
        # 不直接提交，先处理密码
        user = super().save(commit=False)

        password = self.cleaned_data.get("password1")
        if password:
            # 使用 Django 内置加密
            user.set_password(password)

        if commit:
            user.save()

        # 处理角色到 UserProfile
        role = self.cleaned_data["role"]
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.role = role
        profile.save()
        return user

class MRIFileForm(forms.ModelForm):
    class Meta:
        model = MRIFile
        # save_name 自动生成，不用在表单里填
        fields = [
            "patient",
            "parent_path",
            "file_name",
            "hash_code",
            "sha256_code",
        ]
        labels = {
            "patient": "患者",
            "parent_path": "父路径",
            "file_name": "原始文件名",
            "hash_code": "哈希码",
            "sha256_code": "SHA256 校验码",
        }


class PETFileForm(forms.ModelForm):
    class Meta:
        model = PETFile
        fields = [
            "patient",
            "parent_path",
            "file_name",
            "hash_code",
            "sha256_code",
        ]
        labels = {
            "patient": "患者",
            "parent_path": "父路径",
            "file_name": "原始文件名",
            "hash_code": "哈希码",
            "sha256_code": "SHA256 校验码",
        }


class EEGFileForm(forms.ModelForm):
    class Meta:
        model = EEGFile
        fields = [
            "patient",
            "parent_path",
            "file_name",
            "hash_code",
            "sha256_code",
        ]
        labels = {
            "patient": "患者",
            "parent_path": "父路径",
            "file_name": "原始文件名",
            "hash_code": "哈希码",
            "sha256_code": "SHA256 校验码",
        }


class SEEGFileForm(forms.ModelForm):
    class Meta:
        model = SEEGFile
        fields = [
            "patient",
            "parent_path",
            "file_name",
            "hash_code",
            "sha256_code",
        ]
        labels = {
            "patient": "患者",
            "parent_path": "父路径",
            "file_name": "原始文件名",
            "hash_code": "哈希码",
            "sha256_code": "SHA256 校验码",
        }
