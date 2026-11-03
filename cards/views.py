from django.contrib.sessions.models import Session
from django.db import DatabaseError, transaction
from django.http import HttpResponseBadRequest, JsonResponse
from django.middleware.csrf import rotate_token
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST

from .security import is_authenticated, pin_configured, pin_required, pin_version, verify_pin
from . import learning
from .forms import LearningSettingsForm, StudyActionForm, StudyAnswerForm, WordForm
from .models import LearningSettings, LoginGuard, StudySession, Word


@never_cache
@require_http_methods(["GET", "HEAD"])
def health(request):
    try:
        ready = pin_configured() and LearningSettings.objects.filter(pk=1).exists() and LoginGuard.objects.filter(pk=1).exists()
        Word.objects.exists()
        StudySession.objects.exists()
        Session.objects.exists()
    except DatabaseError:
        ready = False
    return JsonResponse({"status": "ok" if ready else "unavailable"}, status=200 if ready else 503)


@sensitive_post_parameters("pin")
@never_cache
@require_http_methods(["GET", "POST"])
def login(request):
    if is_authenticated(request):
        return redirect("home")
    if not pin_configured():
        return render(request, "cards/login.html", {"unconfigured": True}, status=503)
    context = {}
    status = 200
    if request.method == "POST":
        accepted, blocked_until = verify_pin(request.POST.get("pin", ""))
        if accepted:
            request.session.flush()
            request.session["pin_version"] = pin_version()
            rotate_token(request)
            return redirect("home")
        if blocked_until:
            seconds = max(1, int((blocked_until - timezone.now()).total_seconds()) + 1)
            context["error"] = "Too many attempts. Please try again in 15 minutes."
            status = 429
        else:
            context["error"] = "That PIN wasn't correct. Please try again."
            status = 400
    response = render(request, "cards/login.html", context, status=status)
    if status == 429:
        response["Retry-After"] = str(seconds)
    return response


@pin_required
@require_http_methods(["GET"])
def home(request):
    now = timezone.now()
    words = Word.objects.filter(archived_at__isnull=True)
    return render(request, "cards/home.html", {
        "words": words,
        "word_count": words.count(),
        "due_count": words.filter(due_at__lte=now).count(),
        "now": now,
        "has_session": StudySession.objects.filter(completed_at__isnull=True).exists(),
    })


@pin_required
@require_http_methods(["GET", "POST"])
def word_add(request):
    form = WordForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("home")
    return render(request, "cards/word_form.html", {"form": form})


@pin_required
@require_http_methods(["GET", "POST"])
def word_edit(request, pk):
    word = get_object_or_404(Word, pk=pk)
    form = WordForm(request.POST if request.method == "POST" else None, instance=word)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            learning.lock_preferences()
            word = get_object_or_404(Word, pk=pk)
            for field in ["chunk", "meaning", "example"]:
                setattr(word, field, form.cleaned_data[field])
            word.save(update_fields=["chunk", "meaning", "example", "updated_at"])
        return redirect("archive" if word.archived_at else "home")
    return render(request, "cards/word_form.html", {"form": form, "word": word})


@pin_required
@require_http_methods(["GET", "POST"])
def word_delete(request, pk):
    word = get_object_or_404(Word, pk=pk)
    if request.method == "POST":
        with transaction.atomic():
            learning.lock_preferences()
            word = get_object_or_404(Word, pk=pk)
            destination = "archive" if word.archived_at else "home"
            word.delete()
        return redirect(destination)
    return render(request, "cards/word_delete.html", {"word": word})


@pin_required
@require_http_methods(["GET"])
def archive(request):
    words = Word.objects.filter(archived_at__isnull=False).order_by("-archived_at", "-pk")
    return render(request, "cards/archive.html", {"words": words, "word_count": words.count()})


@pin_required
@require_POST
def word_restore(request, pk):
    with transaction.atomic():
        learning.lock_preferences()
        word = get_object_or_404(Word, pk=pk)
        now = timezone.now()
        # A repeated or stale restore request cannot reset an already-active word.
        Word.objects.filter(pk=word.pk, archived_at__isnull=False).update(
            archived_at=None, stage=0, due_at=now, updated_at=now,
        )
    return redirect("home")


@pin_required
@require_http_methods(["GET", "POST"])
def learning_settings(request):
    preferences = LearningSettings.objects.get(pk=1)
    form = LearningSettingsForm(request.POST if request.method == "POST" else None, instance=preferences)
    if request.method == "POST" and form.is_valid():
        learning.change_target(form.cleaned_data["review_target"])
        return redirect("learning-settings-saved")
    return render(request, "cards/settings.html", {
        "form": form,
        "saved": request.method == "GET" and request.resolver_match.url_name == "learning-settings-saved",
    })


@pin_required
@require_POST
def study_start(request):
    learning.start_or_resume()
    return redirect("study")


@pin_required
@require_http_methods(["GET"])
def study(request):
    session, word = learning.study_state()
    if session:
        return render(request, "cards/study.html", {"study_session": session, "word": word, "remaining": len(session.queue)})
    last_session = StudySession.objects.order_by("-pk").first()
    active_words = Word.objects.filter(archived_at__isnull=True)
    return render(request, "cards/study_done.html", {
        "last_session": last_session,
        "due_count": active_words.filter(due_at__lte=timezone.now()).count(),
        "next_word": active_words.order_by("due_at").first(),
    })


@pin_required
@require_POST
def study_reveal(request):
    form = StudyActionForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest("Invalid study request. Return to your study session and try again.")
    learning.reveal(form.cleaned_data["response_token"])
    return redirect("study")


@pin_required
@require_POST
def study_answer(request):
    form = StudyAnswerForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest("Invalid study answer. Return to your study session and try again.")
    learning.answer(form.cleaned_data["response_token"], form.cleaned_data["answer"])
    return redirect("study")


@require_POST
def logout(request):
    request.session.flush()
    return redirect("login")
