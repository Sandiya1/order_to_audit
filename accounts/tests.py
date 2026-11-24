import datetime

from django.test import TestCase
from django.urls import reverse

from masters.benchmark import run_scenario
from masters.seed import DEMO_PASSWORD


class RoleAccessAndSmokeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.r = run_scenario()

    def login(self, user):
        self.client.logout()
        self.assertTrue(self.client.login(username=user, password=DEMO_PASSWORD))

    def ok(self, name, *args, code=200, **q):
        resp = self.client.get(reverse(name, args=args), q)
        self.assertEqual(resp.status_code, code, f"{name} -> {resp.status_code}")
        return resp

    def test_anonymous_redirected(self):
        self.assertEqual(self.client.get("/dashboard/").status_code, 302)
        self.assertEqual(self.client.get("/m/").status_code, 302)

    def test_salesman_sees_only_mobile(self):
        self.login("salesA")
        for n in ("m_home", "m_new", "m_orders", "m_sales"):
            self.ok(n)
        for n in ("dashboard", "order_list", "invoice_list", "production_list", "stock_list", "reports_index", "purchase_list"):
            self.ok(n, code=403)

    def test_salesman_only_sees_own_orders(self):
        self.login("salesA")
        body = self.ok("m_orders").content.decode()
        self.assertIn("Retailer A", body)
        self.assertNotIn("Distributor B", body)

    def test_production_role(self):
        self.login("prod")
        for n in ("production_list", "production_new", "purchase_list", "purchase_new", "stock_list", "stock_adjust"):
            self.ok(n)
        for n in ("order_list", "invoice_list", "outstanding", "reports_index", "dashboard"):
            self.ok(n, code=403)

    def test_accountant_is_read_only(self):
        self.login("accountant")
        for n in ("reports_index", "invoice_list", "outstanding", "stock_list", "production_list", "purchase_list", "audit_log"):
            self.ok(n)
        self.ok("production_new", code=403)
        self.ok("stock_adjust", code=403)
        self.ok("payment_new", code=403)
        self.ok("m_home", code=403)
        inv = self.r["invoice"]
        n_before = inv.payments.count()
        resp = self.client.post(reverse("invoice_detail", args=[inv.pk]), {"action": "cancel", "reason": "hack"})
        inv.refresh_from_db()
        self.assertEqual(inv.status, "ISSUED")             # POST ignored for read-only role
        self.assertEqual(inv.payments.count(), n_before)

    def test_admin_pages_and_pdf(self):
        self.login("admin")
        for n in ("dashboard", "order_list", "invoice_list", "outstanding", "payment_new", "production_list", "production_new",
                  "purchase_list", "purchase_new", "stock_list", "stock_adjust", "reports_index", "audit_log", "m_home", "m_new"):
            self.ok(n)
        inv = self.r["invoice"]
        self.ok("invoice_detail", inv.pk)
        self.ok("order_detail", self.r["order_b"].pk)
        resp = self.ok("invoice_pdf", inv.pk)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertTrue(resp.content.startswith(b"%PDF"))
        self.ok("production_detail", self.r["run"].pk)
        self.ok("production_new", product=self.r["run"].product_id)
        self.ok("stock_ledger", self.r["run"].product_id)

    def test_public_invoice_link_needs_no_login(self):
        self.client.logout()
        resp = self.client.get(reverse("invoice_public_pdf", args=[self.r["invoice"].share_token]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.content.startswith(b"%PDF"))

    def test_igst_invoice_pdf_renders(self):
        from sales import services as svc
        from masters.models import Item
        from django.contrib.auth import get_user_model
        U = get_user_model()
        mur, adm = Item.objects.get(code="MUR200"), U.objects.get(username="admin")
        o = self.r["order_b"]
        svc.reduce_to_available(adm, o)
        svc.approve_order(adm, o)
        inv = svc.create_invoice(adm, o)
        self.assertTrue(inv.is_igst); self.assertEqual(inv.cgst, 0)
        self.assertEqual(inv.igst, inv.taxable_value * 5 / 100)
        self.login("admin")
        self.assertTrue(self.ok("invoice_pdf", inv.pk).content.startswith(b"%PDF"))

    def test_all_reports_all_formats(self):
        self.login("accountant")
        for slug in ("sales-register", "customer-sales", "product-sales", "gst-summary", "purchase-register",
                     "stock-summary", "stock-ledger", "production", "collections"):
            self.ok("report_view", slug)
            for fmt, ctype in (("csv", "text/csv"), ("xlsx", "spreadsheetml"), ("pdf", "application/pdf")):
                resp = self.ok("report_view", slug, format=fmt)
                self.assertIn(ctype, resp["Content-Type"], f"{slug}.{fmt}")

    def test_mobile_api_creates_order_and_is_idempotent(self):
        import json, uuid
        from masters.models import Customer, Item
        self.login("salesA")
        cust, mur = Customer.objects.get(name="Retailer A"), Item.objects.get(code="MUR200")
        payload = {"client_uuid": str(uuid.uuid4()), "customer_id": cust.pk, "lines": [{"item_id": mur.pk, "qty": 12}]}
        r1 = self.client.post(reverse("m_api_orders"), json.dumps(payload), content_type="application/json").json()
        r2 = self.client.post(reverse("m_api_orders"), json.dumps(payload), content_type="application/json").json()
        self.assertTrue(r1["ok"]); self.assertFalse(r1["duplicate"])
        self.assertTrue(r2["duplicate"]); self.assertEqual(r1["number"], r2["number"])
        bad = self.client.post(reverse("m_api_orders"), json.dumps({"customer_id": cust.pk, "lines": []}), content_type="application/json")
        self.assertEqual(bad.status_code, 400)


class LoginRedirectTests(TestCase):
    """Each role lands on its own home page after signing in."""
    @classmethod
    def setUpTestData(cls):
        from masters.seed import seed_master_data
        seed_master_data()

    def test_each_role_lands_on_its_home(self):
        expected = {"admin": "/dashboard/", "salesA": "/m/", "prod": "/production/",
                    "accountant": "/reports/", "owner": "/dashboard/"}
        for user, path in expected.items():
            self.client.logout()
            resp = self.client.post("/login/", {"username": user, "password": DEMO_PASSWORD}, follow=True)
            self.assertEqual(resp.redirect_chain[-1][0], path, user)
            self.assertEqual(resp.status_code, 200, user)

    def test_login_page_shows_demo_panel(self):
        body = self.client.get("/login/").content.decode()
        for role in ("Factory admin", "Salesman", "Production supervisor", "Accountant", "Owner"):
            self.assertIn(role, body)

    def test_wrong_password_rejected(self):
        resp = self.client.post("/login/", {"username": "admin", "password": "nope"})
        self.assertContains(resp, "Wrong username or password")
