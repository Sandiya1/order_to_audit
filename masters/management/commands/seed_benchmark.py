from django.core.management import call_command
from django.core.management.base import BaseCommand

from masters.seed import DEMO_PASSWORD, seed_master_data


class Command(BaseCommand):
    help = "Load the section 5 master data (items, BOM, customers, opening stock, demo users)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Wipe all data first.")

    def handle(self, *args, **opts):
        if opts["reset"]:
            call_command("flush", interactive=False)
        seed_master_data()
        self.stdout.write(self.style.SUCCESS("Seed data loaded."))
        self.stdout.write(f"Logins (password '{DEMO_PASSWORD}'): admin, salesA, salesB, prod, accountant, owner")
