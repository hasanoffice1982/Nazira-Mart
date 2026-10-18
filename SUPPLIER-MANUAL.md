# NarizMart: automatic product import and orders (step-by-step manual)

You do this once. After that, products and orders move by themselves.

## What happens automatically
| Step | Who does it |
|---|---|
| New matching products come from the supplier (hidden or live, your choice) | cron job, every 30 min |
| Price and stock stay equal to the supplier's | cron job |
| A customer orders on narizmart.com | customer |
| Safe orders confirm themselves (small, new number, supplier items) | site |
| Confirmed orders are sent to the supplier; cancelled ones are cancelled there | site |
| Failed sends are retried; you get an email when the wallet is low | cron job |
| Shipped / Delivered / Returned + tracking come back | supplier webhook |
You only: top up the wallet, review new products, and call customers on orders the site did not auto-confirm.

## Part A: connect (once)
1. **Supplier panel > My Stores > Nariz Mart > API Settings** (`.../my-stores/152/store-api-settings`). Click **Copy Public Key** and **Copy Secret Key**.
2. Find your server's outgoing IP. In the cPanel terminal: `curl -s https://ifconfig.me`
3. In the same API Settings page: **Whitelisted IPs** = that IP; **Allowed Domains** = `narizmart.com`. Save.
4. On the server edit `/home/salespal/narizmart_app/.env` (File Manager > Edit, or `nano`) and add (type the real values yourself):
```
SUPPLIER_API_KEY=key_live_...
SUPPLIER_API_SECRET=sec_live_...
SUPPLIER_WEBHOOK_TOKEN=<random string: python3 -c "import secrets;print(secrets.token_urlsafe(32))">
```
5. Set the webhook in the supplier panel (API Settings > Webhook Endpoints): **Payload URL** `https://narizmart.com/supplier/webhook/<SUPPLIER_WEBHOOK_TOKEN>/`. Webhook Secret: leave empty.
6. Restart the app (Setup Python App > RESTART).

## Part B: test the connection
In the cPanel terminal:
```
source /home/salespal/virtualenv/narizmart_app/3.12/bin/activate && cd /home/salespal/narizmart_app
python manage.py supplier_check
```
Good result: `Balance OK ...` and `Products OK: 3090 items`.
- `HTTP 403` or "IP": the IP in step 3 is wrong.
- `HTTP 401`: the keys are wrong.
- `Products failed ... 404`: set `SUPPLIER_PATH_PRODUCTS` (and `SUPPLIER_PATH_ORDERS`, `SUPPLIER_PATH_STATUS`) in `.env` to the paths the supplier support gives you, then test again.

## Part C: import products
**1. Preview (changes nothing):**
```
python manage.py sync_supplier_products --dry-run --include "earring|necklace|pendant|girls|baby|hoodie|bag"
```
It lists what would be imported, with the guessed category and the price.
**2. Import (30 new products, hidden):**
```
python manage.py sync_supplier_products --include "earring|necklace|pendant|girls|baby|hoodie|bag" --exclude "\bmen|islamic|watch" --limit 30
```
**3. Review in the admin** (Products, filter Active = No): check name, description, photos (up to 5 are downloaded), category. Retouch photos if you want. Then tick **Active**.
Add `--activate` to go live at once. Add `--any-category` to also import items whose category cannot be guessed (they go to "Other").
Re-running only refreshes **price and stock**; your edits to names, text and photos stay.
Prices equal the supplier's selling price, because the courier collects exactly that amount. Don't use coupons on these products.

## Part D: switch on the automation
Add to `.env`, then RESTART:
```
SUPPLIER_AUTO_SEND=True
SUPPLIER_AUTO_CONFIRM=True
SUPPLIER_AUTO_CONFIRM_MAX_TOTAL=5000
SUPPLIER_SYNC_INCLUDE=earring|necklace|pendant|girls|baby|hoodie|bag
SUPPLIER_SYNC_EXCLUDE=\bmen|islamic|watch
SUPPLIER_SYNC_ACTIVATE=False
SUPPLIER_SYNC_LIMIT=30
SUPPLIER_LOW_BALANCE=500
```
cPanel > **Cron Jobs** > every 30 minutes, command:
```
/home/salespal/virtualenv/narizmart_app/3.12/bin/python /home/salespal/narizmart_app/manage.py supplier_cron 2>&1 | tail -n 20 > /home/salespal/narizmart_data/supplier_last_run.log
```
See the last run: `cat /home/salespal/narizmart_data/supplier_last_run.log`.
Start safe: keep `SUPPLIER_SYNC_ACTIVATE=False` until you trust the matches; start `SUPPLIER_AUTO_CONFIRM=False` for the first week if you want to call every customer.

## Part E: daily routine (about 10 minutes)
1. Admin > Orders: orders still **Order placed** are the ones the site did not auto-confirm (big, repeat refusals, or not supplier products). Call, then set **Confirmed**: they are sent automatically.
2. Orders marked **Needs manual order** (quantity above 1): place them by hand in the supplier panel (Orders > Manual Order).
3. Check the supplier wallet; keep it above 200 BDT or orders fail.
4. Admin > Products (Active = No): review new products, set **Active**.
5. Refused parcels arrive as **Returned / refused** through the webhook: nothing to do.

## Part F: when something looks wrong
| Symptom | Check |
|---|---|
| Order stays Confirmed, not sent | Admin > Orders > open it > "Supplier orders" shows the error; fix, then use the list action **Send to supplier** |
| "Cannot reach the supplier" | IP whitelist changed (shared hosting IPs can change); repeat Part A step 2-3 |
| Low wallet | top up; failed orders are retried by the cron automatically |
| Status never updates | webhook URL/token mismatch; re-save the Payload URL in the supplier panel |
| New products do not appear | `SUPPLIER_SYNC_INCLUDE` empty or too narrow; run the dry run to see matches |
Logs: `tail -n 40 /home/salespal/narizmart_app/stderr.log`.
Technical details: `SUPPLIER.md`.
