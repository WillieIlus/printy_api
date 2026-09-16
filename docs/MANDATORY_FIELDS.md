# Mandatory Fields — Printy API

Fields marked as **required** in API requests. Frontend forms should show asterisk (*) and validate before submit.

---

## Auth

| Endpoint | Field | Required |
|----------|-------|----------|
| POST `/api/auth/token/` | email | ✓ |
| POST `/api/auth/token/` | password | ✓ |
| POST `/api/auth/register/` | email | ✓ |
| POST `/api/auth/register/` | password | ✓ |
| POST `/api/auth/register/` | first_name | ✓ |
| POST `/api/auth/register/` | last_name | ✓ |

---

## Shop Setup (Seller)

### Machines (`POST /api/shops/{slug}/machines/`)

| Field | Required |
|-------|----------|
| name | ✓ |
| machine_type | ✓ |
| max_width_mm | ✓ |
| max_height_mm | ✓ |

### Papers (`POST /api/shops/{slug}/papers/`)

| Field | Required |
|-------|----------|
| sheet_size | ✓ |
| gsm | ✓ |
| paper_type | ✓ |
| buying_price | ✓ |
| selling_price | ✓ |

### Finishing Rates (`POST /api/shops/{slug}/finishing-rates/`)

| Field | Required |
|-------|----------|
| name | ✓ |
| charge_unit | ✓ |
| price | ✓ |

### Materials (`POST /api/shops/{slug}/materials/`)

| Field | Required |
|-------|----------|
| material_type | ✓ |
| unit | ✓ |
| buying_price | ✓ |
| selling_price | ✓ |

### Products (`POST /api/shops/{slug}/products/`)

| Field | Required |
|-------|----------|
| name | ✓ |
| pricing_mode | ✓ |
| default_finished_width_mm | ✓ |
| default_finished_height_mm | ✓ |

---

## Quote Draft Items

### PRODUCT items

| Field | Required |
|-------|----------|
| product | ✓ |
| quantity | ✓ |

### CUSTOM items

| Field | Required |
|-------|----------|
| title or spec_text | ✓ (one of) |
| quantity | ✓ |
| chosen_width_mm | ✓ |
| chosen_height_mm | ✓ |

---

## Shop Creation

| Field | Required |
|-------|----------|
| name | ✓ |
| business_email | ✓ |
| address_line | ✓ |
| city | ✓ |
| state | ✓ |
| country | ✓ |
| zip_code | ✓ |
