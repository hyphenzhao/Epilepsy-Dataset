# epilepsy/views.py

import os

from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model, logout
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponseForbidden, FileResponse, Http404
from django.views.generic import ListView, CreateView, UpdateView, DetailView
from django.urls import reverse_lazy
from django.db.models import Q

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

User = get_user_model()


@login_required
def dashboard(request):
    """
    仪表盘视图：只负责请求 + 渲染，业务逻辑放在 helper。
    """
    context = build_dashboard_context()
    return render(request, "epilepsy/dashboard.html", context)


class PatientListView(RoleRequiredMixin, ListView):
    # 原样保留
    model = Patient
    template_name = "epilepsy/patient_list.html"
    context_object_name = "patients"
    paginate_by = 20
    allowed_roles = [UserRole.ADMIN, UserRole.STAFF, UserRole.GUEST]

    def get_queryset(self):
        qs = super().get_queryset().order_by("id")
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(name__icontains=q)
                | Q(bed_number__icontains=q)
                | Q(department__icontains=q)
            )
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["q"] = self.request.GET.get("q", "").strip()
        return context


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
