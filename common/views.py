from django.http import JsonResponse


def api_not_found(request, exception):
    if request.path.startswith("/api/"):
        return JsonResponse(
            {
                "code": "NOT_FOUND",
                "message": "The requested API resource was not found.",
                "reason": "Check the request path and HTTP method.",
                "field_errors": {},
                "suggestions": [],
            },
            status=404,
        )
    return JsonResponse({"detail": "Not found."}, status=404)


def api_server_error(request):
    if request.path.startswith("/api/"):
        return JsonResponse(
            {
                "code": "SERVER_ERROR",
                "message": "An unexpected server error occurred.",
                "reason": "Please try again later.",
                "field_errors": {},
                "suggestions": [],
            },
            status=500,
        )
    return JsonResponse({"detail": "Server error."}, status=500)
