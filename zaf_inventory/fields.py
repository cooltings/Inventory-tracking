"""Field definitions for every record type.

The database schema, the edit forms, the table columns and CSV import/export are
all driven from these lists, so adding a field here is all that is needed to
add it everywhere (the database migrates itself on next start).
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    # text, multiline, combo (editable w/ suggestions), choice (fixed list),
    # money, int, percent, date, faults, readonly
    kind: str = "text"
    options: tuple = ()
    suggest: Optional[str] = None  # settings list that feeds suggestions
    column: bool = True  # visible as a table column by default
    required: bool = False
    tip: str = ""


SQL_TYPES = {"money": "REAL", "int": "INTEGER", "percent": "INTEGER"}
NUMERIC_KINDS = {"money", "int", "percent"}

# --------------------------------------------------------------------------- lists
GRADES = ("Brand New", "Like New", "A - Excellent", "B - Good", "C - Fair",
          "D - Poor", "For Parts / Not Working")
NETWORKS = ("Unlocked", "EE", "O2", "Vodafone", "Three", "Tesco Mobile",
            "giffgaff", "Sky", "Other / Locked", "Unknown")
STORAGE = ("8GB", "16GB", "32GB", "64GB", "128GB", "256GB", "512GB", "1TB", "2TB")
# Written-off items stay on record but are left out of stock counts and values.
WRITTEN_OFF = "Written Off"
DEVICE_STATUSES = ("In Stock", "Listed", "Reserved", "Awaiting Repair",
                   "In Repair", "On Hold", WRITTEN_OFF)
PART_CONDITIONS = ("New - Genuine", "New - Aftermarket", "Used - Pulled",
                   "Refurbished", "Faulty / Untested")
PART_STATUSES = ("In Stock", "Listed", "Reserved", WRITTEN_OFF)
SALE_STATUSES = ("Awaiting Payment", "Paid", "Shipped", "Delivered", "Completed",
                 "Returned", "Refunded", "Cancelled")
# sales in these states are left out of revenue / profit totals
EXCLUDED_SALE_STATUSES = ("Returned", "Refunded", "Cancelled")

DEFAULT_LISTS = {
    "brands": ["Apple", "Samsung", "Google", "OnePlus", "Xiaomi", "Huawei",
               "Motorola", "Sony", "Nokia", "Oppo", "Honor", "Nothing", "Realme"],
    "faults": [
        "Cracked screen", "Cracked back glass", "Dead pixels / lines",
        "Screen burn-in", "Touch not working", "Face ID not working",
        "Touch ID / fingerprint faulty", "Battery needs service",
        "Won't charge", "Charging port faulty", "No power / dead", "Boot loop",
        "iCloud / Activation locked", "Google FRP locked", "Network locked",
        "Blacklisted / barred IMEI", "No signal / baseband",
        "Wi-Fi / Bluetooth faulty", "Rear camera faulty", "Front camera faulty",
        "Loudspeaker faulty", "Earpiece faulty", "Microphone faulty",
        "Buttons faulty", "Water damage", "Bent frame / housing damage",
        "True Tone missing", "Non-genuine parts message",
        "Vibration motor faulty", "Software issue",
    ],
    "part_types": ["Screen / LCD", "OLED Display", "Battery", "Charging Port Flex",
                   "Back Glass / Housing", "Rear Camera", "Front Camera",
                   "Loudspeaker", "Earpiece", "Motherboard / Logic Board",
                   "Face ID Module", "Buttons / Flex", "SIM Tray",
                   "Taptic Engine", "Charger / Cable", "Case / Accessory", "Other"],
    "sources": ["eBay", "Facebook Marketplace", "CeX", "Gumtree", "Trade-in",
                "Auction", "Wholesale", "Private sale"],
    "platforms": ["eBay", "Facebook Marketplace", "Back Market", "CeX",
                  "Local / Cash", "Other"],
    "locations": ["Shelf A", "Shelf B", "Drawer 1", "Drawer 2", "Repair bench"],
}

# --------------------------------------------------------------------------- devices
DEVICE_FIELDS = [
    Field("sku", "SKU", "readonly"),
    Field("brand", "Brand", "combo", suggest="brands", required=True),
    Field("model", "Model", "combo", required=True, tip="e.g. iPhone 12 Pro"),
    Field("storage", "Storage", "combo", options=STORAGE),
    Field("colour", "Colour", "combo"),
    Field("imei", "IMEI", "text", tip="15 digit IMEI (*#06#)"),
    Field("serial", "Serial No.", "text"),
    Field("quantity", "Qty", "int", tip="Number of identical units in this line"),
    Field("network", "Network", "choice", options=NETWORKS),
    Field("grade", "Grade", "choice", options=GRADES),
    Field("battery_health", "Battery %", "percent"),
    Field("faults", "Faults", "faults"),
    Field("status", "Status", "choice", options=DEVICE_STATUSES),
    Field("location", "Location", "combo", suggest="locations"),
    Field("purchase_date", "Bought On", "date"),
    Field("source", "Bought From", "combo", suggest="sources", column=False),
    Field("purchase_price", "Purchase Price", "money"),
    Field("repair_cost", "Repair Costs", "money"),
    Field("list_price", "List Price", "money"),
    Field("ebay_item_id", "eBay Item No.", "text", column=False),
    Field("accessories", "Accessories", "text", column=False,
          tip="Box, charger, case..."),
    Field("description", "Description", "multiline", column=False,
          tip="Listing description / condition details"),
]

# --------------------------------------------------------------------------- parts
PART_FIELDS = [
    Field("sku", "SKU", "readonly"),
    Field("part_type", "Part Type", "combo", suggest="part_types", required=True),
    Field("brand", "Brand", "combo", suggest="brands"),
    Field("compatible", "Compatible Models", "text",
          tip="Comma separated, e.g. iPhone 12, iPhone 12 Pro"),
    Field("part_number", "Part No.", "text", column=False),
    Field("imei", "IMEI", "text", tip="For boards / devices that carry an IMEI"),
    Field("serial", "Serial No.", "text"),
    Field("condition", "Condition", "choice", options=PART_CONDITIONS),
    Field("quantity", "Qty", "int"),
    Field("min_qty", "Low Stock At", "int", column=False,
          tip="Highlight when quantity is at or below this"),
    Field("unit_cost", "Unit Cost", "money"),
    Field("list_price", "List Price", "money"),
    Field("status", "Status", "choice", options=PART_STATUSES),
    Field("location", "Location", "combo", suggest="locations"),
    Field("source", "Bought From", "combo", suggest="sources", column=False),
    Field("purchase_date", "Bought On", "date", column=False),
    Field("ebay_item_id", "eBay Item No.", "text", column=False),
    Field("description", "Description", "multiline", column=False),
]

# --------------------------------------------------------------------------- sales
SALE_FIELDS = [
    Field("sku", "SKU", "readonly"),
    Field("title", "Item", "text"),
    Field("item_kind", "Type", "readonly"),
    Field("brand", "Brand", "text", column=False),
    Field("model", "Model", "text", column=False),
    Field("imei", "IMEI", "text"),
    Field("serial", "Serial No.", "text"),
    Field("sale_date", "Sold On", "date"),
    Field("platform", "Platform", "combo", suggest="platforms"),
    Field("order_number", "Order No.", "text"),
    Field("buyer", "Buyer", "text"),
    Field("quantity", "Qty", "int"),
    Field("sale_price", "Sale Price", "money"),
    Field("postage_charged", "P&P Charged", "money", column=False),
    Field("fees", "Fees", "money"),
    Field("postage_cost", "Postage Cost", "money"),
    Field("other_costs", "Other Costs", "money", column=False),
    Field("cost_basis", "Item Cost", "money"),
    Field("profit", "Profit", "money"),
    Field("status", "Status", "choice", options=SALE_STATUSES),
    Field("tracking", "Tracking", "text", column=False),
]

TABLE_FIELDS = {"devices": DEVICE_FIELDS, "parts": PART_FIELDS, "sales": SALE_FIELDS}


def field_map(fields):
    return {f.key: f for f in fields}
