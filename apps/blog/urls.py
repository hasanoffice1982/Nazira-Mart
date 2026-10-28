from django.urls import path

from . import views

app_name = "blog"
urlpatterns = [
    path("blog/", views.PostListView.as_view(), name="index"),
    path("blog/category/<str:slug>/", views.PostListView.as_view(), name="category"),
    path("blog/tag/<str:slug>/", views.PostListView.as_view(), name="tag"),
    path("blog/<str:slug>/", views.PostDetailView.as_view(), name="post"),
]
