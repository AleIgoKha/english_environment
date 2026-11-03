from django.urls import path

from . import views

urlpatterns = [
    path("health/", views.health, name="health"),
    path("", views.home, name="home"),
    path("login/", views.login, name="login"),
    path("logout/", views.logout, name="logout"),
    path("words/add/", views.word_add, name="word-add"),
    path("words/<int:pk>/edit/", views.word_edit, name="word-edit"),
    path("words/<int:pk>/delete/", views.word_delete, name="word-delete"),
    path("words/<int:pk>/restore/", views.word_restore, name="word-restore"),
    path("archive/", views.archive, name="archive"),
    path("settings/", views.learning_settings, name="learning-settings"),
    path("settings/saved/", views.learning_settings, name="learning-settings-saved"),
    path("study/start/", views.study_start, name="study-start"),
    path("study/", views.study, name="study"),
    path("study/reveal/", views.study_reveal, name="study-reveal"),
    path("study/answer/", views.study_answer, name="study-answer"),
]
