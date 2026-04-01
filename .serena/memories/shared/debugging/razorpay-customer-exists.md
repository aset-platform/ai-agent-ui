# Razorpay: "Customer already exists" Error

## Problem
After rebuilding `auth.users` table, the `razorpay_customer_id` field is empty. The checkout endpoint calls `client.customer.create()` which fails with:
```
razorpay.errors.BadRequestError: Customer already exists for the merchant
```

## Root Cause
Razorpay enforces unique customers per email per merchant. The customer exists in Razorpay but our DB lost the reference.

## Fix (in `auth/endpoints/subscription_routes.py`)
```python
try:
    cust = client.customer.create({...})
except Exception as exc:
    if "already exists" in str(exc).lower():
        # Paginate customer.all() to find by email
        skip = 0
        while not rz_cust_id and skip < 200:
            page = client.customer.all({"count": 50, "skip": skip})
            for c in page.get("items", []):
                if c.get("email") == user.email:
                    rz_cust_id = c["id"]
                    break
            skip += 50
```

## Key Detail
- Razorpay SDK has NO email-based customer lookup API
- `customer.all(count=1)` only returns the most recent customer (NOT filtered)
- Must paginate with `skip` to search through all customers
- Same pattern applies to Stripe (`stripe.Customer.list(email=...)` is easier — Stripe supports email filter)
