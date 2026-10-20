from django.urls import path

from . import views

app_name = "dealers"
urlpatterns = [path("webhook/<str:token>/", views.webhook, name="webhook")]
