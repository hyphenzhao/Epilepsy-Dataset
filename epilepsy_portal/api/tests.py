from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from api.views import EndpointSearchView


TRANSFER_SCOPE = "urn:globus:auth:scope:transfer.api.globus.org:all"


class GlobusAuthenticationTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def request_endpoint(self, social_account):
        social_auth = Mock()
        social_auth.filter.return_value.first.return_value = social_account
        user = SimpleNamespace(is_authenticated=True, social_auth=social_auth)
        request = self.factory.get("/api/endpoints/")
        request.session = {}
        force_authenticate(request, user=user)
        response = EndpointSearchView.as_view({"get": "list"})(request)
        social_auth.filter.assert_called_once_with(provider="globus")
        return response, request

    @patch("api.views.requests.get")
    def test_local_login_without_globus_returns_forbidden(self, get):
        response, request = self.request_endpoint(None)
        self.assertEqual(response.status_code, 403)
        self.assertIn("Globus", str(response.data["detail"]))
        self.assertNotIn("transfer_token", request.session)
        get.assert_not_called()

    @patch("api.views.requests.get")
    def test_missing_transfer_authorization_returns_forbidden(self, get):
        account = SimpleNamespace(extra_data={
            "token_type": "Bearer",
            "other_tokens": [{"scope": "openid", "access_token": "dummy"}],
        })
        response, _ = self.request_endpoint(account)
        self.assertEqual(response.status_code, 403)
        get.assert_not_called()

    @patch("api.views.requests.get")
    def test_malformed_credentials_return_forbidden(self, get):
        for extra_data in (
            None,
            {},
            {"other_tokens": 123},
            {"other_tokens": [None, {"scope": None}]},
            {"token_type": "Bearer", "other_tokens": [{"scope": TRANSFER_SCOPE}]},
            {"token_type": "Bearer", "other_tokens": [{
                "scope": TRANSFER_SCOPE, "access_token": "",
            }]},
        ):
            with self.subTest(extra_data=extra_data):
                response, _ = self.request_endpoint(SimpleNamespace(extra_data=extra_data))
                self.assertEqual(response.status_code, 403)
        get.assert_not_called()

    @patch("api.views.requests.get")
    def test_globus_credentials_are_used_for_endpoint_search(self, get):
        get.return_value.json.return_value = {"DATA": [{"id": "dummy-endpoint"}]}
        account = SimpleNamespace(extra_data={
            "token_type": "Bearer",
            "other_tokens": [{
                "scope": "openid " + TRANSFER_SCOPE,
                "access_token": "dummy-transfer-token",
            }],
        })
        response, request = self.request_endpoint(account)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [{"id": "dummy-endpoint"}])
        self.assertEqual(request.session["transfer_token"], "dummy-transfer-token")
        self.assertEqual(get.call_args.kwargs["headers"], {
            "Authorization": "Bearer dummy-transfer-token",
        })

    @patch("api.views.requests.get")
    def test_anonymous_user_keeps_globus_login_redirect(self, get):
        request = self.factory.get("/api/endpoints/")
        request.session = {}
        response = EndpointSearchView.as_view({"get": "list"})(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/login/globus")
        get.assert_not_called()
