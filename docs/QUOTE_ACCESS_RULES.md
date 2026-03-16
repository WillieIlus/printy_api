# Quote Marketplace — Access Rules

Clear permission rules to prevent quote data leaking across shops/customers.

---

## 1. Customers

| Resource | Who Can Access | Restrictions |
|----------|----------------|---------------|
| **Quote requests** | Creator only (`created_by=user`) | List, retrieve, update (draft), submit, accept, cancel |
| **Quote request items** | Creator only | List, create, update, delete (draft only) |
| **Sent quotes** | Via own quote request only | Shops send quotes; customer sees them in quote request detail |
| **Jobs** | Jobs from own accepted quotes | `GET /jobs/?as_customer=1` — read-only |

**Rule:** Customers never see other customers' quote requests or quote responses.

---

## 2. Shops

| Resource | Who Can Access | Restrictions |
|----------|----------------|---------------|
| **Incoming requests** | Shop owner only | `/shops/<slug>/incoming-requests/` — list, retrieve, send-quote, mark-viewed, decline |
| **Sent quotes** | Shop owner only | List, retrieve, revise, create-job |
| **Jobs** | Shop owner only | Full CRUD; `?shop=<slug>` requires ownership |

**Rule:** Shops only see incoming requests for their shop. `?shop=<slug>` is ignored unless user owns that shop (or is staff).

---

## 3. Shared / Jobs

| Role | List | Retrieve | Create | Update | Delete | Processes |
|------|------|----------|--------|--------|--------|------------|
| **Shop owner** | ✓ (own shop) | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Customer** | ✓ (`?as_customer=1`) | ✓ | ✗ | ✗ | ✗ | Read only |
| **Other** | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |

**Rule:** Accepted jobs visible to both shop and customer. Only shop can create/update/delete.

---

## 4. Admin / Staff

| Behavior |
|----------|
| Staff can access any quote request, sent quote, incoming request, or job |
| Staff can use `?shop=<slug>` to access any shop's production data |
| Staff bypass is applied in `IsQuoteRequestBuyer`, `IsQuoteRequestSeller`, `IsShopQuoteOwner`, `IsJobCustomerOrShopOwner` |

---

## 5. Permission Classes

| Class | Use | Object Check |
|-------|-----|--------------|
| `IsQuoteRequestBuyer` | Customer quote requests | `obj.created_by_id == user.id` |
| `IsQuoteRequestItemBuyer` | Quote items | `obj.quote_request.created_by_id == user.id` |
| `IsQuoteRequestSeller` | Incoming requests | `obj.shop.owner_id == user.id` |
| `IsShopQuoteOwner` | Sent quotes | `obj.shop.owner_id == user.id` |
| `IsJobCustomerOrShopOwner` | Jobs | Shop owner OR `shop_quote.quote_request.created_by_id == user.id` |

---

## 6. Fixes Applied

| Gap | Fix |
|-----|-----|
| **`_get_shop_from_request`** | When `?shop=<slug>` provided, verify user owns shop (or is staff). Previously any user could access another shop's jobs. |
| **Quote request items** | Restricted to buyer only. Shops use `/shops/<slug>/incoming-requests/` for their view. |
| **Customer job visibility** | Added `GET /jobs/?as_customer=1` for customers to see jobs from their accepted quotes. |
| **Job write actions** | Customers can only read jobs; create/update/delete/processes restricted to shop owner. |
| **ProductionOrderWriteSerializer** | `_get_shop_from_request` now verifies ownership when creating jobs without `shop_quote`. |
