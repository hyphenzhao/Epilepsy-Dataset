from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from epilepsy.models import UserProfile, UserRole
from .models import KnowledgeChunk, KnowledgeDocument


class KnowledgeUploadConfirmationTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="upload_staff")
        UserProfile.objects.create(user=user, role=UserRole.STAFF)
        self.client.force_login(user)
        self.document = KnowledgeDocument.objects.create(
            title="Upload confirmation test",
            doc_type="case_report",
            text_content="Test document content",
            metadata={"patient_age": "20"},
            is_approved=False,
            created_by=user,
        )
        self.raw_chunks = [
            {"text": "First chunk", "section_label": "History"},
            {"text": "Second chunk", "section_label": "Assessment"},
        ]

    def confirm(self):
        return self.client.post(reverse("knowledge:upload"), {
            "action": "confirm",
            "doc_id": self.document.pk,
            "meta_patient_age": "30",
        })

    def assert_pending(self):
        self.document.refresh_from_db()
        self.assertFalse(self.document.is_approved)
        self.assertEqual(self.document.metadata, {"patient_age": "30"})

    def test_vector_failure_keeps_document_pending(self):
        with patch("knowledge.chunker.chunk_text", return_value=self.raw_chunks), \
                patch("knowledge.vector_store.add_chunks", side_effect=RuntimeError("Vector service unavailable")):
            response = self.confirm()
        self.assertRedirects(response, reverse("knowledge:upload"), fetch_redirect_response=False)
        self.assert_pending()
        self.assertFalse(self.document.chunks.exists())

    def test_empty_chunks_do_not_approve_document(self):
        with patch("knowledge.chunker.chunk_text", return_value=[]), \
                patch("knowledge.vector_store.add_chunks") as add_chunks:
            response = self.confirm()
        self.assertRedirects(response, reverse("knowledge:upload"), fetch_redirect_response=False)
        add_chunks.assert_not_called()
        self.assert_pending()
        self.assertFalse(self.document.chunks.exists())

    def test_success_approves_document_after_storing_chunks(self):
        def check_pending(*args, **kwargs):
            self.document.refresh_from_db()
            self.assertFalse(self.document.is_approved)

        with patch("knowledge.chunker.chunk_text", return_value=self.raw_chunks), \
                patch("knowledge.vector_store.add_chunks", side_effect=check_pending) as add_chunks:
            response = self.confirm()
        self.assertRedirects(response, reverse("knowledge:list"), fetch_redirect_response=False)
        add_chunks.assert_called_once_with(
            "case_report",
            [f"doc_{self.document.pk}_chunk_0", f"doc_{self.document.pk}_chunk_1"],
            ["First chunk", "Second chunk"],
            [
                {"document_id": self.document.pk, "doc_type": "case_report", "section_label": "History"},
                {"document_id": self.document.pk, "doc_type": "case_report", "section_label": "Assessment"},
            ],
        )
        self.document.refresh_from_db()
        self.assertTrue(self.document.is_approved)
        self.assertEqual(list(self.document.chunks.values_list("text", flat=True)), ["First chunk", "Second chunk"])

    def test_database_failure_rolls_back_partial_chunk_replacement(self):
        original = KnowledgeChunk.objects.create(
            document=self.document,
            chunk_index=0,
            text="Original chunk",
            embedding_id="original_chunk",
        )

        def partially_save_then_fail(chunks):
            chunks[0].save()
            raise RuntimeError("Chunk storage failure")

        with patch("knowledge.chunker.chunk_text", return_value=self.raw_chunks), \
                patch("knowledge.vector_store.add_chunks"), \
                patch("knowledge.views.KnowledgeChunk.objects.bulk_create", side_effect=partially_save_then_fail):
            response = self.confirm()
        self.assertRedirects(response, reverse("knowledge:upload"), fetch_redirect_response=False)
        self.assert_pending()
        self.assertEqual(list(self.document.chunks.values_list("pk", "text")), [(original.pk, "Original chunk")])
