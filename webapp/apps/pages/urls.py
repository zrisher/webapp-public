from django.conf.urls import patterns, include, url

from .views import homepage, aboutpage, newspage, newsdetailpage


urlpatterns = patterns('',
    url(r'^$', homepage, name='home'),
    url(r'^about/$', aboutpage, name='about'),
    url(r'^news/$', newspage, name='news'),
    url(r'^news/news-detail$', newsdetailpage, name='newsdetail'),
)
