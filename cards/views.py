import uuid

from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.db.models import BooleanField, Case, Q, Value, When
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from .security import google_configured, local_test_login_enabled, owner_required
from . import importing, learning
from .duplicates import classify_rows
from .forms import BulkWordActionForm, LearningSettingsForm, StudyActionForm, StudyAnswerForm, WordForm, WordImportForm
from .models import LearningSettings, StudySession, Word, WordImport


@never_cache
@require_http_methods(["GET", "HEAD"])
def health(request):
    try:
        ready = (google_configured() or local_test_login_enabled()) and LearningSettings.objects.filter(pk=1).exists()
        Word.objects.exists()
        StudySession.objects.exists()
        Session.objects.exists()
    except DatabaseError:
        ready = False
    return JsonResponse({"status": "ok" if ready else "unavailable"}, status=200 if ready else 503)


@owner_required
@require_http_methods(["GET"])
def home(request):
    now = timezone.now()
    deck = Word.objects.all()
    active = deck.filter(archived_at__isnull=True)
    pending = StudySession.objects.filter(completed_at__isnull=True).values_list("queue", flat=True).first() or []
    ready = Q(archived_at__isnull=True) & (Q(due_at__lte=now) | Q(pk__in=pending))
    progress = request.GET.get("progress", "all")
    filters = {
        "all": Q(),
        "to-learn": ready,
        "practiced": Q(archived_at__isnull=True) & ~ready,
        "learned": Q(archived_at__isnull=False),
    }
    if progress not in filters:
        progress = "all"
    query = request.GET.get("q", "").strip()[:300]
    words = deck.filter(filters[progress])
    if query:
        words = words.filter(Q(chunk__icontains=query) | Q(meaning__icontains=query) | Q(example__icontains=query))
    words = words.annotate(ready_to_practice=Case(
        When(ready, then=Value(True)), default=Value(False), output_field=BooleanField(),
    ))
    ready_count = active.filter(ready).count()
    return render(request, "cards/home.html", {
        "words": words,
        "word_count": words.count(),
        "total_count": deck.count(),
        "to_learn_count": ready_count,
        "practiced_count": active.exclude(ready).count(),
        "learned_count": deck.filter(archived_at__isnull=False).count(),
        "due_count": ready_count,
        "progress": progress,
        "query": query,
        "cards_open": bool(query or progress != "all" or request.GET.get("cards") == "open"),
        "now": now,
        "has_session": StudySession.objects.filter(completed_at__isnull=True).exists(),
    })


@owner_required
@require_http_methods(["GET", "POST"])
def word_add(request):
    form = WordForm(request.POST if request.method == "POST" else None)
    if request.method == "POST":
        with transaction.atomic():
            learning.lock_preferences()
            if form.is_valid():
                form.save()
                return redirect("home")
    return render(request, "cards/word_form.html", {"form": form})


@owner_required
@require_http_methods(["GET", "POST"])
def word_import(request):
    if request.method == "GET":
        return redirect(reverse("home") + "?cards=open")
    form = WordImportForm(request.POST if request.method == "POST" else None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            rows = importing.parse_csv(form.cleaned_data["file"])
        except ValidationError as error:
            form.add_error("file", error)
        else:
            with transaction.atomic():
                learning.lock_preferences()
                WordImport.objects.filter(created_at__lt=timezone.now() - importing.PREVIEW_LIFETIME).delete()
                batch = WordImport.objects.create(rows=rows)
            request.session["word_import_id"] = str(batch.pk)
            if "application/json" in request.headers.get("Accept", ""):
                return JsonResponse({"preview_url": reverse("word-import-preview", kwargs={"pk": batch.pk})})
            return redirect("word-import-preview", pk=batch.pk)
    errors = [error["message"] for field_errors in form.errors.get_json_data().values() for error in field_errors]
    return JsonResponse({"errors": errors}, status=400)


@owner_required
@require_http_methods(["GET", "POST"])
def word_import_preview(request, pk):
    if request.session.get("word_import_id") != str(pk):
        raise Http404
    batch = get_object_or_404(WordImport, pk=pk)
    error = None
    if request.method == "POST":
        try:
            importing.confirm_import(pk)
        except ValidationError as invalid:
            error = " ".join(invalid.messages)
        except WordImport.DoesNotExist:
            raise Http404 from None
        else:
            return redirect("word-import-preview", pk=pk)
    if batch.imported_at:
        return render(request, "cards/word_import_done.html", {"batch": batch})
    expired = batch.created_at < timezone.now() - importing.PREVIEW_LIFETIME
    if expired:
        return render(request, "cards/word_import_preview.html", {"expired": True}, status=410)
    rows = classify_rows(batch.rows)
    add_count = sum(not row["duplicate"] for row in rows)
    return render(request, "cards/word_import_preview.html", {
        "rows": rows, "add_count": add_count, "skipped_count": len(rows) - add_count, "error": error,
    })


@owner_required
@require_http_methods(["GET", "POST"])
def word_edit(request, pk):
    word = get_object_or_404(Word, pk=pk)
    return_to_study = (request.POST if request.method == "POST" else request.GET).get("return_to") == "study"
    form = WordForm(request.POST if request.method == "POST" else None, instance=word)
    if request.method == "POST":
        with transaction.atomic():
            learning.lock_preferences()
            word = get_object_or_404(Word, pk=pk)
            previously_showed_reverse = word.show_reverse_immediately
            form = WordForm(request.POST, instance=word)
            if form.is_valid():
                # Save card content and display preference without changing learning progress.
                word.save(update_fields=["chunk", "meaning", "example", "show_reverse_immediately", "updated_at"])
                if previously_showed_reverse != word.show_reverse_immediately:
                    session = learning.active_session()
                    if session and session.queue and session.queue[0] == word.pk:
                        session.revealed = word.show_reverse_immediately
                        session.response_token = uuid.uuid4()
                        session.save(update_fields=["revealed", "response_token"])
                if return_to_study:
                    return redirect("study")
                return redirect(reverse("home") + "?progress=learned" if word.archived_at else "home")
    return render(request, "cards/word_form.html", {"form": form, "word": word, "return_to_study": return_to_study})


@owner_required
@require_http_methods(["GET", "POST"])
def word_delete(request, pk):
    word = get_object_or_404(Word, pk=pk)
    if request.method == "POST":
        with transaction.atomic():
            learning.lock_preferences()
            word = get_object_or_404(Word, pk=pk)
            destination = reverse("home") + "?progress=learned" if word.archived_at else "home"
            learning.clear_undo_history()
            word.delete()
        return redirect(destination)
    return render(request, "cards/word_delete.html", {"word": word})


@owner_required
@require_http_methods(["GET"])
def archive(request):
    # Keep old bookmarks working without a separate archive screen.
    return redirect(reverse("home") + "?progress=learned")


@owner_required
@require_POST
def word_restore(request, pk):
    with transaction.atomic():
        learning.lock_preferences()
        word = get_object_or_404(Word, pk=pk)
        now = timezone.now()
        # A repeated or stale restore request cannot reset an already-active word.
        restored = Word.objects.filter(pk=word.pk, archived_at__isnull=False).update(
            archived_at=None, stage=0, due_at=now, updated_at=now,
        )
        if restored:
            learning.clear_undo_history()
    return redirect("home")


@owner_required
@require_http_methods(["GET", "POST"])
def word_reset(request, pk):
    word = get_object_or_404(Word, pk=pk)
    if request.method == "POST":
        learning.reset_word(word.pk)
        return redirect(reverse("home") + "?progress=to-learn")
    return render(request, "cards/word_reset.html", {"word": word})


@owner_required
@require_POST
def word_bulk(request):
    if len(request.POST.getlist("word_ids")) > 500:
        return HttpResponseBadRequest("Select no more than 500 words at a time.")
    with transaction.atomic():
        learning.lock_preferences()
        form = BulkWordActionForm(request.POST)
        if not form.is_valid():
            return HttpResponseBadRequest("Choose an action and valid words. Return to My cards and try again.")
        words = list(form.cleaned_data["word_ids"])
        action = form.cleaned_data["action"]
        if request.POST.get("confirmed") == "yes":
            word_ids = [word.pk for word in words]
            if action == "reset":
                learning.reset_words(word_ids)
            else:
                learning.clear_undo_history()
                Word.objects.filter(pk__in=word_ids).delete()
                session = learning.active_session()
                if session:
                    learning.clean_queue(session)
            return redirect(reverse("home") + "?cards=open")
    return render(request, "cards/word_bulk_confirm.html", {"words": words, "action": action})


@owner_required
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


@owner_required
@require_POST
def study_start(request):
    learning.start_or_resume()
    return redirect("study")


@owner_required
@require_http_methods(["GET"])
def study(request):
    session, word = learning.study_state()
    if word:
        return render(request, "cards/study.html", {
            "study_session": session, "word": word, "remaining": len(session.queue),
            "completed": max(0, session.total_words - len(session.queue)),
            "can_undo": learning.can_undo(session),
        })
    active_words = Word.objects.filter(archived_at__isnull=True)
    return render(request, "cards/study_waiting.html", {
        "study_session": session,
        "remaining": 0, "completed": session.total_words,
        "next_word": active_words.order_by("due_at").first(),
        "can_undo": learning.can_undo(session),
    })


@owner_required
@require_http_methods(["GET"])
def study_status(request):
    session, word = learning.study_state()
    return JsonResponse({
        "response_token": str(session.response_token), "remaining": len(session.queue),
        "total": session.total_words, "completed": max(0, session.total_words - len(session.queue)),
        "revealed": session.revealed, "can_undo": learning.can_undo(session),
    })


@owner_required
@require_POST
def study_reveal(request):
    form = StudyActionForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest("Invalid study request. Return to your study session and try again.")
    learning.reveal(form.cleaned_data["response_token"])
    return redirect("study")


@owner_required
@require_POST
def study_answer(request):
    form = StudyAnswerForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest("Invalid study answer. Return to your study session and try again.")
    learning.answer(form.cleaned_data["response_token"], form.cleaned_data["answer"])
    return redirect("study")


@owner_required
@require_POST
def study_undo(request):
    form = StudyActionForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest("Invalid undo request. Return to your study session and try again.")
    learning.undo(form.cleaned_data["response_token"])
    return redirect("study")
