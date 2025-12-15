from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
import os

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

    # 自然发作状态 清醒 / 睡眠 / 清醒或睡眠
    SEIZURE_STATE_CHOICES = [("AWAKE", "清醒"),("SLEEP", "睡眠"),("BOTH", "清醒和睡眠")]

    # 先兆 有 / 无
    AURA_CHOICES = [("Y", "有"),("N", "无"),]

    # EEG 记录电极 10-20 系统 / 10-10 系统
    EEG_RECORDING_ELECTRODES_CHOICES = [("10-20 system", "10-20 系统"),("10-10 system", "10-10 系统"),]

    # 对称性
    EEG_SYMMETRY_CHOICES = [("bilateral", "双侧"),("left_attenuated", "左侧衰减"),("right_attenuated", "右侧衰减"),]

    # 清醒期背景
    EEG_AWAKE_BACKGROUND_CHOICES = [("normal", "正常"), ("diffuse_slow", "弥漫性慢波"), ("unilateral_slow", "偏侧性慢波"), ("focal_slow", "局灶性慢波")]
    
    # 结果选项
    RESULT_CHOICES = [("normal", "未见异常"), ("not_done", "未作"), ("changed", "相关改变")]

    # 慢波建立
    EEG_HV_SLOW_WAVE_BUILD_CHOICES = [("none", "无"), ("frontal", "前头部"), ("occipital", "后头部"), ("diffuse", "广泛性"), ("mild", "少量不明显")]
    
    # 慢波对称性
    EEG_HV_SLOW_WAVE_SYMMETRY_CHOICES = [("bilateral", "双侧"), ("left_stronger", "左侧较强"), ("right_stronger", "右侧较强")]
    
    # 放电对侧性
    EEG_HV_DISCHARGE_LATERALITY_CHOICES = [("bilateral", "双侧"), ("left", "左侧"), ("right", "右侧")]

    # 睡眠周期
    SLEEP_PERIOD_CHOICES = [("mostly_normal", "大致正常"), ("uncertain", "不明确"), ("absent", "消失")]
   
    # 睡眠波
    SLEEP_WAVE_CHANGE_CHOICES = [("normal", "正常"), ("left_reduced", "左侧衰减"), ("right_reduced", "右侧衰减")]

    # 状态
    EEG_INTERICTAL_STATE_CHOICES = [ ("AWAKE", "清醒期"), ("DROWSY", "困倦期"),("SLEEP", "睡眠期"),("POST_AWAKE", "觉醒后"),("ALL", "醒-睡各期"),]

    # 部位
    EEG_INTERICTAL_LOCATION_CHOICES = [ ("FOCAL", "局灶"), ("LAT", "偏侧"),("MULTI", "多灶"),]

    # 波幅/波形
    EEG_INTERICTAL_MORPH_CHOICES = [("SHARP", "尖波"),("SPIKE", "棘波"),("POLY_SPIKE", "多棘波"),("SHARP_SLOW", "棘慢复合波"),]

    # 数量
    EEG_INTERICTAL_AMOUNT_CHOICES = [("RARE", "稀少"), ("OCCASIONAL", "偶见"),("FREQUENT", "频繁"),("HIGH_DENSITY", "高密度"),("CONTINUOUS", "连续"),]

    # 出现方式
    EEG_INTERICTAL_PATTERN_CHOICES = [("SCATTERED", "散发"),("PAROXYSMAL", "阵发"),("RHYTHMIC_PAROXYSMAL", "节律性阵发"),("CONTINUOUS", "连续发放"),("BURST", "爆发"),("INTERMITTENT", "间断性发放"),
    ("PERIODIC", "周期性发放"),("MIGRATORY", "游走性发放"),("NEAR_CONTINUOUS", "接近持续发放"),]

    # 眼状态相关
    EEG_INTERICTAL_EYE_RELATED_CHOICES = [("NONE", "无"),("FOCAL_SENSITIVE", "有：失对焦敏感（闭眼增多）"),("PHOTOSENSITIVE", "合眼敏感"), ("BLINK_RELATED", "瞬目相关"),]

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
    education_level = models.CharField("受教育程度",max_length=20,choices=EDUCATION_CHOICES,blank=True,)
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

    family_history = models.CharField("家族病史", max_length=20, blank=True)

    first_seizure_age = models.PositiveIntegerField(
        "首次发作年龄（岁）", blank=True, null=True
    )
    first_seizure_description = models.TextField(
        "首次发作症状", blank=True
    )

    medication_history = models.TextField("药物治疗过程", blank=True)

    # 【发作症状学】
    seizure_state = models.CharField(
    "自然发作状态",max_length=10,choices=SEIZURE_STATE_CHOICES,blank=True,)
    aura = models.CharField(
        "先兆", max_length=1, choices=AURA_CHOICES, blank=True)
    # typical_seizure_time = models.CharField(
    #     "惯常发作时间", max_length=100, blank=True
    # )
    # typical_seizure_semiology = models.TextField(
    #     "惯常发作表现形式", blank=True
    # )
    initial_seizure_symptom = models.TextField( "首发症状",max_length=150, blank=True, )
    evolution_symptom = models.TextField("演变症状",max_length=150, blank=True,)
    seizure_duration_seconds = models.CharField("发作持续时间", blank=True, null=True )
    # seizure_duration_minutes = models.PositiveIntegerField(
    #     "发作持续时间（分钟）", blank=True, null=True
    # )

    seizure_freq_per_day = models.CharField(
        "发作频率", blank=True, null=True)
    # seizure_freq_per_week = models.PositiveIntegerField(
    #     "发作频率（次/周）", blank=True, null=True
    # )
    # seizure_freq_per_month = models.PositiveIntegerField(
    #     "发作频率（次/月）", blank=True, null=True
    # )
    # seizure_freq_per_year = models.PositiveIntegerField(
    #     "发作频率（次/年）", blank=True, null=True
    # )

    # 【神经系统检查】
    neuro_exam = models.CharField(
        "神经系统检查",
        max_length=1,
        choices=NEURO_EXAM_CHOICES,
        blank=True,)
    neuro_exam_description = models.TextField(
        "神经系统检查异常描述", blank=True)

    # 【认知和精神量表】
    moca_score = models.PositiveIntegerField("MoCA 评分", blank=True, null=True)
    hama_score = models.PositiveIntegerField("HAMA 评分", blank=True, null=True)
    hamd_score = models.PositiveIntegerField("HAMD 评分", blank=True, null=True)
    bai_score = models.PositiveIntegerField("BAI 评分", blank=True, null=True)
    bdi_score = models.PositiveIntegerField("BDI 评分", blank=True, null=True)
    epilepsy_scale_score = models.PositiveIntegerField(
        "癫痫量表评分", blank=True, null=True )

    # 【视频头皮 EEG 检查】
    eeg_recording_electrodes = models.CharField( "EEG 记录电极", max_length=20, choices=EEG_RECORDING_ELECTRODES_CHOICES, blank=True)
    eeg_recording_duration_days = models.PositiveIntegerField("记录时间 (天)", blank=True, null=True)
    eeg_bg_occipital_rhythm = models.CharField("枕区优势节律（闭目安静状态）",max_length=100, blank=True,null=True,)
    eeg_eye_response = models.CharField(
    "睁/闭眼反应", max_length=100, choices=AURA_CHOICES, blank=True)
    eeg_symmetry = models.CharField(
    "对称性", max_length=100, choices=EEG_SYMMETRY_CHOICES, blank=True)
    eeg_awake_background = models.CharField(
    "清醒期背景", max_length=100, choices=EEG_AWAKE_BACKGROUND_CHOICES, blank=True)
    eeg_hv_result = models.CharField("HV 结果", max_length=20, choices=RESULT_CHOICES, blank=True, null=True)
    eeg_hv_slow_wave_build = models.CharField("慢波建立", max_length=100, choices=EEG_HV_SLOW_WAVE_BUILD_CHOICES, blank=True, null=True)
    eeg_hv_slow_wave_frequency = models.CharField("慢波频率(Hz)", max_length=50, blank=True, null=True)
    eeg_hv_slow_wave_symmetry = models.CharField("慢波对称性", max_length=100, choices=EEG_HV_SLOW_WAVE_SYMMETRY_CHOICES, blank=True, null=True)
    eeg_hv_epileptiform_discharge = models.CharField("诱发癫痫样放电", max_length=50, choices=AURA_CHOICES, blank=True, null=True)
    eeg_hv_discharge_laterality = models.CharField("放电对侧性", max_length=100, choices=EEG_HV_DISCHARGE_LATERALITY_CHOICES, blank=True, null=True)
    ips_result = models.CharField("IPS 结果", max_length=20, choices=RESULT_CHOICES, blank=True, null=True)
    frequency = models.CharField("闪光频率(Hz)", max_length=50, blank=True, null=True)
    laterality = models.CharField("侧别", max_length=20, choices=EEG_HV_DISCHARGE_LATERALITY_CHOICES, blank=True, null=True)
    eeg_sleep_period_overall = models.CharField("睡眠周期", max_length=20, choices=SLEEP_PERIOD_CHOICES, blank=True, null=True)
    eeg_sleep_hypersynchrony_slow_wave = models.CharField("思睡期超同步化慢波", max_length=10, choices=AURA_CHOICES, blank=True, null=True)
    eeg_sleep_vertex_wave = models.CharField("顶尖波", max_length=20, choices=SLEEP_WAVE_CHANGE_CHOICES, blank=True, null=True)
    eeg_sleep_spindle = models.CharField("睡眠纺锤波", max_length=20, choices=SLEEP_WAVE_CHANGE_CHOICES, blank=True, null=True)
    eeg_sleep_k_complex = models.CharField("K-综合波", max_length=20, choices=SLEEP_WAVE_CHANGE_CHOICES, blank=True, null=True)
    eeg_sleep_post = models.CharField("POST", max_length=10, choices=AURA_CHOICES, blank=True, null=True)
    eeg_sleep_frontal_awake_rhythm = models.CharField("额区觉醒节律", max_length=10, choices=AURA_CHOICES, blank=True, null=True)
    eeg_sleep_other = models.CharField("睡眠周期 其他", max_length=200, blank=True, null=True)
    eeg_interictal_state = models.CharField(
    "状态（多选）",
    max_length=255,
    blank=True,
    help_text="多选，用逗号分隔编码存储",
)
    eeg_interictal_location = models.CharField(
    "部位（多选）",
    max_length=255,
    blank=True,
    help_text="多选，用逗号分隔编码存储",
)
    eeg_interictal_morph = models.CharField(
    "波幅/波形（多选）",
    max_length=255,
    blank=True,
    help_text="多选，用逗号分隔编码存储",
)
    eeg_interictal_amount = models.CharField(
    "数量（多选）",
    max_length=255,
    blank=True,
    help_text="多选，用逗号分隔编码存储",
)
    eeg_interictal_pattern = models.CharField(
    "出现方式（多选）",
    max_length=255,
    blank=True,
    help_text="多选，用逗号分隔编码存储",
)
    eeg_interictal_eye_relation = models.CharField(
    "眼状态相关（多选）",
    max_length=255,
    blank=True,
    help_text="多选，用逗号分隔编码存储",
)

    eeg_interictal = models.TextField("EEG 发作间期放电", blank=True)
    eeg_ictal = models.TextField("EEG 发作期放电", blank=True)
    eeg_clinical_correlation = models.TextField(
        "EEG 同步发作临床症状", blank=True
    )
    # EEG 文件链接（可选）
    eeg_file_link = models.URLField(
        "EEG 原始数据下载链接",
        max_length=500,
        blank=True
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
    # SEEG 文件链接（可选）
    seeg_file_link = models.URLField(
        "SEEG 原始数据下载链接",
        max_length=500,
        blank=True
    )
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

class BasePatientFile(models.Model):
    """
    患者相关文件的抽象基类：
    - parent_path: 存储在服务器/存储系统中的父路径
    - file_name: 原始文件名（带扩展名）
    - hash_code: 自定义哈希（例如 MD5 或你自己的规则）
    - sha256_code: 文件内容的 SHA256 校验码
    - save_name: 实际保存的文件名 = hash_code + 原文件扩展名
    """
    parent_path = models.CharField("父路径", max_length=1024, blank=True)
    file_name = models.CharField("原始文件名", max_length=255)
    hash_code = models.CharField("哈希码", max_length=64)
    sha256_code = models.CharField("SHA256 校验码", max_length=64)
    save_name = models.CharField("保存文件名", max_length=300, editable=False)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        abstract = True  # 不单独建表，只做基类

    def save(self, *args, **kwargs):
        # 自动根据 file_name 的扩展名生成 save_name = hash_code + ext
        if self.file_name and self.hash_code:
            _, ext = os.path.splitext(self.file_name)
            ext = ext.lower()
            self.save_name = f"{self.hash_code}{ext}"
        super().save(*args, **kwargs)

class MRIFile(BasePatientFile):
    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="mri_files",
        verbose_name="患者"
    )

    class Meta:
        verbose_name = "MRI 文件"
        verbose_name_plural = "MRI 文件"

    def __str__(self):
        return f"MRI: {self.patient.name} - {self.file_name}"


class PETFile(BasePatientFile):
    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="pet_files",
        verbose_name="患者"
    )

    class Meta:
        verbose_name = "PET 文件"
        verbose_name_plural = "PET 文件"

    def __str__(self):
        return f"PET: {self.patient.name} - {self.file_name}"


class EEGFile(BasePatientFile):
    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="eeg_files",
        verbose_name="患者"
    )

    class Meta:
        verbose_name = "EEG 文件"
        verbose_name_plural = "EEG 文件"

    def __str__(self):
        return f"EEG: {self.patient.name} - {self.file_name}"


class SEEGFile(BasePatientFile):
    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="seeg_files",
        verbose_name="患者"
    )

    class Meta:
        verbose_name = "SEEG 文件"
        verbose_name_plural = "SEEG 文件"

    def __str__(self):
        return f"SEEG: {self.patient.name} - {self.file_name}"

class PatientInfoFile(BasePatientFile):
    """
    患者信息导出文件：
    - file_name：显示名，如 “张三_123456”
    - save_name：hashcode + .csv/.docx/.pdf
    - format：导出格式
    """
    class Format(models.TextChoices):
        CSV = "CSV", "CSV"
        WORD = "WORD", "Word"
        PDF = "PDF", "PDF"

    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="info_files",
        verbose_name="患者",
    )
    format = models.CharField(
        "导出格式",
        max_length=10,
        choices=Format.choices,
    )

    class Meta:
        verbose_name = "患者信息导出文件"
        verbose_name_plural = "患者信息导出文件"

    def __str__(self):
        return f"患者信息导出: {self.patient.name} - {self.file_name} ({self.format})"

    def save(self, *args, **kwargs):
        # 根据 format 决定扩展名，强制 save_name = hash_code + ext
        ext_map = {
            self.Format.CSV: ".csv",
            self.Format.WORD: ".docx",
            self.Format.PDF: ".pdf",
        }
        if self.hash_code:
            ext = ext_map.get(self.format, "")
            self.save_name = f"{self.hash_code}{ext}"
        # 跳过 BasePatientFile.save 的扩展名逻辑，直接走 Model.save
        super(BasePatientFile, self).save(*args, **kwargs)
