"""
URL configuration for lms project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include

from courses.admin import get_admin_custom_urls
from courses import views as course_views

admin_urlpatterns = get_admin_custom_urls() + admin.site.get_urls()

urlpatterns = [
    path("admin/", include((admin_urlpatterns, "admin"), namespace="admin")),
    path("", include("courses.urls")),
    path("accounts/register/", course_views.register, name="register"),
    path("accounts/register/success/", course_views.register_success, name="register_success"),
    path("accounts/", include("django.contrib.auth.urls")),
]
