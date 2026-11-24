"""Runs the section 5 scenario through the same services the UI uses."""
import datetime
from decimal import Decimal as Dc

from django.core.exceptions import ValidationError

from inventory.services import create_purchase, on_hand
from masters.models import Unit
from production.services import post_run, update_overheads
from sales import services as sales
from sales.models import Order


def run_scenario():
    from masters.seed import seed_master_data
    s = seed_master_data()
    it, u = s["items"], s["users"]
    today = datetime.date.today()
    kg = Unit.objects.get(code="kg")
    out = {}

    # 1. purchase 60 kg flour @ 45
    create_purchase(u["prod"], s["supplier"], today, [dict(item=it["flour"], qty=Dc("60"), unit=kg, rate=Dc("45"))],
                    bill_no="FM-101")
    # 2-3. production run: 40 kg flour, 10.5 L oil, 1.2 kg spice; 188 good, 6 pouches damaged, 9 cartons used
    run = post_run(u["prod"], it["mur"], today, 1, 188, 6,
                   actuals={it["flour"].pk: 40, it["oil"].pk: Dc("10.5"), it["spice"].pk: Dc("1.2"),
                            it["carton"].pk: 9})
    out["run"] = run
    out["fg_after_run"] = on_hand(it["mur"])
    # 4-5. Salesman A order 150 for Retailer A -> approve -> invoice -> dispatch
    oa, _ = sales.place_order(u["salesA"], s["retailer"], [(it["mur"], 150)])
    sales.approve_order(u["admin"], oa)
    inv = sales.create_invoice(u["admin"], oa)
    sales.dispatch_invoice(u["admin"], inv)
    out["invoice"] = inv
    out["fg_after_dispatch"] = on_hand(it["mur"])
    # 6. Salesman B order 120 for Distributor B -> shortage
    ob, _ = sales.place_order(u["salesB"], s["distributor"], [(it["mur"], 120)])
    try:
        sales.approve_order(u["admin"], ob)
        out["shortage"] = None
    except ValidationError as e:
        out["shortage"] = e.shortages[0]["shortage"]
    out["order_b"] = ob
    out["tax_b"] = sales.tax_preview(ob)
    # 7. payment 4000 from Retailer A
    sales.record_payment(u["admin"], s["retailer"], Dc("4000"), invoice=inv, mode="UPI", reference="UPI-0001")
    out["outstanding_a"] = sales.customer_outstanding(s["retailer"])
    # 8. labour 1200 + gas/power 600
    update_overheads(u["prod"], run, 1200, 600, 0)
    run.refresh_from_db()
    out["run"] = run
    out["balances"] = {k: on_hand(v) for k, v in it.items()}
    return out
