import health_check.urls
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

urlpatterns = [path("health/", include(health_check.urls))]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)