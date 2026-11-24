from django.conf import settings
from django.http import HttpResponse
from django.urls import reverse


def pause_cardy(get_response):
    def middleware(request):
        if settings.CARDY_MAINTENANCE_MODE and request.path_info != reverse("health"):
            return HttpResponse("Cardy is undergoing maintenance. Please try again shortly.",
                                status=503, headers={"Cache-Control": "no-store"})
        return get_response(request)
    return middleware
