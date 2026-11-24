from django.core.management import call_command
from django.core.management.base import BaseCommand

from masters.benchmark import run_scenario
from masters.utils import inr


class Command(BaseCommand):
    help = "Run the section 5 scenario end to end and print the checks (use on a freshly seeded DB: --reset)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Flush the DB first so numbers match exactly.")

    def handle(self, *args, **opts):
        if opts["reset"]:
            call_command("flush", interactive=False)
        r = run_scenario()
        run, inv, b = r["run"], r["invoice"], r["balances"]
        lines = {l.item.code: l for l in run.lines.all()}
        rows = [
            ("Flour closing balance (kg)", "120", f"{b['flour']:f}"),
            ("Oil closing balance (L)", "19.5", f"{b['oil']:f}"),
            ("Pouches closing balance", "306", f"{b['pouch']:f}"),
            ("Finished goods after run", "238", f"{r['fg_after_run']:f}"),
            ("Finished goods after Retailer A dispatch", "88", f"{r['fg_after_dispatch']:f}"),
            ("Yield variance %", "-6 (flagged)", f"{run.yield_variance_pct} ({'flagged' if run.yield_flagged else 'ok'})"),
            ("Oil consumption variance %", "+5 (flagged)",
             f"{lines['OIL'].variance_pct} ({'flagged' if lines['OIL'].flagged else 'ok'})"),
            ("Retailer A invoice taxable", "7500.00", f"{inv.taxable_value}"),
            ("CGST / SGST", "187.50 / 187.50", f"{inv.cgst} / {inv.sgst}"),
            ("Invoice total", "7875.00", f"{inv.total}"),
            ("Retailer A outstanding", "3875.00", f"{r['outstanding_a']}"),
            ("Distributor B shortage", "32", f"{r['shortage']:f}"),
            ("Distributor B tax is IGST", "yes", "yes" if r["tax_b"]["igst_applies"] else "no"),
        ]
        w = max(len(x[0]) for x in rows)
        self.stdout.write(f"{'Check'.ljust(w)}  {'Expected':<16} Actual")
        for name, exp, act in rows:
            self.stdout.write(f"{name.ljust(w)}  {exp:<16} {act}")
        self.stdout.write("")
        self.stdout.write(f"Material cost  : Rs {inr(run.material_cost)}  (flour @ weighted average)")
        self.stdout.write(f"Overheads      : Rs {inr(run.total_overhead)}")
        self.stdout.write(f"Total run cost : Rs {inr(run.total_cost)}  over {run.good_output:f} good packets")
        self.stdout.write(self.style.SUCCESS(
            f"Cost per packet: Rs {run.cost_per_packet:.2f}   margin Rs {run.margin_per_packet:.2f} ({run.margin_pct}%)"))
