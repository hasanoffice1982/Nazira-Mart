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
