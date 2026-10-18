# Supplier integration (Smart DropShop BD)

Automatic **product import** and **order sending** through the supplier's store API.
Code: `apps/supplier/`. It is switched off until the keys are set.

## 1. Set up on the server (`.env`, chmod 600; never commit keys)
```
SUPPLIER_API_KEY=key_live_...        # My Stores > Nariz Mart > API Settings
SUPPLIER_API_SECRET=sec_live_...
SUPPLIER_WEBHOOK_TOKEN=<long random string>
SUPPLIER_AUTO_SEND=True              # optional: send on "Confirmed"
```
In the supplier's API Settings add: **Whitelisted IP** = the server's outgoing IP (`curl -s https://ifconfig.me` on the server),
**Allowed domain** = `narizmart.com`, **Webhook URL** = `https://narizmart.com/supplier/webhook/<SUPPLIER_WEBHOOK_TOKEN>/`.

## 2. Test
```
python manage.py supplier_check
```
Prints the wallet balance and the number of products. If products show HTTP 404, set `SUPPLIER_PATH_PRODUCTS`
(the public docs only print `/balance`); same for `SUPPLIER_PATH_ORDERS` and `SUPPLIER_PATH_STATUS`.

## 3. Import products
```
python manage.py sync_supplier_products --dry-run --include "earring|necklace|girls|baby"
python manage.py sync_supplier_products --include "earring|necklace|girls|baby" --limit 30
```
New products are created **hidden**: review, fix names and photos, then tick Active. Price = the supplier's `final_selling_price`
(the courier collects exactly that). Running it again only refreshes price and stock. Cron daily:
`python manage.py sync_supplier_products` (existing products are only refreshed; new ones need `--include`).

## 4. Orders
Confirm an order (after the phone call): it is sent automatically when `SUPPLIER_AUTO_SEND=True`, or use the order list action
**Send to supplier**. Result and any error show under the order ("Supplier orders"). Orders with quantity above 1 are marked
"Needs manual order" until you enable `SUPPLIER_SEND_QUANTITY` (only if the supplier API accepts a quantity field).
Coupons/discounts do not reach the supplier: the courier still collects the full supplier price.

## 5. Status updates (webhook)
Shipped / Delivered / Returned / Cancelled from the supplier move the order automatically and save courier and tracking.

## 6. Full automation (recommended settings in `.env`)
```
SUPPLIER_AUTO_SEND=True               # confirmed orders go to the supplier; cancelled orders are cancelled there too
SUPPLIER_AUTO_CONFIRM=True            # safe new orders confirm themselves (no phone call)
SUPPLIER_AUTO_CONFIRM_MAX_TOTAL=5000
SUPPLIER_SYNC_INCLUDE=earring|necklace|pendant|girls|baby|hoodie|bag     # which new products to import by themselves
SUPPLIER_SYNC_EXCLUDE=\bmen|islamic|watch
SUPPLIER_SYNC_ACTIVATE=False          # False = new products wait hidden for your review; True = go live at once
SUPPLIER_SYNC_LIMIT=30                # new products per run
SUPPLIER_LOW_BALANCE=500              # email SUPPORT_EMAIL when the supplier wallet is below this
```
**Auto-confirm only happens when** the order total is within the limit, every item comes from the supplier with quantity 1,
and the customer's phone has never refused or cancelled an order. Anything else stays "Order placed" for a phone call.
**Cron (every 30 minutes)** runs everything else: refresh price and stock, import new matching products, retry failed sends,
and email you when the wallet is low:
```
*/30 * * * * /home/salespal/virtualenv/narizmart_app/3.12/bin/python /home/salespal/narizmart_app/manage.py supplier_cron 2>&1 | tail -n 20 > /home/salespal/narizmart_data/supplier_last_run.log
```
Without `SUPPLIER_SYNC_INCLUDE`, the cron only refreshes price and stock of products you already linked, and never adds new ones.
Prices from discount coupons are not passed to the supplier (the courier collects the supplier's price), so avoid coupons on supplier products.
