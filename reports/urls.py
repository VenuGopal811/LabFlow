from django.urls import path
from . import views

app_name = 'reports'

urlpatterns = [
    path('<str:token>/', views.download_report, name='download_report'),
]
