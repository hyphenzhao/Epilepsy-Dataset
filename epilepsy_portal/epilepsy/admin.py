from django.contrib import admin
from .models import Patient, UserProfile, PatientDataset


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("name", "gender", "bed_number", "admission_date")
    search_fields = ("name", "bed_number", "department")


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role")
    list_filter = ("role",)


@admin.register(PatientDataset)
class PatientDatasetAdmin(admin.ModelAdmin):
    list_display = ("patient", "name", "globus_endpoint_id", "is_active")
    search_fields = ("patient__name", "name")
