from django.contrib import admin
from django.urls import path,include

urlpatterns = [
    path("admin/", admin.site.urls),#デフォルトで管理画面のルーティングであるpathが記載されている
    path("",include("paper_abstract_app.urls"))
]
