"""
Normalized API error responses for frontend consumption.
Format: { code, message, reason, field_errors, suggestions }
"""
from rest_framework.views import exception_handler
from rest_framework import status


def api_exception_handler(exc, context):
    """Convert DRF and Django exceptions to normalized error format."""
    response = exception_handler(exc, context)
    if response is None:
        return None

    data = response.data
    if isinstance(data, dict) and "detail" in data and len(data) == 1:
        # Simple DRF error: {"detail": "..."}
        normalized = {
            "code": _status_to_code(response.status_code),
            "message": str(data["detail"]) if not isinstance(data["detail"], list) else data["detail"][0],
            "reason": _reason_for_status(response.status_code),
            "field_errors": {},
            "suggestions": [],
        }
        response.data = normalized
        return response

    if isinstance(data, dict):
        # DRF validation error: {"field": ["error"], ...}
        field_errors = {}
        for k, v in data.items():
            if isinstance(v, list):
                field_errors[k] = v
            else:
                field_errors[k] = [str(v)]

        # Extract first message as main message
        first_msg = ""
        for vals in field_errors.values():
            if vals:
                first_msg = vals[0]
                break

        normalized = {
            "code": "VALIDATION_ERROR" if response.status_code == 400 else _status_to_code(response.status_code),
            "message": first_msg or "Validation failed.",
            "reason": "Please check the fields below.",
            "field_errors": field_errors,
            "suggestions": [],
        }
        if "suggestions" in data:
            normalized["suggestions"] = data["suggestions"]
        if "reason" in data:
            normalized["reason"] = data["reason"]
        response.data = normalized

    return response


def _status_to_code(status_code):
    codes = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        500: "SERVER_ERROR",
    }
    return codes.get(status_code, "ERROR")


def _reason_for_status(status_code):
    reasons = {
        400: "Invalid request.",
        401: "Please sign in to continue.",
        403: "You don't have permission for this action.",
        404: "The requested resource was not found.",
        409: "Conflict with current state.",
        500: "Something went wrong. Please try again.",
    }
    return reasons.get(status_code, "An error occurred.")
