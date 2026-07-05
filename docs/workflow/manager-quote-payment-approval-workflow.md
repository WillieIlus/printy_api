# Manager Quote, Payment, Payout, Turnaround, and Proof Workflow

## 1. Purpose

The manager or middleman is the main engine of Printy. The workflow must let the manager move a client job from an initial quote request through pricing, payment, production dispatch, proof approval, and production go-ahead without losing role boundaries or leaving unclear next actions.

The manager must be able to:

- Create custom quotes.
- Source production pricing.
- Select or confirm shops.
- Send a quote to the client.
- Continue after client payment.
- Dispatch work to the shop.
- Handle proof and artwork approval.
- Give the printer production go-ahead.
- Know who acts next at each state.

## 2. Completed Phase 1: Manager Custom Quote Context

Phase 1 fixed the manager custom quote production pricing error:

```text
calculator_context and intent are required
```

Cause: the manager production pricing request called the partner production-matches endpoint without the backend routing fields required for direct production sourcing.

Fix: the frontend now sends the existing backend-supported routing fields on the partner production-matches request:

```ts
calculator_context: 'manager_dashboard',
intent: 'source_production',
```

This was a frontend-only fix. No backend enum was added, no backend intent was added, and the homepage calculator flow was untouched.

Completed commit:

```text
49e7407b fix: send manager production quote context
```

Validation passed:

```text
yarn typecheck
yarn build
```

## 3. Remaining Phase 2: M-Pesa Failure Visibility

Problem: client cancellation, rejection, timeout, or failed M-Pesa STK push must not remain visually stuck in a pending state. The client and manager need clear payment state feedback so the next action is obvious.

Client payment states to show:

- Pending.
- Paid.
- Cancelled.
- Expired or timed out.
- Failed.

Manager payment states to show:

- Waiting for payment.
- Payment confirmed.
- Client cancelled payment.
- Payment expired or timed out.
- Payment failed.

Planned backend work:

- Return latest STK response fields from the payment query/detail endpoint:
  - `stk_status`
  - `stk_response_code`
  - `stk_response_description`
  - `stk_customer_message`
  - `stk_checkout_request_id`
  - `stk_merchant_request_id`
- Map Daraja code `1032` to cancelled.
- Map Daraja code `1037` to expired/timeout.
- Preserve successful payment behavior.
- Do not expose cost, margin, commission, or payout fields from payment endpoints.

Planned frontend work:

- Stop polling on paid, failed, cancelled, or expired.
- Show retry action after failed, cancelled, or expired STK.
- Show plain-language copy for the client and manager.
- Allow client retries only.
- Manager sees a normalized payment label and a subordinate Daraja description where useful.

## 4. Remaining Phase 3: Payout and Turnaround MVP

Recommended payout model:

- Client pays Printy.
- Printy pays the shop later.
- MVP payout may be manual.
- Deposit trigger recommendation: `in_production`.
- Balance trigger recommendation: `completed`.

Role visibility:

Client sees:

- Quote status.
- Payment status.
- Job progress.
- No payout, production cost, Printy fee, margin, or commission fields.

Manager sees:

- Client payment status.
- Payment confirmed amount.
- Shop payout amount.
- Shop payout status.
- Allowed manager earnings only where already allowed.
- No wrong-role production cost or Printy fee exposure.

Printshop sees:

- Production payout amount.
- Production payout status.
- Production job progress.
- No client total, manager margin, Printy fee, or broker payout.

Admin sees:

- Full economics.
- Payout controls.
- Payment controls.
- Audit fields.

Turnaround:

- If turnaround is missing, the manager requests it from the shop.
- The shop provides a structured estimate and optional note.
- The manager uses that turnaround in the client quote or quote revision.

Recommended fields:

- `turnaround_min_hours`
- `turnaround_max_hours`
- `turnaround_note`
- `turnaround_status`

The turnaround response should be structured enough for filtering, deadlines, and display, while still allowing a short shop note for operational context.

## 5. Remaining Phase 4: Proof / Artwork Approval

Required manager actions:

- Approve for production.
- Send to client for approval.
- Request changes from shop.

Flow A: manager approves directly.

- Shop uploads proof.
- Manager reviews proof.
- Manager approves directly.
- State becomes `approved_for_production`.
- Printer sees production go-ahead.

Flow B: manager sends proof to client.

- Shop uploads proof.
- Manager reviews proof.
- Manager sends proof to client.
- Client approves or requests changes.
- If approved, printer gets production go-ahead.
- If rejected, manager and printer see changes requested.

The proof workflow must not dead-end after manager approval. A proof approval must produce a visible next state for the printer.

## 6. Track Job Rule

`/track-job/[id]` has already been dealt with. Do not redesign it in these workflow phases.

Only regression-check that new payment, proof, and turnaround states still display correctly on the track-job page.

## 7. Acceptance Criteria

- Manager custom quote production pricing works.
- M-Pesa failed, cancelled, and expired states are visible.
- Client can retry failed, cancelled, or expired STK.
- Manager sees payment failure state.
- Shop payout status is role-safe.
- Turnaround request/provide flow works.
- Proof approval has no dead end.
- Printer only proceeds after manager or client approval.
- No wrong-role economics leak.
- Homepage calculator still works.
- Track-job page still works.

## 8. Manual QA Checklist

1. Manager creates custom quote.
2. Manager sources production pricing.
3. Manager sends quote to client.
4. Client accepts quote.
5. Client cancels STK.
6. Client sees cancellation and retry.
7. Manager sees payment cancellation.
8. Client retries and pays.
9. Manager dispatches to shop.
10. Shop provides turnaround if missing.
11. Shop uploads proof.
12. Manager approves directly.
13. Printer sees go-ahead.
14. Manager sends proof to client.
15. Client approves.
16. Printer sees go-ahead.
17. Client rejects.
18. Manager and printer see changes requested.
19. Track-job page still displays correctly.

