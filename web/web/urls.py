from django.conf.urls import patterns, include, url

# Enable admin.
from django.contrib import admin
admin.autodiscover()

urlpatterns = patterns('',
    url(r'^admin/', include(admin.site.urls)),
    url(r'', include('cses.urls'))
)
