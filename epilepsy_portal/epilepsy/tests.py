from datetime import date
import io
import zipfile

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Patient, UserProfile, UserRole


class PatientPermissionsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.patient = Patient.objects.create(
            name="Permission test patient",
            gender="M",
            birthday=date(1990, 1, 1),
            handedness="R",
            admission_date=date(2025, 1, 1),
        )
        cls.users = {}
        for role in (UserRole.ADMIN, UserRole.STAFF, UserRole.GUEST):
            user = get_user_model().objects.create_user(username=role.lower())
            UserProfile.objects.create(user=user, role=role)
            cls.users[role] = user
        cls.unprofiled_user = get_user_model().objects.create_user(username="no_profile")

    def patient_url(self, name):
        return reverse(f"epilepsy:{name}", args=[self.patient.pk])

    def edit_data(self, name="Updated patient"):
        return {
            "name": name,
            "gender": "M",
            "birthday": "1990-01-01",
            "handedness": "R",
            "admission_date": "2025-01-01",
        }

    def protected_requests(self):
        return [
            ("get", reverse("epilepsy:dashboard"), {}),
            ("get", self.patient_url("patient_detail"), {}),
            ("get", self.patient_url("patient_edit"), {}),
            ("post", self.patient_url("patient_edit"), self.edit_data()),
            ("get", self.patient_url("patient_followup_detail"), {}),
            ("get", self.patient_url("patient_followup_edit"), {}),
            ("post", reverse("epilepsy:batch_delete_patients"), {"patient_ids": str(self.patient.pk)}),
            ("post", reverse("epilepsy:batch_download_info"), {"patient_ids": str(self.patient.pk)}),
            ("post", reverse("epilepsy:batch_download_files"), {"patient_ids": str(self.patient.pk), "modalities": "MRI"}),
        ]

    def test_anonymous_patient_requests_redirect_to_login(self):
        for method, url, data in self.protected_requests():
            with self.subTest(method=method, url=url):
                response = getattr(self.client, method)(url, data)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.url.startswith(reverse("login") + "?next="))
        self.patient.refresh_from_db()
        self.assertEqual(self.patient.name, "Permission test patient")

    def test_users_without_a_profile_cannot_access_patient_data(self):
        self.client.force_login(self.unprofiled_user)
        for method, url, data in self.protected_requests():
            with self.subTest(method=method, url=url):
                response = getattr(self.client, method)(url, data)
                self.assertEqual(response.status_code, 403)
        self.patient.refresh_from_db()
        self.assertEqual(self.patient.name, "Permission test patient")

    def test_guest_cannot_edit_or_delete_patients(self):
        self.client.force_login(self.users[UserRole.GUEST])
        for method, url, data in [
            ("get", self.patient_url("patient_edit"), {}),
            ("post", self.patient_url("patient_edit"), self.edit_data()),
            ("post", self.patient_url("patient_delete"), {}),
            ("post", reverse("epilepsy:batch_delete_patients"), {"patient_ids": str(self.patient.pk)}),
        ]:
            with self.subTest(method=method, url=url):
                self.assertEqual(getattr(self.client, method)(url, data).status_code, 403)
        self.patient.refresh_from_db()
        self.assertEqual(self.patient.name, "Permission test patient")

    def test_guest_followup_edit_does_not_create_records(self):
        self.patient.followups.all().delete()
        self.client.force_login(self.users[UserRole.GUEST])
        for method in ("get", "post"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(self.patient_url("patient_followup_edit"))
                self.assertEqual(response.status_code, 403)
                self.assertFalse(self.patient.followups.exists())

    def test_guest_can_read_patient_details_and_followups(self):
        self.client.force_login(self.users[UserRole.GUEST])
        for name in ("patient_detail", "patient_followup_detail"):
            with self.subTest(name=name):
                response = self.client.get(self.patient_url(name), HTTP_X_REQUESTED_WITH="XMLHttpRequest")
                self.assertContains(response, self.patient.name)

    def test_guest_can_download_patient_information_and_files(self):
        self.client.force_login(self.users[UserRole.GUEST])
        response = self.client.post(reverse("epilepsy:batch_download_info"), {"patient_ids": str(self.patient.pk)})
        self.assertContains(response, self.patient.name)
        response = self.client.post(
            reverse("epilepsy:batch_download_files"),
            {"patient_ids": str(self.patient.pk), "modalities": "MRI"},
        )
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(archive.namelist(), [])

    def test_admin_and_staff_can_edit_patients(self):
        for role in (UserRole.ADMIN, UserRole.STAFF):
            with self.subTest(role=role):
                self.client.force_login(self.users[role])
                response = self.client.post(
                    self.patient_url("patient_edit"),
                    self.edit_data(name=f"Updated by {role}"),
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["success"])
                self.patient.refresh_from_db()
                self.assertEqual(self.patient.name, f"Updated by {role}")

    def test_admin_and_staff_can_delete_patients_in_bulk(self):
        for role in (UserRole.ADMIN, UserRole.STAFF):
            with self.subTest(role=role):
                patient = Patient.objects.create(
                    name=f"Delete by {role}",
                    gender="M",
                    birthday=date(1990, 1, 1),
                    admission_date=date(2025, 1, 1),
                )
                self.client.force_login(self.users[role])
                response = self.client.post(reverse("epilepsy:batch_delete_patients"), {"patient_ids": str(patient.pk)})
                self.assertRedirects(response, reverse("epilepsy:patient_list"), fetch_redirect_response=False)
                self.assertFalse(Patient.objects.filter(pk=patient.pk).exists())
