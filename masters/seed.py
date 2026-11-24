"""Section 5 master data. Idempotent: safe to run repeatedly."""
import datetime
from decimal import Decimal as Dc

from django.contrib.auth import get_user_model

from inventory.models import StockMovement
from inventory.services import post_movement
from masters.models import BOM, BOMLine, Customer, Item, Supplier, Unit

DEMO_PASSWORD = "demo1234"


def seed_master_data():
    User = get_user_model()
    U = {}
    for code, name, dim, factor, carton in [
        ("kg", "Kilogram", "MASS", "1", False), ("g", "Gram", "MASS", "0.001", False),
        ("L", "Litre", "VOLUME", "1", False), ("ml", "Millilitre", "VOLUME", "0.001", False),
        ("pc", "Piece", "COUNT", "1", False), ("pkt", "Packet", "COUNT", "1", False),
        ("ctn", "Carton of packets", "COUNT", "1", True),
    ]:
        U[code], _ = Unit.objects.get_or_create(code=code, defaults=dict(
            name=name, dimension=dim, factor=Dc(factor), is_carton=carton))

    def item(code, name, kind, unit, **kw):
        obj, _ = Item.objects.get_or_create(code=code, defaults=dict(name=name, kind=kind, base_unit=U[unit], **kw))
        return obj

    flour = item("FLOUR", "Flour", "RAW", "kg", reorder_level=Dc("50"))
    oil = item("OIL", "Cooking oil", "RAW", "L", reorder_level=Dc("15"))
    spice = item("SPICE", "Salt and spice mix", "RAW", "kg", reorder_level=Dc("2"))
    pouch = item("POUCH200", "200 g pouch", "PACK", "pc", reorder_level=Dc("200"))
    carton = item("CARTON20", "Carton (holds 20 packets)", "PACK", "pc", reorder_level=Dc("10"))
    mur = item("MUR200", "Murukku 200 g", "FG", "pkt", hsn_code="21069099", gst_rate=Dc("5"),
               selling_price=Dc("50"), units_per_carton=20, shelf_life_days=90, reorder_level=Dc("40"))

    bom = BOM.objects.filter(product=mur).first()
    if not bom:
        bom = BOM.objects.create(product=mur, name="Murukku 200 g standard", yield_qty=Dc("200"),
                                 tolerance_pct=Dc("3"))
        for it, q, u, prim in [(flour, "40", "kg", False), (oil, "10", "L", False), (spice, "1.2", "kg", False),
                               (pouch, "200", "pc", True), (carton, "10", "pc", False)]:
            BOMLine.objects.create(bom=bom, item=it, qty=Dc(q), unit=U[u], is_primary_pack=prim)

    users = {}
    for username, role, first, staff in [
        ("admin", "ADMIN", "Factory Admin", True), ("salesA", "SALESMAN", "Salesman A", False),
        ("salesB", "SALESMAN", "Salesman B", False), ("prod", "PRODUCTION", "Production Supervisor", False),
        ("accountant", "ACCOUNTANT", "Accountant", False), ("owner", "OWNER", "Owner", False)]:
        u, created = User.objects.get_or_create(username=username, defaults=dict(
            role=role, first_name=first, is_staff=staff, is_superuser=(username == "admin")))
        if created:
            u.set_password(DEMO_PASSWORD)
            u.save()
        users[username] = u

    retailer, _ = Customer.objects.get_or_create(name="Retailer A", defaults=dict(
        customer_type="RETAILER", gstin="33AAAAA1111A1Z1", state_code="33", phone="919800000001",
        address="45, Bazaar Street, Tiruchirappalli, Tamil Nadu", salesman=users["salesA"]))
    distributor, _ = Customer.objects.get_or_create(name="Distributor B", defaults=dict(
        customer_type="DISTRIBUTOR", gstin="29BBBBB2222B1Z2", state_code="29", phone="919800000002",
        address="7, MG Road, Bengaluru, Karnataka", salesman=users["salesB"]))
    supplier, _ = Supplier.objects.get_or_create(name="Sri Lakshmi Flour Mills", defaults=dict(
        state_code="33", gstin="33CCCCC3333C1Z3", address="Tiruchirappalli"))

    # Opening stock (posted once)
    if not StockMovement.objects.filter(movement_type=StockMovement.Type.OPENING).exists():
        d = datetime.date.today().replace(day=1)
        # Opening finished goods are not costed in the brief: value them at BOM standard material
        # cost at opening rates = (40*42 + 10*140 + 1.2*200 + 200*2.5 + 10*15) / 200 = 19.85
        std = (Dc("40") * 42 + Dc("10") * 140 + Dc("1.2") * 200 + Dc("200") * Dc("2.5") + Dc("10") * 15) / 200
        for it, q, rate in [(flour, "100", "42"), (oil, "30", "140"), (spice, "5", "200"),
                            (pouch, "500", "2.50"), (carton, "30", "15"), (mur, "50", std)]:
            post_movement(it, Dc(q), StockMovement.Type.OPENING, "opening", 0, "OPENING", d, users["admin"],
                          unit_cost=Dc(rate), remarks="Opening balance")
    return dict(items=dict(flour=flour, oil=oil, spice=spice, pouch=pouch, carton=carton, mur=mur),
                users=users, retailer=retailer, distributor=distributor, supplier=supplier, bom=bom)
