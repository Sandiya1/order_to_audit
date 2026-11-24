from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseForbidden

R = None  # filled lazily to avoid import cycles


def role_required(*roles):
    """Allow only the given roles (superusers always pass). Anonymous -> login."""
    def deco(view):
        @wraps(view)
        def wrapper(request, *a, **kw):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if request.user.is_superuser or request.user.role in roles:
                return view(request, *a, **kw)
            return HttpResponseForbidden("Your role does not have access to this page.")
        return wrapper
    return deco


def can(user, *roles):
    return user.is_authenticated and (user.is_superuser or user.role in roles)
