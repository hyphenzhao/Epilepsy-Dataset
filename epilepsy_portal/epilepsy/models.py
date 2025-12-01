from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class UserRole(models.TextChoices):
    ADMIN = "ADMIN", _("管理员")
    STAFF = "STAFF", _("工作人员")
    GUEST = "GUEST", _("访客")


class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    role = models.CharField(
        max_length=10,
        choices=UserRole.choices,
        default=UserRole.GUEST,
        verbose_name=_("角色"),
    )

    def __str__(self):
        return f"{self.user} ({self.get_role_display()})"

    @property
    def is_admin(self):
        return self.role == UserRole.ADMIN

    @property
    def is_staff_member(self):
        return self.role == UserRole.STAFF

    @property
    def is_guest(self):
        return self.role == UserRole.GUEST

class Patient(models.Model):
    GENDER_CHOICES = [("M", "男"), ("F", "女"), ("O", "其他"),]
    HAND_CHOICES = [("L", "左利手"), ("R", "右利手"), ("A", "双手"),]

    # 受教育程度
    EDUCATION_CHOICES = [("PRIMARY", "小学及以下"),("MIDDLE", "初中"),
        ("HIGH", "高中/中专"),("COLLEGE", "大专"),("UNIVERSITY", "本科"),
        ("POSTGRAD", "研究生及以上"),]

    # 既往不良病史（多选）
    PAST_MEDICAL_HISTORY_CHOICES = [("HYPOXIA", "出生乏氧"),
        ("FEBRILE_SEIZURE", "热惊厥"), ("ENCEPHALITIS", "脑炎"),
        ("TRAUMA", "外伤"),("NONE", "无"),("OTHER", "其他"),]

    # 其他病史（多选）
    OTHER_MEDICAL_HISTORY_CHOICES = [ ("DM", "糖尿病"),("HTN", "高血压"),]

    # 先兆 有 / 无
    AURA_CHOICES = [("Y", "有"),("N", "无"),]

    # 神经系统检查 正常 / 异常
    NEURO_EXAM_CHOICES = [("N", "正常"),("A", "异常"),]

    # 基本信息
    name = models.CharField("患者姓名", max_length=20)
    gender = models.CharField("性别", max_length=1, choices=GENDER_CHOICES)
    birthday = models.DateField("生日")
    handedness = models.CharField("左右利手", max_length=1, choices=HAND_CHOICES)
    department = models.CharField("科室", max_length=20, blank=True)
    bed_number = models.CharField("床号", max_length=20, blank=True)
    medical_record_number = models.CharField("病历号", max_length=20, blank=True)
    admission_date = models.DateField("入院时间")
    education_level = models.CharField(
        "受教育程度",
        max_length=20,
        choices=EDUCATION_CHOICES,
        blank=True,
    )
    occupation = models.CharField("职业", max_length=20, blank=True)
    imaging_number = models.CharField("影像号", max_length=20, blank=True)
    admission_diagnosis = models.CharField("入院诊断", max_length=20, blank=True)

    # 【病史】
    # pregnancy_birth_history = models.TextField("母孕出生史", blank=True)
    # education_level = models.CharField(
    #     "受教育程度",
    #     max_length=20,
    #     choices=EDUCATION_CHOICES,
    #     blank=True,
    # )
    #occupation = models.CharField("职业", max_length=100, blank=True)

    # 既往不良病史（用逗号分隔的编码存储）
    past_medical_history = models.CharField(
        "既往不良病史（多选）",
        max_length=255,
        blank=True,
        help_text="多选，用逗号分隔编码存储",
    )

    # 其他病史（用逗号分隔的编码存储）
    other_medical_history = models.CharField(
        "其他病史（多选）",
        max_length=255,
        blank=True,
        help_text="多选，用逗号分隔编码存储",
    )

    family_history = models.TextField("家族病史", blank=True)

    first_seizure_age = models.PositiveIntegerField(
        "首次发作年龄（岁）", blank=True, null=True
    )
    first_seizure_description = models.TextField(
        "首次发作简要表现形式", blank=True
    )

    medication_history = models.TextField("药物治疗过程", blank=True)

    # 【发作症状学】
    aura = models.CharField(
        "先兆", max_length=1, choices=AURA_CHOICES, blank=True
    )
    typical_seizure_time = models.CharField(
        "惯常发作时间", max_length=100, blank=True
    )
    typical_seizure_semiology = models.TextField(
        "惯常发作表现形式", blank=True
    )

    seizure_duration_seconds = models.PositiveIntegerField(
        "发作持续时间（秒）", blank=True, null=True
    )
    seizure_duration_minutes = models.PositiveIntegerField(
        "发作持续时间（分钟）", blank=True, null=True
    )

    seizure_freq_per_day = models.PositiveIntegerField(
        "发作频率（次/天）", blank=True, null=True
    )
    seizure_freq_per_week = models.PositiveIntegerField(
        "发作频率（次/周）", blank=True, null=True
    )
    seizure_freq_per_month = models.PositiveIntegerField(
        "发作频率（次/月）", blank=True, null=True
    )
    seizure_freq_per_year = models.PositiveIntegerField(
        "发作频率（次/年）", blank=True, null=True
    )

    # 【神经系统检查】
    neuro_exam = models.CharField(
        "神经系统检查",
        max_length=1,
        choices=NEURO_EXAM_CHOICES,
        blank=True,
    )
    neuro_exam_description = models.TextField(
        "神经系统检查异常描述", blank=True
    )

    # 【认知和精神量表】
    moca_score = models.PositiveIntegerField("MoCA 评分", blank=True, null=True)
    hama_score = models.PositiveIntegerField("HAMA 评分", blank=True, null=True)
    hamd_score = models.PositiveIntegerField("HAMD 评分", blank=True, null=True)
    bai_score = models.PositiveIntegerField("BAI 评分", blank=True, null=True)
    bdi_score = models.PositiveIntegerField("BDI 评分", blank=True, null=True)
    epilepsy_scale_score = models.PositiveIntegerField(
        "癫痫量表评分", blank=True, null=True
    )

    # 【视频头皮 EEG 检查】
    eeg_interictal = models.TextField("EEG 发作间期放电", blank=True)
    eeg_ictal = models.TextField("EEG 发作期放电", blank=True)
    eeg_clinical_correlation = models.TextField(
        "EEG 同步发作临床症状", blank=True
    )

    # 【影像学检查结果】
    mri_brief = models.TextField("MRI 简要描述", blank=True)
    mri_link = models.URLField(
        "MRI 图像下载链接", max_length=500, blank=True
    )
    pet_brief = models.TextField("PET 简要描述", blank=True)
    pet_link = models.URLField(
        "PET 图像下载链接", max_length=500, blank=True
    )

    # 【一期无创性评估结果】
    first_stage_lateralization = models.CharField(
        "一期无创性评估定侧", max_length=100, blank=True
    )
    first_stage_region = models.CharField(
        "一期无创性评估定区域", max_length=100, blank=True
    )
    first_stage_location = models.CharField(
        "一期无创性评估定具体部位", max_length=200, blank=True
    )

    # 【SEEG 发作间期放电及发作期放电】
    seeg_interictal_overall = models.TextField(
        "SEEG 发作间期放电总体描述", blank=True
    )
    seeg_group1 = models.TextField("SEEG 发作间期 Group 1", blank=True)
    seeg_group2 = models.TextField("SEEG 发作间期 Group 2", blank=True)
    seeg_group3 = models.TextField("SEEG 发作间期 Group 3", blank=True)
    seeg_ictal = models.TextField("SEEG 发作期放电", blank=True)

    # 【二期有创性评估结果】
    second_stage_core_zone = models.TextField(
        "二期有创评估核心区域", blank=True
    )
    second_stage_hypothesis_zone = models.TextField(
        "二期有创评估假设区域", blank=True
    )

    # 【外科切除计划】
    resection_plan_convex = models.TextField(
        "外科切除计划 - 凸面", blank=True
    )
    resection_plan_concave = models.TextField(
        "外科切除计划 - 凹面", blank=True
    )

    # 评估人 + 时间
    evaluator = models.CharField("评估人", max_length=100, blank=True)
    evaluation_date = models.DateField(
        "评估日期", blank=True, null=True
    )

    # 系统字段
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "患者"
        verbose_name_plural = "患者"

    def __str__(self):
        return f"{self.name} ({self.bed_number})"


class PatientDataset(models.Model):
    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="datasets",
        verbose_name="患者"
    )
    name = models.CharField("数据名称", max_length=200)
    description = models.TextField("说明", blank=True)

    # 面向 Globus / DGPF 的字段：
    globus_endpoint_id = models.CharField(
        "Globus Endpoint ID",
        max_length=64,
        help_text="存放该患者数据的 Globus Endpoint"
    )
    globus_path = models.CharField(
        "数据路径",
        max_length=1024,
        help_text="Endpoint 上的路径，例如 /data/epilepsy/p001/"
    )

    # 可选：与 Globus Search 索引的记录做关联（以后用来搜索）
    search_subject = models.CharField(
        "Globus Search subject",
        max_length=256,
        blank=True
    )

    is_active = models.BooleanField("启用", default=True)

    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "患者数据"
        verbose_name_plural = "患者数据"

    def __str__(self):
        return f"{self.patient.name} - {self.name}"
