# Order-to-Audit: small food manufacturer prototype

One system from a salesman's order to the monthly auditor's report. Every transaction is entered once, at its
source, and flows into stock, production, costing and accounts.

**Here is the drive Link for a demo video:**https://drive.google.com/file/d/1W0oBCSjdOz481J-hCasolbC3vUFLU1px/view?usp=sharing

## Run it

```bash
python -m venv venv && venv\Scripts\activate        # Mac/Linux: source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_benchmark                      # section 5 master data + one login per role
python manage.py runserver                           # http://127.0.0.1:8000
```

Or with Docker: `docker compose up --build` (same URL, same seed data).

**Logins** (password `demo1234`): `admin`, `salesA`, `salesB`, `prod`, `accountant`, `owner`.
The login page has a one-tap panel for each role, and each role lands on its own home page.

| Role | Lands on | Can do |
|---|---|---|
| salesman | `/m/` mobile screens | pick customer, add products, submit order (offline-capable), see own orders and customer-wise sales |
| admin | `/dashboard/` | verify/edit orders, stock check, approve/reject, GST invoice + PDF, dispatch, payments, credit notes, master data (`/admin/`) |
| production | `/production/` | purchases, production runs, overheads, stock adjustments, stock ledger |
| accountant | `/reports/` | read-only reports and exports, stock, outstanding, audit trail |
| owner | `/dashboard/` | today's orders, stock levels, variance alerts, cost per packet, dues |

### Replaying the benchmark
`python manage.py run_benchmark --reset` wipes the database, seeds it and runs section 5 through the same
service functions the screens use, then prints expected vs actual. Or follow the steps by hand in the browser
(purchase 60 kg flour, production run, orders, payment, overheads, then Reports).
`python manage.py test` runs 42 tests, including the benchmark, stock and GST maths, role access and every report export.

## Stack and why
Django + server-rendered templates + Bootstrap, SQLite by default. One codebase covers mobile and desktop roles
with no separate API layer to maintain, the ORM gives a real relational schema with `Decimal` fields, and
Django's auth, admin and transactions cover roles, master data and all-or-nothing postings. The salesman screens
are a PWA-style page with a service worker and a local order queue. Moving to PostgreSQL is a `DATABASES` change.

## What is built (against section 6)
**Must have:** roles and login; product, customer, raw-material and BOM masters; mobile order entry and admin
approval; GST invoice PDF; production run that updates raw and finished stock; planned-vs-actual consumption and
yield with variance; stock summary and ledger; sales register and stock report as Excel/CSV/PDF.
**Should have:** purchases and suppliers; payments, outstanding and ageing; cost per packet with overheads; GSTR-1 / 3B shaped
summary; variance alerts; owner dashboard.
**Stretch done:** offline order capture with idempotent sync; batch numbers and expiry dates on production output;
low-stock alerts on reorder levels; per-customer prices; WhatsApp/email share links with a public PDF link; audit trail screen.

## Key design choices
* **Ledger-based stock.** `StockMovement` is append-only (save/delete are blocked). Balance = sum of the ledger, never a stored
  number. Every row carries its source document (`PUR-0001`, `PR-0001`, `INV/2026-27/0001`, `CN/...`). Mistakes are fixed with reversing rows.
* **Costing: weighted average**, derived from the ledger (total value / total qty). Simple, stable against price swings, and easy to audit.
  Issues are valued at the average at that moment.
* **Overheads** are entered per run and shared equally across good packets. Added later, they are capitalised through a quantity-less
  `COST_ADJ` ledger row so stock value and cost per packet agree.
* **Money** is `Decimal` everywhere. GST is rounded once per line to paise and SGST takes the remainder so CGST + SGST equals the tax exactly.
* **Invoices** snapshot the buyer, are numbered per financial year, and are never deleted: cancelling issues a credit note and returns stock at original cost.
* **Audit trail:** `AuditLog` records user, time, action and detail for orders, invoices, production runs and stock adjustments.

## Benchmark results
| Check | Expected | Result |
|---|---|---|
| Flour closing | 120 kg | 120 |
| Oil closing | 19.5 L | 19.5 |
| Pouches closing | 306 | 306 |
| Finished goods after run / after dispatch | 238 / 88 | 238 / 88 |
| Yield variance | -6%, flagged | -6.00%, flagged |
| Oil variance | +5%, flagged | +5.00%, flagged |
| Retailer A invoice | 7,500 + 187.50 + 187.50 = 7,875 | same |
| Retailer A outstanding | 3,875 | 3,875 |
| Distributor B | shortage 32, IGST | shortage 32, IGST 300.00 on 6,000 |
| Cost per packet | our figure | **31.14** (materials 4,055.00 + labour 1,200 + gas/power 600 = 5,855.00 / 188) |

Notes on differences or choices:
* Flour is valued at the weighted average 43.125 (100 kg at 42 and 60 kg at 45). FIFO would use 42 for the 40 kg, making materials 4,010
  and cost per packet 30.90. We chose weighted average and say so.
* Pouches 194 vs 200 is -3.00%: **not** flagged because the rule is "greater than 3%". Cartons 9 vs 10 (-10%) are flagged.
* The brief gives no cost for the 50 opening finished packets. We value them at the BOM standard material cost at opening rates, 19.85.
  This only affects the cost of goods of stock that was already on hand, not cost per packet of the run.

## Assumptions
* Price list rate per item, with optional customer-specific prices. Orders are priced when placed, admin may edit.
* Approval is blocked on shortage. Stock promised to approved-but-undispatched orders counts as reserved. Admin can trim to available stock.
* Payments are applied to the oldest invoice first for ageing; a payment can also be tied to one invoice.
* HSN 21069099 for murukku at 5% GST (the exercise says assume 5%).
* Masters are maintained in Django admin (admin role only).

## Known gaps
* Partial dispatch and sales returns of individual lines are not modelled (only full dispatch and full cancellation by credit note).
* No FEFO dispatch (batch and expiry are recorded, not used to pick stock).
* No Tally export, e-invoice, or Tamil UI. Work in progress is not modelled.
* Dispatches made before overheads are added keep the earlier cost of goods; only stock still on hand picks up the overhead.
* The offline queue needs the order page to have been opened once online; browser behaviour was not tested on a real phone.
