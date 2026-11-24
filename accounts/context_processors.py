from django.conf import settings


def company(request):
    return {"COMPANY": settings.COMPANY}
