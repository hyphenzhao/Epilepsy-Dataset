# epilepsy/views.py

import os, csv, datetime, io, zipfile, re
import mimetypes
from django.utils.encoding import smart_str
from django.conf import settings
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model, logout
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, JsonResponse, HttpResponseForbidden, FileResponse, Http404
from django.views.generic import ListView, CreateView, UpdateView, DetailView, TemplateView
from django.urls import reverse_lazy
from django.db import models
from django.db.models import Q, F
from django.db.models.functions import Cast
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
import logging
from pprint import pformat

form_debug_logger = logging.getLogger("epilepsy.formdebug")


def log_invalid_form(request, form, *, tag="PatientForm"):
    """
    Log form validation errors to runserver console.
    - field errors + non-field errors
    - request content-type + POST keys + FILES summary
    """
    # Keep it safe in prod
    try:
        from django.conf import settings
        if not getattr(settings, "DEBUG", False):
            return
    except Exception:
        pass

    try:
        content_type = request.META.get("CONTENT_TYPE", "")
        post_keys = sorted(list(request.POST.keys()))
        files_keys = sorted(list(request.FILES.keys()))

        files_summary = {}
        for k in files_keys:
            f = request.FILES.get(k)
            if f is None:
                continue
            files_summary[k] = {
                "name": getattr(f, "name", None),
                "size": getattr(f, "size", None),
                "content_type": getattr(f, "content_type", None),
            }

        # JSON-serializable errors with codes
        try:
            err_json = form.errors.get_json_data(escape_html=False)
        except Exception:
            err_json = {k: [str(e) for e in v] for k, v in form.errors.items()}

        # Include field labels for readability
        labeled = {}
        for field, errs in err_json.items():
            if field == "__all__":
                labeled[field] = errs
                continue
            label = None
            try:
                if field in form.fields:
                    label = form.fields[field].label
            except Exception:
                pass
            labeled[f"{field} ({label})" if label else field] = errs

        payload = {
            "tag": tag,
            "path": request.path,
            "method": request.method,
            "is_ajax": request.headers.get("x-requested-with") == "XMLHttpRequest",
            "content_type": content_type,
            "POST_keys": post_keys,
            "FILES_keys": files_keys,
            "FILES_summary": files_summary,
            "non_field_errors": list(form.non_field_errors()),
            "errors": labeled,
        }

        # logging + print ensures it shows in runserver output even if logging config is minimal
        form_debug_logger.warning("INVALID FORM:\n%s", pformat(payload, width=140))
        print("INVALID FORM:\n", pformat(payload, width=140))

    except Exception as e:
        form_debug_logger.exception("Failed to log invalid form: %s", e)
        print("Failed to log invalid form:", repr(e))

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

    @staticmethod
    def _pinyin_natural_key(value):
        """中英混排排序 key：拼音（汉字）+ 自然数。

        - 需要安装 pypinyin：pip install pypinyin
        - 若未安装则退化为普通 lower() 排序
        """
        import re

        s = "" if value is None else str(value)
        s = s.strip()

        try:
            from pypinyin import lazy_pinyin
        except Exception:
            lazy_pinyin = None

        def to_pinyin(text: str) -> str:
            if not text:
                return ""
            if lazy_pinyin is None:
                return text.lower()
            # errors=lambda x: x 让非汉字保留原字符
            return "".join(lazy_pinyin(text, errors=lambda x: x)).lower()

        parts = []
        for token in re.split(r"(\d+)", s):
            if token == "":
                continue
            if token.isdigit():
                parts.append((0, int(token)))
            else:
                parts.append((1, to_pinyin(token)))

        return tuple(parts)

    def get_queryset(self):
        qs = super().get_queryset()
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

        # 自然发作状态：对应模型字段 seizure_state (AWAKE/SLEEP/BOTH)
        natural_state = request.GET.get("natural_state", "").strip()
        if self._field_exists("seizure_state"):
            # 兼容旧版（1=有, 0=无）：这里解释为“该字段是否已填写”
            if natural_state in ("1", "0"):
                try:
                    if natural_state == "1":
                        qs = qs.exclude(seizure_state="").exclude(seizure_state__isnull=True)
                    else:
                        qs = qs.filter(Q(seizure_state="") | Q(seizure_state__isnull=True))
                except FieldError:
                    pass
            elif natural_state:
                try:
                    qs = qs.filter(seizure_state=natural_state)
                except FieldError:
                    pass

        # 先兆：对应模型字段 aura (Y/N)。若存在 major_aura，也一并纳入“有/无”的判断
        aura = request.GET.get("aura", "").strip()
        # 兼容旧版（1=有, 0=无）
        if aura in ("1", "0"):
            aura = "Y" if aura == "1" else "N"

        if aura in ("Y", "N") and self._field_exists("aura"):
            q_yes = Q(aura="Y")
            q_no = Q(aura="N") | Q(aura="") | Q(aura__isnull=True)

            if self._field_exists("major_aura"):
                q_yes = q_yes | Q(major_aura="Y")
                q_no = q_no & (Q(major_aura="N") | Q(major_aura="") | Q(major_aura__isnull=True))

            try:
                qs = qs.filter(q_yes if aura == "Y" else q_no)
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

        # 关键字过滤：按字段分组（PATIENT_GROUP_FIELDS）
        # - 输入支持“空格分隔多个词”；多个词之间采用 AND（逐词过滤），分组内字段采用 OR
        # - 对非文本字段（JSONField/数值/布尔等）先 Cast 成 TextField，再做 icontains，避免数据库不支持导致过滤无效
        #
        # ⚠️ 兼容性说明：
        # 你的 PATIENT_GROUP_FIELDS 可能会因为“字段名/标签（verbose_name）”混用而失效（导致 fields 为空或全都 _field_exists=False）。
        # 下面会尽量把 PATIENT_GROUP_FIELDS 的值解析回真实字段名；同时提供 param 级别的兜底字段列表。
        group_param_to_group_names = {
            "kw_stage1_noninvasive": ["一期无创性评估结果", "一期无创性评估", "无创性评估结果"],
            "kw_seeg_discharge": [
                "SEEG 发作间期及发作期放电",
                "sEEG 发作间期及发作期放电",
                "SEEG发作间期及发作期放电",
                "sEEG发作间期及发作期放电",
            ],
            "kw_stage2_invasive": ["二期有创性评估结果", "二期有创性评估", "有创性评估结果"],
            "kw_surgery_plan": ["外科切除计划", "手术切除计划", "外科计划"],
        }

        # param -> 兜底字段名（确保关键词过滤至少能作用在这些字段上）
        fallback_param_fields = {
            "kw_stage1_noninvasive": [
                "first_stage_lateralization",
                "first_stage_region",
                "first_stage_location",
            ],
            "kw_seeg_discharge": [
                "seeg_primary_discharge_zone",
                "seeg_secondary_discharge_zone",
                "seeg_other_discharge_zone",
                "seeg_ictal_onset_zone",
                "seeg_ictal_spread_zone_sequence",
                "seeg_interictal_overall",
                "seeg_group1",
                "seeg_group2",
                "seeg_group3",
                "seeg_ictal",
                "seeg_thermocoagulation",
            ],
            "kw_stage2_invasive": [
                "second_stage_core_zone",
                "second_stage_hypothesis_zone",
            ],
            "kw_surgery_plan": [
                "resection_plan_convex",
                "resection_plan_concave",
                "resection_plan",
            ],
        }

        def _normalize_fields(obj):
            """把 PATIENT_GROUP_FIELDS[group] 的各种可能形态统一成 field token 列表。"""
            if obj is None:
                return []
            if isinstance(obj, dict):
                iterable = obj.keys()
            elif isinstance(obj, (list, tuple, set)):
                iterable = obj
            else:
                return []
            out = []
            for it in iterable:
                if isinstance(it, (list, tuple)) and it:
                    out.append(str(it[0]).strip())
                else:
                    out.append(str(it).strip())
            return [x for x in out if x]

        def _resolve_field_token(token: str):
            """把 token（可能是字段名/verbose_name/其它标签）解析成真实字段名。"""
            token = ("" if token is None else str(token)).strip()
            if not token:
                return None

            # ORM 路径（关联字段）直接放行
            if "__" in token:
                return token

            # 真实字段名
            if self._field_exists(token):
                return token

            # verbose_name / 标签 -> 字段名
            want = re.sub(r"\s+", "", token)
            for f in self.model._meta.get_fields():
                if not getattr(f, "concrete", False) or getattr(f, "many_to_many", False):
                    continue
                vn = getattr(f, "verbose_name", None)
                if vn is None:
                    continue
                vn_norm = re.sub(r"\s+", "", str(vn))
                if vn_norm == want or vn_norm.startswith(want) or (want and want in vn_norm):
                    return f.name

            return None

        def _get_group_fields(param: str, candidates):
            """从 PATIENT_GROUP_FIELDS 取字段，并做最大化容错解析；必要时使用 fallback。"""
            candidates = list(candidates or [])

            # 额外兼容：有人会把 key 写成 param 或简写
            candidates.extend([
                param,
                param.replace("kw_", ""),
                param.replace("kw_", "").replace("_", " "),
            ])

            raw_fields = []

            # 1) 直接 key 命中
            for name in candidates:
                if name in (PATIENT_GROUP_FIELDS or {}):
                    raw_fields = _normalize_fields((PATIENT_GROUP_FIELDS or {}).get(name))
                    break

            # 2) key 去空白后命中
            if not raw_fields:
                normalized = {re.sub(r"\s+", "", str(k)): v for k, v in (PATIENT_GROUP_FIELDS or {}).items()}
                for name in candidates:
                    key = re.sub(r"\s+", "", str(name or ""))
                    if key in normalized:
                        raw_fields = _normalize_fields(normalized.get(key))
                        break

            # 3) token -> 真实字段名（或 ORM 路径）
            resolved = []
            for token in raw_fields:
                real = _resolve_field_token(token)
                if real:
                    resolved.append(real)

            # 4) param 兜底字段
            if not resolved and param in fallback_param_fields:
                for f in fallback_param_fields[param]:
                    if "__" in f or self._field_exists(f):
                        resolved.append(f)

            # 去重（保序）
            seen = set()
            out = []
            for f in resolved:
                if f in seen:
                    continue
                seen.add(f)
                out.append(f)
            return out

        for param, group_names in group_param_to_group_names.items():
            raw = (request.GET.get(param) or "").strip()
            if not raw:
                continue

            fields = _get_group_fields(param, group_names)
            if not fields:
                continue

            # 为分组内“非文本字段”准备 Cast 注解
            field_alias = {}
            annotations = {}
            for field in fields:
                if "__" in field:
                    continue
                if not self._field_exists(field):
                    continue
                try:
                    mf = self.model._meta.get_field(field)
                except Exception:
                    continue

                if isinstance(mf, (models.CharField, models.TextField)):
                    continue

                alias = "cast_" + re.sub(r"[^0-9a-zA-Z_]+", "_", field)
                field_alias[field] = alias
                annotations[alias] = Cast(F(field), output_field=models.TextField())

            if annotations:
                qs = qs.annotate(**annotations)

            # 多关键词：逐词 AND；分组字段：OR
            for kw in [x for x in re.split(r"\s+", raw) if x]:
                q_obj = Q()
                for field in fields:
                    try:
                        if "__" in field:
                            q_obj |= Q(**{f"{field}__icontains": kw})
                        elif field in field_alias:
                            q_obj |= Q(**{f"{field_alias[field]}__icontains": kw})
                        else:
                            if not self._field_exists(field):
                                continue
                            q_obj |= Q(**{f"{field}__icontains": kw})
                    except Exception:
                        continue

                if q_obj:
                    qs = qs.filter(q_obj)

        # 防止关联过滤导致重复
        qs = qs.distinct()

        # ---------- 排序（点击表头） ----------
        sort = (request.GET.get("sort") or "").strip()
        direction = (request.GET.get("dir") or "asc").lower()
        if direction not in ("asc", "desc"):
            direction = "asc"
        reverse = (direction == "desc")

        allowed = {"name", "gender", "birthday", "bed_number", "admission_date"}
        if sort not in allowed:
            return qs.order_by("id")

        # 日期字段：用数据库排序
        if sort in ("birthday", "admission_date"):
            order = f"-{sort}" if reverse else sort
            return qs.order_by(order, "id")

        # 其余：Python 侧排序（支持拼音/自定义顺序）
        objs = list(qs)

        # 先按 id 排一次，保证主键稳定（随后再按主排序规则排序）
        objs.sort(key=lambda o: o.id)

        if sort == "gender":
            order_map = {"M": 0, "F": 1, "O": 2}
            objs.sort(key=lambda o: order_map.get(getattr(o, "gender", None), 9), reverse=reverse)
            return objs

        if sort == "name":
            objs.sort(key=lambda o: self._pinyin_natural_key(getattr(o, "name", "")), reverse=reverse)
            return objs

        if sort == "bed_number":
            objs.sort(key=lambda o: self._pinyin_natural_key(getattr(o, "bed_number", "")), reverse=reverse)
            return objs

        return objs


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
                "kw_stage1_noninvasive": request.GET.get("kw_stage1_noninvasive", ""),
                "kw_seeg_discharge": request.GET.get("kw_seeg_discharge", ""),
                "kw_stage2_invasive": request.GET.get("kw_stage2_invasive", ""),
                "kw_surgery_plan": request.GET.get("kw_surgery_plan", ""),
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
            "kw_stage1_noninvasive",
            "kw_seeg_discharge",
            "kw_stage2_invasive",
            "kw_surgery_plan",
        ]
        context["advanced_open"] = any(request.GET.get(k) for k in advanced_keys)


        # ---------- 表头排序回显 & 链接 ----------
        sort = (request.GET.get("sort") or "").strip()
        direction = (request.GET.get("dir") or "asc").lower()
        if direction not in ("asc", "desc"):
            direction = "asc"

        allowed = {"name", "gender", "birthday", "bed_number", "admission_date"}
        if sort not in allowed:
            sort = ""

        context["sort"] = sort
        context["dir"] = direction

        # base_qs：保留除 sort/dir/page 之外的查询参数（用于表头排序和分页）
        params = request.GET.copy()
        for k in ("sort", "dir", "page"):
            if k in params:
                params.pop(k)
        base_qs = params.urlencode()
        context["base_qs"] = base_qs

        def _mk_url(field: str, next_dir: str) -> str:
            prefix = f"?{base_qs}&" if base_qs else "?"
            return f"{prefix}sort={field}&dir={next_dir}"

        sort_fields = ["name", "gender", "birthday", "bed_number", "admission_date"]
        sort_links = {}
        for field in sort_fields:
            is_cur = (sort == field)
            next_dir = "desc" if (is_cur and direction == "asc") else "asc"
            icon = "▲" if (is_cur and direction == "asc") else ("▼" if (is_cur and direction == "desc") else "")
            sort_links[field] = {"url": _mk_url(field, next_dir), "icon": icon}

        context["sort_links"] = sort_links

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
    # success_url = reverse_lazy("epilepsy:patient_list")
    allowed_roles = [UserRole.ADMIN, UserRole.STAFF]

    def form_valid(self, form):
        self.object = form.save()
        handle_patient_file_uploads(self.request, self.object)

        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse(
               {"success": True, "patient_id": self.object.pk, "keep_open": True}
            )

        messages.success(self.request, "保存成功")
        return redirect(self.request.path)

    
    def form_invalid(self, form):
        log_invalid_form(self.request, form, tag="PatientCreateView.PatientForm")

        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            try:
                data = form.errors.get_json_data(escape_html=True)
                errors = {k: [e.get("message", "") for e in v] for k, v in data.items()}
            except Exception:
                errors = {k: [str(e) for e in v] for k, v in form.errors.items()}
            return JsonResponse({"success": False, "errors": errors})
        return super().form_invalid(form)


class PatientUpdateView(RoleRequiredMixin, UpdateView):
    model = Patient
    form_class = PatientForm
    template_name = "epilepsy/patient_form_partial.html"
    # success_url = reverse_lazy("epilepsy:patient_list")
    allowed_roles = [UserRole.ADMIN, UserRole.STAFF]

    def get_template_names(self):
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return ["epilepsy/patient_form_partial.html"]
        return ["epilepsy/patient_form.html"]

    def form_valid(self, form):
        # 手动保存，避免 UpdateView 默认 success_url 跳转
        self.object = form.save()
        handle_patient_file_uploads(self.request, self.object)

        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse(
                {"success": True, "patient_id": self.object.pk, "keep_open": True}
            )

        messages.success(self.request, "保存成功")
        return redirect(self.request.path)

    
    def form_invalid(self, form):
        log_invalid_form(self.request, form, tag="PatientUpdateView.PatientForm")

        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            try:
                data = form.errors.get_json_data(escape_html=True)
                errors = {k: [e.get("message", "") for e in v] for k, v in data.items()}
            except Exception:
                errors = {k: [str(e) for e in v] for k, v in form.errors.items()}
            return JsonResponse({"success": False, "errors": errors})
        return super().form_invalid(form)

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
                return JsonResponse({"success": True, "keep_open": True})
            messages.success(request, "保存成功")
            return redirect(request.path)
                # return JsonResponse({"success": True})
            # return redirect("epilepsy:patient_list")
        else:
            log_invalid_form(request, form, tag="patient_edit.PatientForm")
    else:
        form = PatientForm(instance=patient)

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return render(request, "epilepsy/patient_form_partial.html", {"form": form})

    return render(request, "epilepsy/patient_form.html", {
        "form": form,
        "patient": patient,
    })



def patient_detail(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    form = PatientForm(instance=patient)

    allowed_ext = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

    def first_three_images(qs):
        out = []
        for f in qs.order_by("-created_at"):
            ext = os.path.splitext(getattr(f, "file_name", "") or "")[1].lower()
            if ext in allowed_ext:
                out.append(f)
            if len(out) >= 60:
                break
        return out
    def all_images(qs):
        out = []
        for f in qs.order_by("-created_at"):
            ext = os.path.splitext(getattr(f, "file_name", "") or "")[1].lower()
            if ext in allowed_ext:
                out.append(f)
        return out
    context = {
        "patient": patient,
        "form": form,
        # "preview_mri": first_three_images(patient.mri_files.all()),
        # "preview_pet": first_three_images(patient.pet_files.all()),
        # "preview_eeg": first_three_images(patient.eeg_files.all()),
        # "preview_seeg": first_three_images(patient.seeg_files.all()),
        "preview_mri": all_images(patient.mri_files.all()),
        "preview_pet": all_images(patient.pet_files.all()),
        "preview_eeg": all_images(patient.eeg_files.all()),
        "preview_seeg": all_images(patient.seeg_files.all()),
    }

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

@login_required
def patient_file_preview(request, file_type, file_id):
    """
    用于 <img src="..."> 预览：Content-Disposition inline，不强制下载。
    仅允许图片类型，避免浏览器直接打开非图片内容带来的风险。
    """
    profile = getattr(request.user, "profile", None)
    if not profile or profile.role not in [UserRole.ADMIN, UserRole.STAFF, UserRole.GUEST]:
        return HttpResponseForbidden("无权限查看")

    model_map = {"mri": MRIFile, "pet": PETFile, "eeg": EEGFile, "seeg": SEEGFile}
    model_cls = model_map.get(file_type)
    if model_cls is None:
        raise Http404("未知文件类型")

    file_obj, file_path = build_patient_file_path(model_cls, file_id)
    if not os.path.exists(file_path):
        raise Http404("文件不存在")

    # 仅允许常见图片扩展名（按 file_name）
    allowed_ext = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
    ext = os.path.splitext(getattr(file_obj, "file_name", "") or "")[1].lower()
    if ext not in allowed_ext:
        raise Http404("不支持预览的文件类型")

    content_type, _ = mimetypes.guess_type(file_obj.file_name)
    content_type = content_type or "application/octet-stream"

    resp = FileResponse(open(file_path, "rb"), content_type=content_type)
    resp["Content-Disposition"] = f'inline; filename="{smart_str(file_obj.file_name)}"'
    return resp
