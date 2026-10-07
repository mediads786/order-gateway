import logging
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

env_file = PROJECT_ROOT / ".env"
if env_file.is_file():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator:
            os.environ.setdefault(name.strip(), value.strip().strip("\"'"))

from app.adapters.factory import get_adapter
from app.adapters.odoo import OdooAdapter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SKUS = ("BOOK", "PEN", "X", "TSHIRT-BLK-M", "MUG-WHT", "NOTEBOOK-A5", "TEA")


def main() -> None:
    adapter = get_adapter()
    if not isinstance(adapter, OdooAdapter):
        raise RuntimeError("Set ERP_ADAPTER=odoo before running the Odoo seed script")
    for sku in SKUS:
        adapter.set_opening_stock(sku, 100)
        logger.info("Seeded Odoo product and opening stock sku=%s", sku)


if __name__ == "__main__":
    main()
