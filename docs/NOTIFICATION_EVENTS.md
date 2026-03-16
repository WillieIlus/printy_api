# Quote Marketplace — Notification Events

Lightweight in-app notifications for the quote marketplace.

---

## Event Map

| Event | Recipient | Trigger | object_type | object_id |
|-------|-----------|---------|-------------|-----------|
| **quote_request_submitted** | Shop owner | Customer submits draft | quote_request | qr.id |
| **shop_quote_sent** | Customer | Shop sends quote | quote_request | qr.id |
| **shop_quote_revised** | Customer | Shop revises quote | shop_quote | sq.id |
| **shop_quote_accepted** | Shop owner | Customer accepts quote | shop_quote | sq.id |
| **request_declined** | Customer | Shop declines request | quote_request | qr.id |
| **quote_request_cancelled** | Shop owner | Customer cancels request | quote_request | qr.id |
| **job_status_updated** | Customer | Shop updates job status | production_order | job.id |

---

## API

| Method | Endpoint | Action |
|--------|----------|--------|
| GET | `/api/me/notifications/` | List notifications (newest first) |
| GET | `/api/me/notifications/{id}/` | Retrieve one |
| POST | `/api/me/notifications/{id}/mark-read/` | Mark as read |
| POST | `/api/me/notifications/mark-all-read/` | Mark all as read |

---

## Model

- **user** — recipient (who gets the notification)
- **actor** — who triggered the event (optional)
- **notification_type** — event type
- **object_type** — e.g. quote_request, shop_quote, production_order
- **object_id** — PK of reference object
- **message** — human-readable message
- **read_at** — when marked read (null = unread)

---

## Service

```python
from notifications.services import notify

notify(
    recipient=user,
    notification_type=Notification.QUOTE_REQUEST_SUBMITTED,
    message="New quote request #123 from John",
    object_type="quote_request",
    object_id=123,
    actor=request.user,
)
```
