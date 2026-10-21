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

## The n8n workflows (created in your n8n Cloud as drafts)
| Workflow | Link | Purpose |
|---|---|---|
| NarizMart: Messenger + WhatsApp order assistant | https://instance-447316956.n8nclouds.host/workflow/7sDYqNQaMBWy0q0P | OpenAI agent with 4 tools (search_products, quote_order, create_order, order_status) |
| NarizMart: dealer updates by Telegram (AI) | https://instance-447316956.n8nclouds.host/workflow/bNYNyi7kRO9GxVnv | Dealers post "NM-1A2B3C4D shipped, Pathao, tracking PT123" in a Telegram group; OpenAI updates the dealer sheet |

### Setup of the order assistant
1. Server `.env`: `CHAT_ORDER_TOKEN=<secret>` then restart. In n8n create a **Bearer Auth** credential named `NarizMart chat-order token` with the same secret (used by the four tool nodes).
2. **Messenger**: a Meta app with the Messenger product, a Facebook Page, and in n8n a *Facebook App* credential (app id + secret) for the trigger (also set the App ID in the trigger node) and a *Facebook Graph API* credential holding the Page access token for the reply node. Subscribe the page to `messages`.
3. **WhatsApp**: a Meta app with WhatsApp Business Cloud, a phone number, and in n8n the *WhatsApp Business Cloud* credential (access token + business account id) and the *WhatsApp Trigger* credential (app client id + secret). The reply node sends within the 24-hour customer-service window.
4. Publish. Test with your own phone: ask for a product, order it, say yes; the order appears in Admin > Orders with Via = Messenger/WhatsApp, status Pending. Phone the customer, then Confirm.

### Setup of the Telegram dealer bot
Create a **new** bot with @BotFather (do not reuse the LinkedIn bot: one bot can feed only one Telegram trigger) and make an n8n Telegram credential `NarizMart Dealer Bot`. Create one group per dealer, add the bot and the dealer, put the group's chat id in the trigger's *Chat IDs*, pick the dealer sheet in the two Sheets nodes, publish.

## New-order email to staff
Set `ORDER_NOTIFY_EMAILS=narizmart.com@gmail.com` (comma-separated for several) in the server's `.env`, then restart. Every new order (website or chat) sends a plain-text alert whose subject starts with `[NEW ORDER]`, e.g. `[NEW ORDER] NM-56E2C938 · Messenger · ৳560`. In Gmail, create a filter for subject `[NEW ORDER]` → apply label "NarizMart Orders" and star it.
