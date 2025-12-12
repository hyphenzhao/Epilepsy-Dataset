# epilepsy/views.py

import os, csv, datetime, io, zipfile
# from 
from django.conf import settings
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model, logout
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, JsonResponse, HttpResponseForbidden, FileResponse, Http404
from django.views.generic import ListView, CreateView, UpdateView, DetailView, TemplateView
from django.urls import reverse_lazy
from django.db.models import Q
from django.contrib.staticfiles import finders
from django.contrib import messages
from django.core.exceptions import FieldDoesNotExist, FieldError
from django.utils import timezone
import markdown
from .mixins import RoleRequiredMixin
from .models import (
    Patient, UserRole,
    PatientDataset,
    MRIFile, PETFile, EEGFile, SEEGFile,
)
from .forms import PatientForm, UserWithRoleForm

# 新增：导入 helper
from .views_helper import (
    build_dashboard_context,
    require_admin,
    handle_patient_file_uploads,
    build_patient_file_path,
    generate_patient_info_file,
)
from .json import PATIENT_GROUP_FIELDS, FIELDS_FOR_EXPORT

User = get_user_model()


@login_required
def dashboard(request):
    """
    仪表盘视图：只负责请求 + 渲染，业务逻辑放在 helper。
    """
    context = build_dashboard_context()
    return render(request, "epilepsy/dashboard.html", context)


class PatientListView(RoleRequiredMixin, ListView):
    model = Patient
    template_name = "epilepsy/patient_list.html"
    context_object_name = "patients"
    paginate_by = 20
    allowed_roles = [UserRole.ADMIN, UserRole.STAFF, UserRole.GUEST]

    def get_queryset(self):
        qs = super().get_queryset().order_by("id")
        request = self.request

        # 基础关键字搜索
        q = request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(name__icontains=q)
                | Q(bed_number__icontains=q)
                | Q(department__icontains=q)
            )

        # ---------- 高级搜索条件 ----------

        # 入院时间范围：admission_date
        qs = self._apply_date_range_filter(
            qs,
            "admission_date",
            request.GET.get("admission_start"),
            request.GET.get("admission_end"),
        )

        # 评估时间范围：假定字段名为 evaluation_date（如不同，请改 field_name）
        qs = self._apply_date_range_filter(
            qs,
            "evaluation_date",
            request.GET.get("evaluation_start"),
            request.GET.get("evaluation_end"),
        )

        # 年龄范围：用“当前年份 - 生日年份”近似计算
        age_min = request.GET.get("age_min")
        age_max = request.GET.get("age_max")
        if (age_min or age_max) and self._field_exists("birthday"):
            today = timezone.now().date()
            from datetime import date

            dob_min = dob_max = None
            # 年龄 <= age_max  -> 出生年份 >= now.year - age_max
            try:
                if age_max:
                    a_max = int(age_max)
                    dob_min = date(today.year - a_max, 1, 1)
            except ValueError:
                pass

            # 年龄 >= age_min  -> 出生年份 <= now.year - age_min
            try:
                if age_min:
                    a_min = int(age_min)
                    dob_max = date(today.year - a_min, 12, 31)
            except ValueError:
                pass

            lookup = {}
            if dob_min:
                lookup["birthday__gte"] = dob_min
            if dob_max:
                lookup["birthday__lte"] = dob_max
            if lookup:
                qs = qs.filter(**lookup)

        # 自然发作状态：假定布尔字段 natural_seizure_state（1=有, 0=无）
        natural_state = request.GET.get("natural_state")
        if natural_state in ("1", "0") and self._field_exists("natural_seizure_state"):
            value = (natural_state == "1")
            try:
                qs = qs.filter(natural_seizure_state=value)
            except FieldError:
                pass

        # 先兆：假定布尔字段 aura（1=有, 0=无）
        aura = request.GET.get("aura")
        if aura in ("1", "0") and self._field_exists("aura"):
            value = (aura == "1")
            try:
                qs = qs.filter(aura=value)
            except FieldError:
                pass

        # MoCA / HAMA / HAMD / BAI / BDI / 癫痫量表评分 区间
        # 假定字段名为 *_score；如模型中命名不同，请改 field_name
        qs = self._apply_numeric_range_filter(
            qs,
            "moca_score",
            request.GET.get("moca_min"),
            request.GET.get("moca_max"),
        )
        qs = self._apply_numeric_range_filter(
            qs,
            "hama_score",
            request.GET.get("hama_min"),
            request.GET.get("hama_max"),
        )
        qs = self._apply_numeric_range_filter(
            qs,
            "hamd_score",
            request.GET.get("hamd_min"),
            request.GET.get("hamd_max"),
        )
        qs = self._apply_numeric_range_filter(
            qs,
            "bai_score",
            request.GET.get("bai_min"),
            request.GET.get("bai_max"),
        )
        qs = self._apply_numeric_range_filter(
            qs,
            "bdi_score",
            request.GET.get("bdi_min"),
            request.GET.get("bdi_max"),
        )
        qs = self._apply_numeric_range_filter(
            qs,
            "epilepsy_scale_score",
            request.GET.get("epilepsy_scale_min"),
            request.GET.get("epilepsy_scale_max"),
        )

        # 是否有 MRI / PET / EEG / sEEG 文件
        for param, rel_name in (
            ("has_mri", "mri_files"),
            ("has_pet", "pet_files"),
            ("has_eeg", "eeg_files"),
            ("has_seeg", "seeg_files"),
        ):
            val = request.GET.get(param)
            if val not in ("1", "0"):
                continue
            try:
                if val == "1":
                    qs = qs.filter(**{f"{rel_name}__isnull": False})
                else:  # val == "0"
                    qs = qs.filter(**{f"{rel_name}__isnull": True})
            except FieldError:
                # 关系名不正确时直接忽略该条件
                pass

        # 防止关联过滤导致重复
        return qs.distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        request = self.request

        context["q"] = request.GET.get("q", "").strip()

        # 高级搜索字段回显
        context.update(
            {
                "admission_start": request.GET.get("admission_start", ""),
                "admission_end": request.GET.get("admission_end", ""),
                "evaluation_start": request.GET.get("evaluation_start", ""),
                "evaluation_end": request.GET.get("evaluation_end", ""),
                "age_min": request.GET.get("age_min", ""),
                "age_max": request.GET.get("age_max", ""),
                "natural_state": request.GET.get("natural_state", ""),
                "aura": request.GET.get("aura", ""),
                "moca_min": request.GET.get("moca_min", ""),
                "moca_max": request.GET.get("moca_max", ""),
                "hama_min": request.GET.get("hama_min", ""),
                "hama_max": request.GET.get("hama_max", ""),
                "hamd_min": request.GET.get("hamd_min", ""),
                "hamd_max": request.GET.get("hamd_max", ""),
                "bai_min": request.GET.get("bai_min", ""),
                "bai_max": request.GET.get("bai_max", ""),
                "bdi_min": request.GET.get("bdi_min", ""),
                "bdi_max": request.GET.get("bdi_max", ""),
                "epilepsy_scale_min": request.GET.get("epilepsy_scale_min", ""),
                "epilepsy_scale_max": request.GET.get("epilepsy_scale_max", ""),
                "has_mri": request.GET.get("has_mri", ""),
                "has_pet": request.GET.get("has_pet", ""),
                "has_eeg": request.GET.get("has_eeg", ""),
                "has_seeg": request.GET.get("has_seeg", ""),
            }
        )

        # 是否默认展开高级搜索
        advanced_keys = [
            "admission_start",
            "admission_end",
            "evaluation_start",
            "evaluation_end",
            "age_min",
            "age_max",
            "natural_state",
            "aura",
            "moca_min",
            "moca_max",
            "hama_min",
            "hama_max",
            "hamd_min",
            "hamd_max",
            "bai_min",
            "bai_max",
            "bdi_min",
            "bdi_max",
            "epilepsy_scale_min",
            "epilepsy_scale_max",
            "has_mri",
            "has_pet",
            "has_eeg",
            "has_seeg",
        ]
        context["advanced_open"] = any(request.GET.get(k) for k in advanced_keys)

        return context

    # ---------- 内部工具方法 ----------

    def _field_exists(self, field_name: str) -> bool:
        """检查模型上是否存在某个字段，防止字段名不一致时报错。"""
        try:
            self.model._meta.get_field(field_name)
            return True
        except FieldDoesNotExist:
            return False

    def _apply_date_range_filter(self, qs, field_name, start, end):
        """针对 DateField 的范围过滤，start/end 为 'YYYY-MM-DD' 字符串。"""
        if not (start or end):
            return qs
        if not self._field_exists(field_name):
            return qs

        lookup = {}
        if start:
            lookup[f"{field_name}__gte"] = start
        if end:
            lookup[f"{field_name}__lte"] = end
        if not lookup:
            return qs

        try:
            return qs.filter(**lookup)
        except FieldError:
            return qs

    def _apply_numeric_range_filter(self, qs, field_name, min_value, max_value):
        """针对数值字段的区间过滤；无输入或字段不存在时直接返回原 qs。"""
        if not (min_value or max_value):
            return qs
        if not self._field_exists(field_name):
            return qs

        lookup = {}
        try:
            if min_value not in (None, ""):
                lookup[f"{field_name}__gte"] = float(min_value)
            if max_value not in (None, ""):
                lookup[f"{field_name}__lte"] = float(max_value)
        except ValueError:
            # 非数字输入，则忽略此条件
            return qs

        if not lookup:
            return qs

        try:
            return qs.filter(**lookup)
        except FieldError:
            return qs



class PatientCreateView(RoleRequiredMixin, CreateView):
    model = Patient
    form_class = PatientForm
    template_name = "epilepsy/patient_form.html"
    success_url = reverse_lazy("epilepsy:patient_list")
    allowed_roles = [UserRole.ADMIN, UserRole.STAFF]

    def form_valid(self, form):
        response = super().form_valid(form)
        # 使用 helper 处理上传
        handle_patient_file_uploads(self.request, self.object)

        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "patient_id": self.object.pk,
            })
        return response

    def form_invalid(self, form):
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": False,
                "errors": form.errors,
            })
        return super().form_invalid(form)


class PatientUpdateView(RoleRequiredMixin, UpdateView):
    model = Patient
    form_class = PatientForm
    template_name = "epilepsy/patient_form_partial.html"
    success_url = reverse_lazy("epilepsy:patient_list")
    allowed_roles = [UserRole.ADMIN, UserRole.STAFF]

    def get_template_names(self):
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return ["epilepsy/patient_form_partial.html"]
        return ["epilepsy/patient_form.html"]


@login_required
def patient_delete(request, pk):
    profile = getattr(request.user, "profile", None)
    if not profile or profile.role not in [UserRole.ADMIN, UserRole.STAFF]:
        return HttpResponseForbidden("无权限删除")

    patient = get_object_or_404(Patient, pk=pk)
    if request.method == "POST":
        patient.delete()
        return redirect("epilepsy:patient_list")
    return render(request, "epilepsy/patient_confirm_delete.html", {"patient": patient})


class UserListView(RoleRequiredMixin, ListView):
    model = User
    template_name = "epilepsy/user_list.html"
    context_object_name = "users"
    allowed_roles = [UserRole.ADMIN]


def user_create(request):
    deny = require_admin(request)
    if deny:
        return deny

    if request.method == "POST":
        form = UserWithRoleForm(request.POST)
        if form.is_valid():
            user = form.save()
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"success": True, "id": user.id})
            return redirect("epilepsy:user_list")
    else:
        form = UserWithRoleForm()

    template = (
        "epilepsy/user_form_partial.html"
        if request.headers.get("x-requested-with") == "XMLHttpRequest"
        else "epilepsy/user_form.html"
    )
    return render(request, template, {"form": form})


def user_edit(request, pk):
    deny = require_admin(request)
    if deny:
        return deny

    user_obj = get_object_or_404(User, pk=pk)

    if request.method == "POST":
        form = UserWithRoleForm(request.POST, instance=user_obj)
        if form.is_valid():
            form.save()
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"success": True})
            return redirect("epilepsy:user_list")
    else:
        form = UserWithRoleForm(instance=user_obj)

    template = (
        "epilepsy/user_form_partial.html"
        if request.headers.get("x-requested-with") == "XMLHttpRequest"
        else "epilepsy/user_form.html"
    )
    return render(request, template, {"form": form})


def user_toggle_active(request, pk):
    deny = require_admin(request)
    if deny:
        return deny

    user_obj = get_object_or_404(User, pk=pk)

    if request.method != "POST":
        return HttpResponseForbidden("只允许 POST 请求")

    user_obj.is_active = not user_obj.is_active
    user_obj.save()

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"success": True, "is_active": user_obj.is_active})

    return redirect("epilepsy:user_list")


def user_delete(request, pk):
    deny = require_admin(request)
    if deny:
        return deny

    user_obj = get_object_or_404(User, pk=pk)

    if request.method == "POST":
        user_obj.delete()
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"success": True})
        return redirect("epilepsy:user_list")

    return render(request, "epilepsy/user_confirm_delete.html", {"user_obj": user_obj})


class PatientDatasetListView(RoleRequiredMixin, DetailView):
    model = Patient
    template_name = "epilepsy/patient_datasets.html"
    context_object_name = "patient"
    allowed_roles = [UserRole.ADMIN, UserRole.STAFF, UserRole.GUEST]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["datasets"] = self.object.datasets.filter(is_active=True)
        return ctx


@login_required
def patient_dataset_download(request, pk):
    dataset = get_object_or_404(PatientDataset, pk=pk, is_active=True)
    profile = getattr(request.user, "profile", None)
    if not profile or profile.role not in [
        UserRole.ADMIN,
        UserRole.STAFF,
        UserRole.GUEST,
    ]:
        return HttpResponseForbidden("无权限下载")

    return render(
        request,
        "epilepsy/download_not_implemented.html",
        {"dataset": dataset},
        status=501,
    )


@login_required
def patient_files_panel(request, pk):
    patient = get_object_or_404(Patient, pk=pk)

    profile = getattr(request.user, "profile", None)
    if not profile or profile.role not in [
        UserRole.ADMIN,
        UserRole.STAFF,
        UserRole.GUEST,
    ]:
        return HttpResponseForbidden("无权限查看")

    context = {
        "patient": patient,
        "mri_files": patient.mri_files.all().order_by("-created_at"),
        "pet_files": patient.pet_files.all().order_by("-created_at"),
        "eeg_files": patient.eeg_files.all().order_by("-created_at"),
        "seeg_files": patient.seeg_files.all().order_by("-created_at"),
    }
    return render(request, "epilepsy/patient_files_panel.html", context)


@login_required
def patient_file_download(request, file_type, file_id):
    profile = getattr(request.user, "profile", None)
    if not profile or profile.role not in [
        UserRole.ADMIN,
        UserRole.STAFF,
        UserRole.GUEST,
    ]:
        return HttpResponseForbidden("无权限下载")

    model_map = {
        "mri": MRIFile,
        "pet": PETFile,
        "eeg": EEGFile,
        "seeg": SEEGFile,
    }
    model_cls = model_map.get(file_type)
    if model_cls is None:
        raise Http404("未知文件类型")

    file_obj, file_path = build_patient_file_path(model_cls, file_id)
    if not os.path.exists(file_path):
        raise Http404("文件不存在")

    return FileResponse(
        open(file_path, "rb"),
        as_attachment=True,
        filename=file_obj.file_name,
    )


def simple_logout(request):
    logout(request)
    return redirect("login")


def patient_edit(request, pk):
    patient = get_object_or_404(Patient, pk=pk)

    if request.method == "POST":
        form = PatientForm(request.POST, request.FILES, instance=patient)
        if form.is_valid():
            patient = form.save()
            handle_patient_file_uploads(request, patient)

            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"success": True})
            return redirect("epilepsy:patient_list")
    else:
        form = PatientForm(instance=patient)

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return render(request, "epilepsy/patient_form_partial.html", {"form": form})

    return render(request, "epilepsy/patient_edit.html", {
        "form": form,
        "patient": patient,
    })


def patient_detail(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    form = PatientForm(instance=patient)
    context = {"patient": patient, "form": form}

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return render(request, "epilepsy/patient_detail_partial.html", context)

    return render(request, "epilepsy/patient_detail.html", context)


@login_required
def patient_export(request, pk, fmt):
    """
    导出单个患者信息，view 只做权限 + 响应，
    实际文件生成在 helper 中完成。
    """
    patient = get_object_or_404(Patient, pk=pk)

    profile = getattr(request.user, "profile", None)
    if not profile or profile.role not in [
        UserRole.ADMIN,
        UserRole.STAFF,
        UserRole.GUEST,
    ]:
        return HttpResponseForbidden("无权限导出")

    try:
        final_path, download_filename, _ = generate_patient_info_file(patient, fmt)
    except ValueError:
        raise Http404("未知导出格式")

    return FileResponse(
        open(final_path, "rb"),
        as_attachment=True,
        filename=download_filename,
    )

class AboutView(TemplateView):
    template_name = "epilepsy/aboutus.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Try to locate /static/readme.md using Django's staticfiles finders
        md_path = finders.find("readme.md")
        if md_path is None:
            # Fallback: direct path if you know it's at BASE_DIR / "static" / "readme.md"
            md_path = os.path.join(settings.BASE_DIR, "static", "readme.md")

        try:
            with open(md_path, encoding="utf-8") as f:
                md_text = f.read()
            context["readme_html"] = markdown.markdown(
                md_text,
                extensions=[
                    "fenced_code",
                    "tables",
                    "toc",
                ],
            )
        except (FileNotFoundError, TypeError):
            context["readme_html"] = "<p>README 文件未找到。</p>"

        return context

@login_required
@require_POST
def batch_download_info(request):
    ids_str = request.POST.get('patient_ids', '')
    id_list = [int(x) for x in ids_str.split(',') if x.strip()]
    patients = Patient.objects.filter(id__in=id_list).order_by('id')

    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    response['Content-Disposition'] = 'attachment; filename="患者信息_批量导出.csv"'

    writer = csv.writer(response)
    # header
    writer.writerow([label for _, label in FIELDS_FOR_EXPORT])

    for p in patients:
        row = []
        for field_name, _ in FIELDS_FOR_EXPORT:
            value = getattr(p, field_name, '')
            if isinstance(value, (datetime.date, datetime.datetime)):
                value = value.strftime('%Y-%m-%d')
            row.append(value if value is not None else '')
        writer.writerow(row)

    return response

@login_required
@require_POST
def batch_delete_patients(request):
    ids_str = request.POST.get('patient_ids', '')
    id_list = [int(x) for x in ids_str.split(',') if x.strip()]

    if not id_list:
        messages.warning(request, '未选择任何患者。')
        return redirect('epilepsy:patient_list')

    count = Patient.objects.filter(id__in=id_list).count()
    Patient.objects.filter(id__in=id_list).delete()

    messages.success(request, f'已删除 {count} 位患者。')
    return redirect('epilepsy:patient_list')

MODALITY_MODEL_MAP = {
    'PET': (PETFile, 'PET'),
    'MRI': (MRIFile, 'MRI'),
    'EEG': (EEGFile, 'EEG'),
    'sEEG': (SEEGFile, 'sEEG'),
}

@login_required
@require_POST
def batch_download_files(request):
    ids_str = request.POST.get('patient_ids', '')
    modalities_str = request.POST.get('modalities', '')

    patient_ids = [int(x) for x in ids_str.split(',') if x.strip()]
    modality_keys = [m for m in modalities_str.split(',') if m.strip()]

    patients = Patient.objects.filter(id__in=patient_ids)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for patient in patients:
            # 用已有字段代替 case_number，优先病历号，其次床号、影像号，最后用 ID
            identifier = (
                patient.medical_record_number
                or patient.bed_number
                or patient.imaging_number
                or str(patient.id)
            )
            folder_prefix = f"{identifier}-{patient.name}"

            for key in modality_keys:
                model_info = MODALITY_MODEL_MAP.get(key)
                if not model_info:
                    continue
                model, modality_folder = model_info
                files_qs = model.objects.filter(patient=patient)

                for obj in files_qs:
                    # 使用现有工具函数，保证路径与单文件下载一致
                    file_obj, file_path = build_patient_file_path(model, obj.id)

                    if not os.path.exists(file_path):
                        continue

                    # 压缩包中显示原始文件名，而不是 hash
                    filename = getattr(file_obj, "file_name", os.path.basename(file_path))
                    arcname = f"{folder_prefix}/{modality_folder}/{filename}"

                    zf.write(file_path, arcname)

    buffer.seek(0)
    response = HttpResponse(buffer.getvalue(), content_type='application/zip')
    response['Content-Disposition'] = 'attachment; filename="患者文件_批量下载.zip"'
    return response

