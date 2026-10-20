# Dealers: products that are not on the dropshipping API

Dropship-supplier products are ordered through the supplier API (see SUPPLIER.md). Products from **your own dealers**
are sent to an automation (n8n or Make) which tells the dealer and brings courier and tracking back.

## In the admin
1. **Dealers → Add dealer.** Name, phone, email. Paste the n8n **Production URL** of the workflow's Webhook node into *Automation webhook URL*. Save: the page then shows the *Tracking address* (give it to n8n) and the signing *secret*.
2. **Products → a product → Who ships it → Dealer.** Or add a `dealer` column (the dealer's exact name) to the CSV import.
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
  "dealer": {"id": 2, "name": "Rahim Traders"},
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
