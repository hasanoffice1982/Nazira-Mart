"""Starter content for the store's required pages. It is a DRAFT: review and edit it in Admin → Pages
(and have the privacy policy and terms checked against Bangladesh law) before you launch."""

PAGES = [
    {
        "slug": "help-center", "title": "Help Center", "group": "care", "position": 1,
        "meta_description": "Answers to common questions about ordering, delivery, payment and your NarizMart account.",
        "body": """## Ordering
**How do I place an order?** Add items to your bag, press checkout, enter your delivery details and confirm. If you're not signed in we email you a 6-digit code to confirm your order, then create your account for you automatically.

**Why do you send a code?** It proves the email address is yours and keeps fake orders out. The code expires after 10 minutes.

**Can I change or cancel my order?** You can cancel from **My orders** while the order is still *Order placed* or *Confirmed*. After packing starts, contact us.

## Payment
**How do I pay?** Cash on delivery: you pay the courier when your order arrives.

## Delivery
**How much is delivery?** See our Shipping & Delivery page for charges and timing.

## Your account
**I forgot my password.** Use *Forgot password* on the sign-in page and we'll email you a reset link.

**What is the sign-in code?** You can turn on an extra email code at sign-in from **My account → Security**.

## Still need help?
Send us a message from the Contact us page and quote your order number.""",
    },
    {
        "slug": "shipping-delivery", "title": "Shipping & Delivery", "group": "care", "position": 2,
        "meta_description": "Delivery areas, charges and timing for NarizMart orders across Bangladesh.",
        "body": """## Where we deliver
We deliver across Bangladesh.

## Delivery charges
- Inside Dhaka: [[currency]][[delivery_dhaka]]
- Outside Dhaka: [[currency]][[delivery_outside]]
- Free delivery on orders of [[currency]][[free_delivery]] or more

## Delivery time
- Inside Dhaka: [1–2 working days]
- Outside Dhaka: [3–5 working days]

Delivery times start once your order is confirmed and packed. Delays can happen during holidays and bad weather.

## Tracking your order
Sign in and open **My orders** to see each step: placed, confirmed, packing, out for delivery and delivered. We also email you at every step.

## Receiving your order
Please keep your phone on, check the parcel before paying the courier, and pay the order total in cash. If something looks wrong, tell the courier and contact us right away.""",
    },
    {
        "slug": "returns-exchanges", "title": "Returns & Exchanges", "group": "care", "position": 3,
        "meta_description": "How to return or exchange a NarizMart order.",
        "body": """## Our promise
We want you to be happy with what you buy. If something isn't right, tell us.

## What can be returned
- Items that arrive damaged, defective or different from what you ordered
- Unused items in original packaging within [7 days] of delivery

## What can't be returned
- Items used, washed or damaged after delivery
- Items marked as final sale
- Hygiene-related products once opened

## How to return
1. Send us a message from the Contact us page with your order number and photos if the item is damaged.
2. We will reply with the next step, usually within one working day.
3. Once we receive and check the item we exchange it or refund you. Cash-on-delivery refunds are sent by [bKash / bank transfer].

## Cancelling before delivery
You can cancel from **My orders** while the order is *Order placed* or *Confirmed*. Stock is returned automatically.""",
    },
    {
        "slug": "privacy-policy", "title": "Privacy Policy", "group": "legal", "position": 1,
        "meta_description": "How NarizMart collects, uses and protects your personal information.",
        "body": """## Who we are
NarizMart ("we", "us") runs this online store for customers in Bangladesh.

## What we collect
- Account details: name, email address, phone number and password (stored securely, never in plain text)
- Order details: items, delivery and billing addresses, order history
- Messages you send us through the contact form
- Basic technical data such as your browser type, used for security and to keep the site working

## Why we use it
- To process and deliver your orders and email you about them
- To send sign-in and confirmation codes and to keep your account secure
- To answer your questions and improve the store
- To send newsletters, only if you subscribe

## Who we share it with
Couriers receive your name, phone number and delivery address so they can deliver. Our email provider sends our emails for us. We do not sell your personal information.

## Cookies
We use essential cookies to keep you signed in, remember your bag and protect forms from abuse. We do not use advertising cookies.

## How long we keep it
We keep order records as long as needed for accounting and legal obligations. You can ask us to delete your account.

## Your choices
You can see your orders and update your details in your account. To correct or delete your information, or to unsubscribe, contact us.

## Security
Passwords are hashed, connections use HTTPS, and we limit sign-in attempts to protect your account.

## Changes
We may update this policy and will change the date below when we do.

## Contact
Questions about your privacy? Use the Contact us page.""",
    },
    {
        "slug": "terms-and-conditions", "title": "Terms & Conditions", "group": "legal", "position": 2,
        "meta_description": "The terms for shopping at NarizMart.",
        "body": """## Using this store
By placing an order you agree to these terms. You must give accurate details and be able to enter a contract under the law.

## Orders
An order is accepted when we confirm it. We may cancel an order if an item is out of stock, a price was shown incorrectly, or we suspect fraud. If we cancel, you are not charged.

## Prices
Prices are in Bangladeshi Taka (৳) and include applicable taxes unless stated. Delivery charges are shown at checkout.

## Payment
Payment is cash on delivery unless we offer another method at checkout.

## Delivery
Delivery times are estimates. See our Shipping & Delivery page.

## Returns
See our Returns & Exchanges page.

## Your account
Keep your password and sign-in codes private. Tell us at once if you think your account was used without permission.

## Acceptable use
Don't misuse the store: no fake orders, automated scraping, or attempts to break or overload the site.

## Product information
We try to show colours and details accurately, but screens differ and small variations can occur.

## Liability
To the extent the law allows, we are not liable for indirect losses. Nothing here limits your legal rights as a consumer.

## Changes
We may update these terms; the version on this page applies to new orders.

## Contact
Use the Contact us page for any questions.""",
    },
]
