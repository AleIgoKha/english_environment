from django.urls import path

from . import auth_views, views

urlpatterns = [
    path("health/", views.health, name="health"),
    path("", views.home, name="home"),
    path("login/", auth_views.login, name="login"),
    path("auth/google/", auth_views.google_login, name="google-login"),
    path("auth/google/callback/", auth_views.google_callback, name="google-callback"),
    path("auth/local-test/", auth_views.local_test_login, name="local-test-login"),
    path("logout/", auth_views.logout, name="logout"),
    path("words/add/", views.word_add, name="word-add"),
    path("words/import/", views.word_import, name="word-import"),
    path("words/import/<uuid:pk>/", views.word_import_preview, name="word-import-preview"),
    path("words/bulk/", views.word_bulk, name="word-bulk"),
    path("words/<int:pk>/edit/", views.word_edit, name="word-edit"),
    path("words/<int:pk>/delete/", views.word_delete, name="word-delete"),
    path("words/<int:pk>/restore/", views.word_restore, name="word-restore"),
    path("words/<int:pk>/reset/", views.word_reset, name="word-reset"),
    path("archive/", views.archive, name="archive"),
    path("settings/", views.learning_settings, name="learning-settings"),
    path("settings/saved/", views.learning_settings, name="learning-settings-saved"),
    path("study/start/", views.study_start, name="study-start"),
    path("study/", views.study, name="study"),
    path("study/reveal/", views.study_reveal, name="study-reveal"),
    path("study/answer/", views.study_answer, name="study-answer"),
    path("study/undo/", views.study_undo, name="study-undo"),
    path("study/status/", views.study_status, name="study-status"),
]
