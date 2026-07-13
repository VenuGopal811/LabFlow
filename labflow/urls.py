"""URL configuration for LabFlow."""
from django.contrib import admin
from django.urls import path, include

# Customize admin site header
admin.site.site_header = 'LabFlow — Lab Management'
admin.site.site_title = 'LabFlow'
admin.site.index_title = 'Dashboard'

urlpatterns = [
    path('admin/', admin.site.urls),
    path('report/', include('reports.urls')),
    path('', include('core.urls')), # Front-end views mapping
]
