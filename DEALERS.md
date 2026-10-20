# Dealers: products that are not on the dropshipping API

Dropship-supplier products are ordered through the supplier API (see SUPPLIER.md). Products from **your own dealers**
are sent to an automation (n8n or Make) which tells the dealer and brings courier and tracking back.

## In the admin
1. **Dealers → Add dealer.** Name, phone, email. Paste the n8n **Production URL** of the workflow's Webhook node into *Automation webhook URL*. Save: the page then shows the *Tracking address* (give it to n8n) and the signing *secret*.
2. **Products → a product → Who ships it → Dealer.** Or use the CSV column below.
3. Product and order lists show a **Ships from** badge: *Dropship*, *Dealer: <name>* or *Own stock*, with a "who ships it" filter.
4. When you **Confirm** an order, its dealer items are POSTed to that dealer's webhook. An order with items from two dealers sends two messages. See **Dealers → Dealer orders** (also inside the order).
   Failed sends show the error; use the order action **Send to dealers**, or `python manage.py dealer_retry`.

## What n8n receives (POST, JSON)
```json
{
  "event": "order.confirmed",            // or "order.cancelled"
  "reference": "NM-1234-2",              // send this back with the tracking
  "order_number": "NM-1234",
  "placed_at": "2026-10-21T10:15:00+06:00",
  "dealer": {"id": 2, "name": "Rahim Traders", "phone": "017...", "email": "rahim@example.com"},
  "customer": {"name": "...", "phone": "017...", "address": "...", "city": "Dhaka", "delivery_area": "dhaka"},
  "cod_amount": "1899.00",               // cash to collect for the WHOLE order
  "delivery_charge": "60.00",
  "items": [{"sku": "NM-2001", "name": "...", "variant": "Pink / M", "quantity": 1, "unit_price": "849.00"}],
  "callback_url": "https://narizmart.com/dealer/webhook/<token>/"
}
```
Header `X-NarizMart-Signature: sha256=<hex>` is the HMAC-SHA256 of the raw body with the dealer's *secret* (optional to check in n8n).

## What n8n sends back (POST to the tracking address, JSON)
```json
{"reference": "NM-1234-2", "status": "shipped", "courier_name": "Pathao", "tracking_code": "PT123", "tracking_link": "https://..."}
```
`status` can be: processing, shipped, delivered, returned, cancelled. The shop moves the order forward, shows the tracking on the customer's Track order page and in the admin. Use `order_number` instead of `reference` only if the order has one dealer.

## Suggested n8n workflow (per dealer, 2 workflows)
**A. New order → dealer**: Webhook (POST, respond 200) → Google Sheets *Append row* (order number, customer, address, items, COD, reference, empty courier/tracking columns) → WhatsApp / Email node to the dealer ("New order NM-1234, see sheet").
**B. Tracking → shop**: Google Sheets Trigger *on row updated* (courier and tracking columns filled) → IF tracking not empty → HTTP Request POST to the tracking address with the JSON above → write "synced" in a sheet column so it is not sent twice.

Each order costs about 2–3 n8n executions. Self-hosted n8n has no execution limit.

## CSV import: `ships_from` (required for new products)
| ships_from | meaning |
|---|---|
| `dropship` | ordered through the supplier API. `supplier_id` (optional column) is the supplier's product id; if empty the `sku` is used. |
| `own` | your own stock; nothing is sent anywhere |
| a dealer's exact name | shipped by that dealer (add the dealer under **Dealers** first) |

Existing products may leave it empty (the source stays as it is). A supplier id can belong to one product only.
When **Import from supplier** runs, products linked from a CSV with the numeric id are matched by the API's `system_id` and their id is corrected to the real `product_id`.

## One-time: link the products you already uploaded as dropship
```bash
python manage.py link_dropship --dry-run                 # lists what would be linked (products whose SKU is all digits)
python manage.py link_dropship --exclude 7191,7192       # do it, leaving some SKUs out
```

## The two n8n workflows (already created in your n8n Cloud, as drafts)
| Workflow | n8n link | What it does |
|---|---|---|
| NarizMart: new dealer order to sheet + dealer email | https://instance-447316956.n8nclouds.host/workflow/yshQzQhK74xHZzYi | Webhook `narizmart-dealer-order` receives the order, adds a row to the dealer sheet (or marks it CANCELLED), emails the dealer if the dealer has an email |
| NarizMart: dealer sheet tracking to shop | https://instance-447316956.n8nclouds.host/workflow/ynhHVsUsFlxinIgw | Every ~2 minutes looks for rows where the dealer set Status or tracking, posts them to the shop's tracking address, then marks that status as sent |

Set up once per dealer (duplicate both workflows for another dealer and use a new webhook path, e.g. `narizmart-dealer-order-2`):
1. Create a Google Sheet, rename the first tab **Orders**, and paste this header row into row 1:
   `Reference, Order Number, Placed At, Customer, Phone, Address, Area, Items, COD Amount, Delivery Charge, Callback URL, Status, Courier, Tracking Code, Tracking Link, Synced Status, Updated`
   Share it with the dealer (they edit only Status, Courier, Tracking Code, Tracking Link).
2. In workflow 1 pick that sheet in the two Google Sheets nodes; in workflow 2 pick it in the trigger and in "Mark status as sent". Publish both.
3. Admin > Dealers > the dealer: Automation webhook URL = `https://instance-447316956.n8nclouds.host/webhook/narizmart-dealer-order`.
4. Place a test order for a dealer product, confirm it, and check the row appears; set Status to `shipped` in the sheet and watch the order change.

Dealer statuses the shop understands: processing, shipped, delivered, returned, cancelled. `NEW` and `CANCELLED - do not ship` are written by the shop and are never sent back.
