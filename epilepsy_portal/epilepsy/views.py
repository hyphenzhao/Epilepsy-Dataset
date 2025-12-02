import shutil
import psutil

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponseForbidden
from django.views.generic import ListView, CreateView, UpdateView

from .mixins import RoleRequiredMixin
from .models import Patient, UserProfile, UserRole, PatientDataset
from django.contrib.auth import get_user_model
from django.urls import reverse_lazy
from .forms import *
from django.db.models import Q
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, DetailView
from django.contrib.auth import logout

from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse

User = get_user_model()


@login_required
def dashboard(request):
    cpu = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    disk = shutil.disk_usage("/")

    context = {
        "cpu_percent": cpu,
        "mem_percent": mem.percent,
        "mem_used": mem.used,
        "mem_total": mem.total,
        "disk_percent": round(disk.used / disk.total * 100, 1),
        "disk_used": disk.used,
        "disk_total": disk.total,
    }
    return render(request, "epilepsy/dashboard.html", context)


class PatientListView(RoleRequiredMixin, ListView):
    model = Patient
    template_name = "epilepsy/patient_list.html"
    context_object_name = "patients"
    paginate_by = 20  # 每页 20 条，可按需要调整
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


class PatientUpdateView(RoleRequiredMixin, UpdateView):
    model = Patient
    form_class = PatientForm
    template_name = "epilepsy/patient_form_partial.html"  # for slide-in panel
    success_url = reverse_lazy("epilepsy:patient_list")
    allowed_roles = [UserRole.ADMIN, UserRole.STAFF]

    def get_template_names(self):
        # If AJAX request (for slide-in), use partial template
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

    # TODO: 在这里和 DGPF 集成
    # - 可以根据 dataset.globus_endpoint_id / dataset.globus_path
    #   构造一个链接跳转到 DGPF 的 transfer 页面
    # - 或者调用你 portal 中封装好的 transfer 视图

    return render(
        request,
        "epilepsy/download_not_implemented.html",
        {"dataset": dataset},
        status=501,  # 501 Not Implemented
    )

def simple_logout(request):
    logout(request)
    return redirect("login")  # "login" 是 accounts/login/ 的 url name

def patient_edit(request, pk):
    patient = get_object_or_404(Patient, pk=pk)

    if request.method == "POST":
        form = PatientForm(request.POST, instance=patient)
        if form.is_valid():
            form.save()
            # AJAX 保存成功返回 JSON
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"success": True})
            # 非 AJAX 情况下，正常重定向回列表
            return redirect("epilepsy:patient_list")
    else:
        form = PatientForm(instance=patient)

    # AJAX 请求：仅返回 partial（不包 base 模板）
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return render(request, "epilepsy/patient_form_partial.html", {
            "form": form,
        })

    # 直接访问 /patients/<id>/edit/ 的 fallback，全页编辑也能用
    return render(request, "epilepsy/patient_edit.html", {
        "form": form,
        "patient": patient,
    })
