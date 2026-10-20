# Orders from Messenger / WhatsApp (and Telegram)

A bot in n8n (OpenAI) chats with the customer, finds products, quotes the total and **creates the order on the website as Pending**.
Staff then phone the customer and click **Confirm** as usual; chat orders are never auto-confirmed. The order shows **Via: WhatsApp / Messenger** in Admin > Orders (and a filter).

## Switch it on
Add to the server `.env`, then restart: `CHAT_ORDER_TOKEN=<long random secret>` (generate: `python3 -c "import secrets;print(secrets.token_urlsafe(40))"`).
With no token the API does not exist (404). The bot sends `Authorization: Bearer <token>`.

## API (JSON)
`GET /api/chat/products/?q=frock&limit=5` -> `{"products":[{"sku","name","category","price","price_varies","in_stock","url","image","needs_variant","variants":[{"sku","label","price","in_stock"}]}]}`
Use a **variant sku** in orders when `needs_variant` is true.

`POST /api/chat/orders/`
```json
{"channel":"whatsapp","channel_user_id":"8801712345678","request_id":"<message id>","full_name":"Rafi","phone":"01712345678",
 "address":"House 1, Mirpur 1","city":"Dhaka","delivery_area":"dhaka","notes":"call after 6pm","items":[{"sku":"FR1-18","quantity":1}],"dry_run":false}
```
- `channel`: messenger | whatsapp | telegram. `delivery_area`: dhaka | sub | outside.
- `dry_run: true` returns the exact totals (delivery charge, discount, total) and saves nothing: use it to quote before the customer says yes.
- `request_id` makes a retry harmless: the same id never creates a second order (returns the first one, `created: false`).
- Safety limits: max 8 items, quantity 1-5, 5 orders per phone per day, stock and prices always come from the shop.
- Errors are `{"error": "..."}` with a message the bot can read to the customer.

`GET /api/chat/orders/status/?phone=01712345678&number=NM-1A2B3C4D` -> status, tracking, items (only when phone and number match).

Chat customers get a light account keyed by phone (`<phone>@chat.narizmart.invalid`, no password, no emails sent to it).
