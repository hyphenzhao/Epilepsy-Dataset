"""API credentials encrypted with the deployment's Django secret key."""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models


class EncryptedAPIKeyField(models.TextField):
    prefix = "llm-key-v1:"

    def _cipher(self):
        digest = hashlib.sha256((settings.SECRET_KEY + ":epilepsy-llm-api-key").encode()).digest()
        return Fernet(base64.urlsafe_b64encode(digest))

    def from_db_value(self, value, expression, connection):
        if value and value.startswith(self.prefix):
            try:
                return self._cipher().decrypt(value[len(self.prefix):].encode()).decode()
            except InvalidToken as exc:
                raise ImproperlyConfigured("LLM API 密钥无法解密，请恢复原部署密钥或重新填写 API Key。") from exc
        return value

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if not value:
            return ""
        return self.prefix + self._cipher().encrypt(value.encode()).decode()
