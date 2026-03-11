# API URL Audit — Frontend ↔ Backend

## Base URL
- **Frontend apiBase**: `NUXT_PUBLIC_API_BASE_URL` + `/api` (e.g. `http://localhost:8000/api`)
- **Backend**: `config/urls.py` mounts at `/api/`

## Auth (accounts app — `/api/auth/`)
| Frontend path | Backend path | Method | Notes |
|---------------|--------------|--------|-------|
| `auth/token/` | `/api/auth/token/` | POST | Body: `{ email, password }` |
| `auth/token/refresh/` | `/api/auth/token/refresh/` | POST | Body: `{ refresh }` |
| `auth/me/` | `/api/auth/me/` | GET, PATCH | Current user |
| `auth/register/` | `/api/auth/register/` | POST | Body: `{ email, password, name }` |

## Demo / Calculator
| Frontend path | Backend path | Notes |
|---------------|--------------|-------|
| `shops/{slug}/rate-card-for-calculator/` | `/api/shops/{slug}/rate-card-for-calculator/` | **Primary** — real shop data |
| `demo/rate-card/` | `/api/demo/rate-card/` | **Deprecated** — may 404 if demo app not deployed |
| `demo/templates/` | `/api/demo/templates/` | Demo products |
| `demo/quote/` | `/api/demo/quote/` | Demo quote calculation |

## Public shops
| Frontend path | Backend path |
|---------------|--------------|
| `public/shops/` | `/api/public/shops/` |
| `public/shops/{slug}/catalog/` | `/api/public/shops/{slug}/catalog/` |
| `public/shops/{slug}/custom-options/` | `/api/public/shops/{slug}/custom-options/` |

## Shops (seller)
| Frontend path | Backend path |
|---------------|--------------|
| `shops/` | `/api/shops/` |
| `shops/{slug}/` | `/api/shops/{slug}/` |
| `shops/{slug}/machines/` | `/api/shops/{slug}/machines/` |
| `shops/{slug}/papers/` | `/api/shops/{slug}/papers/` |
| `shops/{slug}/materials/` | `/api/shops/{slug}/materials/` |
| `shops/{slug}/finishing-rates/` | `/api/shops/{slug}/finishing-rates/` |
| `shops/{slug}/products/` | `/api/shops/{slug}/products/` |
| `shops/{slug}/rate-card/` | `/api/shops/{slug}/rate-card/` |
| `shops/{slug}/gallery/products/{slug}/calculate-price/` | `/api/shops/{slug}/gallery/products/{slug}/calculate-price/` |

## Quote drafts (tweak & add)
| Frontend path | Backend path |
|---------------|--------------|
| `quote-drafts/active/?shop={slug}` | `/api/quote-drafts/active/?shop={slug}` |
| `quote-drafts/{id}/items/` | `/api/quote-drafts/{id}/items/` |
| `quote-drafts/{id}/items/{itemId}/` | `/api/quote-drafts/{id}/items/{itemId}/` |
| `quote-drafts/{id}/tweak-and-add/` | `/api/quote-drafts/{id}/tweak-and-add/` |
| `tweaked-items/{id}/` | `/api/tweaked-items/{id}/` |

## Profiles
| Frontend path | Backend path |
|---------------|--------------|
| `profiles/me/` | `/api/profiles/me/` |
| `profiles/{id}/social-links/` | `/api/profiles/{id}/social-links/` |

## Setup
| Frontend path | Backend path |
|---------------|--------------|
| `setup/status/` | `/api/setup/status/` |

## Auth 400 on token
Common causes:
1. **Body format**: Must be JSON `{ "email": "...", "password": "..." }`
2. **Content-Type**: `application/json`
3. **Invalid credentials**: Email not found or wrong password
4. **CORS**: Ensure backend allows frontend origin
