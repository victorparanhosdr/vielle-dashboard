# Public pricing calculator

Public URL: `/precificacao` (also `/precificacao/` and `/pricing.html`).
The standalone page uses only local static assets. It does not call private APIs,
read or write clinic databases, persist inputs, or change authentication for the
dashboard. No database migration or new environment variable is needed.

## Rules

- Productive hours = days * hours/day * simultaneous rooms * occupancy.
- Hourly structure cost = monthly costs (including pro-labore) / productive hours.
- Procedure base cost = consumed materials + occupied room time.
- Taxes and payment fees are percentages of the sale price.
- Commission applies only to positive profit AFTER base costs, taxes and fees.
- Target margin is the clinic's remaining share divided by the sale price.
- Suggested price = base / (1 - taxes - fees - target_margin / (1 - commission)).
- Impossible combinations are rejected, not displayed as prices.
- Monetary results reconcile in integer cents. Suggested and break-even prices
  are rounded upwards to cover fee/commission rounding.
- A negative result belongs to the clinic; simulated commission is zero.

`pricing-math.js` is the pure calculation module, `pricing.js` handles the UI and
client-side PDF, and `pricing.html`/`pricing.css` define the standalone interface.
PDF generation uses vendored pdf-lib under its accompanying MIT license.

## Local checks

```sh
node --test tests/test_pricing_math.cjs
python3 -m unittest discover -s tests -p test_auth_http.py
python3 tests/serve_pricing_preview.py
```

The preview prints a random localhost URL and uses a temporary auth database.
Clinic database calls and outgoing network calls are blocked in this preview.
Use a Python environment with the existing project dependencies for HTTP tests.
