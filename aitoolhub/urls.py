"""
URL configuration for aitoolhub project.
"""

from django.contrib import admin
from django.urls import path
from tools import views

urlpatterns = [
    # FIRST PAGE = LOGIN
    path("", views.login, name="login"),

    # ADMIN
    path("admin/", admin.site.urls),

    # HOME / AI TOOL HUB PAGE
    path("home/", views.home, name="home"),

    # LOGIN PAGE
    path("login/", views.login, name="login"),

    # SIGN UP
    path("signup/", views.signup, name="signup"),

    path("profile/", views.profile, name="profile"),
    path("logout/", views.logout, name="logout"),

    # AI TOOLS
    path("chatbot/", views.chatbot, name="chatbot"),
    path("writer/", views.writer, name="writer"),
    path("translator/", views.translator, name="translator"),
    path("summarizer/", views.summarizer, name="summarizer"),
    path("coding/", views.coding, name="coding"),
    path("study/", views.study, name="study"),
    path("resume/", views.resume, name="resume"),
    path("resume-guide/", views.resume_guide, name="resume_guide"),
    path("interview/", views.interview, name="interview"),
]