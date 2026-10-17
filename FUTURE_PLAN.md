# NarizMart: future plan

The MVP is built (see `DEPLOY.md` for running it). This file lists what is deliberately **not** built yet, in the order I would do it, with the scope of each item. Estimates are for one developer and include tests.

## Before launch (not "future", do these first)
- Off-server backups for the database dumps and `media/` (rclone to Cloudflare R2 or another server), plus one restore test.
- Uptime monitor on `/healthz` that emails you.
- One full test order: place it with the 5,000 / 10% promotion, check every email, move it through all statuses, cancel one.
- Real products, real contact email in Site settings, read the Privacy and Terms pages once.

## Phase 1: take money online (about 3 to 5 days)
**SSLCommerz** (cards, bKash, Nagad, Rocket and banking through one gateway).
- Checkout gets a payment choice: Cash on delivery or Pay online. COD stays the default.
- New `Payment` model: order, gateway transaction id, amount, status (initiated, paid, failed, cancelled, refunded), raw gateway response.
- Flow: create order as "awaiting payment" with stock held, redirect to SSLCommerz, return to success / fail / cancel pages.
- Trust nothing from the browser: confirm every payment server-side with the gateway's validation API and compare amount and currency with the order.
- Idempotent IPN (webhook): the same notification twice must not mark an order paid twice or change stock twice.
- Unpaid online orders expire after about 30 minutes and release their stock.
- Needs from you: an SSLCommerz merchant account (sandbox first, then live), store id and password as server secrets. Never in the repository.
- Risks: gateway sandbox behaves differently from live; build a "simulate gateway" test double so tests do not call the real service.

## Phase 2: refunds and invoices (about 2 to 3 days)
**Refunds**
- Admin action on an order: full or partial refund with a reason. Records a `Refund` row, restores stock if wanted, emails the customer.
- COD orders: record a manual refund (cash or bKash sent by you). Online orders: call the gateway refund API, track pending / done.
- Payment status becomes refunded or partially refunded; order totals are never rewritten.

**Invoice PDFs**
- Customer downloads a PDF from the order page; staff can print from admin.
- Uses the billing snapshot already stored on each order. Business name, address and tax / BIN number come from Site settings.
- Sequential invoice numbers (separate from the order number), generated once and never changed.

## Phase 3: trust and conversion (about 3 to 4 days)
**Reviews and ratings**
- Only customers with a delivered order for that product can review (verified purchase). One review per product per customer, editable for 30 days.
- Star rating, text, optional photo (reuses the photo validation and shrinking).
- Staff moderation: new reviews wait for approval; admin can hide or delete.
- Product page shows average and count; product card rating becomes real instead of the stored number. Adds `Review` structured data for Google.

**Product structured data (JSON-LD)**: price, stock, rating, images, so products can appear with rich results in search. About half a day, can ship before reviews.

## Phase 4: catalog structure (about 2 to 3 days)
**Brands**: `Brand` model, brand field on products, brand filter and brand pages, `brand` column in the CSV import.
**Subcategories**: category tree (parent link), breadcrumbs, header menu shows top level with a dropdown for children, CSV import accepts `Parent > Child`.
- Risk: the shop filter, sitemap and the home category tiles all assume one level; keep top-level categories working exactly as now while adding children.

## Phase 5: running the business (about 3 to 4 days)
**Sales dashboard** (admin home or a "Reports" page)
- Today / 7 days / 30 days: orders, revenue, average order value, discount given, delivery charges.
- Best sellers, low stock list (links to products), orders waiting for action, conversion by coupon.
- CSV export of orders for your accountant.
- Built on the existing order data; no new tracking needed.

**Staff roles**: Catalog manager, Order manager, Support (Django groups with fixed permissions), so staff only see what they need.

## Phase 6: compliance and polish
**Cookie consent banner** (about 1 day): needed if you advertise to visitors in the EU / UK or want to be cautious. Meta Pixel and Google tag load only after "Accept"; "Reject" keeps them off. The tracking script already reads its settings from one place, so this is a small change.
- Smaller items: Redis for shared rate-limit counters (the deploy log warns about this), a "recently viewed" strip, abandoned-cart email, product Q&A, order SMS (needs an SMS provider), Bangla language option.

## Suggested order
1. Pre-launch checklist above
2. Phase 1 (payments), the biggest revenue unlock
3. Phase 2 (refunds and invoices), needed as soon as online payment exists
4. Phase 3 (JSON-LD first, then reviews)
5. Phase 5 (dashboard), once there are orders to look at
6. Phases 4 and 6 as the catalog and audience grow

## Rules for every phase
- Money is always `Decimal`; order and invoice rows keep a snapshot, never a live link to prices.
- Every new feature ships with tests, passes CI on PostgreSQL, and deploys through the normal pipeline (database backup, migrate, health check, auto-rollback).
- Secrets (gateway keys, SMS keys) live in the server's `.env` or GitHub secrets, never in the repository.
