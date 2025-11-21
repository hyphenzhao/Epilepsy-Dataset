from api.urls import router
from django.contrib.auth import views as auth_views
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from epilepsy_portal.views import (
    landing_page, 
    CustomSearch,
    TransferView
)

from django.urls import path, include
from globus_portal_framework.urls import register_custom_index

register_custom_index('osn_index', ['terrafusion'])

urlpatterns = [
    # Provides the basic search portal
    path("api-auth/", include("rest_framework.urls")),
    path("api/", include(router.urls)),
    path("admin/", admin.site.urls),

    path("", landing_page, name="landing-page"),
    
    path("<osn_index:index>", CustomSearch.as_view(), name="search"),
    path("transfer/", TransferView.as_view(), name="transfer"),
    
    path("", include("globus_portal_framework.urls")),
    path("", include("social_django.urls", namespace="social")),

    # Our epilepsy app:
    path("epilepsy/", include("epilepsy.urls")),

    # ...
    path("accounts/login/", auth_views.LoginView.as_view(
        template_name="registration/login.html"
    ), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(next_page="login"), name="logout"),


]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)