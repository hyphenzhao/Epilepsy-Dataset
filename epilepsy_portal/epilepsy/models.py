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
    GENDER_CHOICES = [
        ("M", "男"),
        ("F", "女"),
        ("O", "其他"),
    ]
    HAND_CHOICES = [
        ("L", "左利手"),
        ("R", "右利手"),
        ("A", "双手"),
    ]

    name = models.CharField("患者姓名", max_length=100)
    gender = models.CharField("性别", max_length=1, choices=GENDER_CHOICES)
    birthday = models.DateField("生日")
    handedness = models.CharField("左右利手", max_length=1, choices=HAND_CHOICES)
    department = models.CharField("科室", max_length=100, blank=True)
    bed_number = models.CharField("床号", max_length=20, blank=True)
    admission_date = models.DateField("入院时间")

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
