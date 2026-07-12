import os
import json
import re
import time
from datetime import datetime, timezone
from supabase import create_client
from blocket_api import BlocketAPI, RecommerceAd

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

api = BlocketAPI()

# ---- Category filter: only real phone listings, no accessories ----
PHONE_CATEGORIES = {"Mobiltelefoner"}

# ---- Brand keyword fallback, used when Blocket's own brand field is empty ----
BRAND_KEYWORDS = {
    "Apple": ["iphone"],
    "Samsung": ["samsung", "galaxy"],
    "Google": ["pixel"],
    "Sony": ["xperia"],
    "OnePlus": ["oneplus"],
    "Huawei": ["huawei"],
}
CONDITION_KEYWORDS = {
    "Ny / oanvänd": ["helt ny", "oanvänd", "nyskick", "ny skick"],
    "Mycket bra skick": ["mycket fint skick", "mycket bra skick", "toppskick"],
    "Bra skick": ["fint skick", "bra skick", "fungerar perfekt"],
    "Begagnad": ["begagnad", "använd", "sliten"],
}

def infer_condition(title, description):
    text = f"{title or ''} {description or ''}".lower()
    for condition, keywords in CONDITION_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return condition
    return "Ej specificerat"  # honest fallback -- we genuinely don't know, don't guess

def infer_brand(title, description):
    text = f"{title or ''} {description or ''}".lower()
    for brand, keywords in BRAND_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return brand
    return None

def parse_storage_gb(raw):
    """'256 GB' -> 256, '1 TB' -> 1024, None/unparseable -> None"""
    if not raw:
        return None
    match = re.search(r"([\d.]+)\s*(GB|TB)", str(raw), re.IGNORECASE)
    if not match:
        return None
    value, unit = float(match.group(1)), match.group(2).upper()
    return int(value * 1024) if unit == "TB" else int(value)

def find_price(item, root):
    """Search-result price first, then fall back to the detail page's structured offer price."""
    if item.get("price_amount") is not None:
        return item["price_amount"]
    offer_price = (root.get("jsonLd", {}) or {}).get("offers", {}).get("price")
    try:
        return float(offer_price) if offer_price is not None else None
    except (TypeError, ValueError):
        return None

def map_to_schema(item, detail):
    root = detail.get("loaderData", {}).get("item-recommerce", {}) or {}
    item_data = root.get("itemData", {}) or {}
    json_ld = root.get("jsonLd", {}) or {}
    offers = json_ld.get("offers", {}) or {}
    shop = root.get("shopProfileData") or {}
    transactable = root.get("transactableData", {}) or {}
    category = item_data.get("category", {}) or {}
    location = item_data.get("location", {}) or {}
    meta = item_data.get("meta", {}) or {}
    images = item_data.get("images") or []

    title = item_data.get("title") or item.get("heading")
    description = item_data.get("description")

    brand = item.get("brand") or json_ld.get("brand") or infer_brand(title, description)
    price = find_price(item, root)
    storage = parse_storage_gb(item.get("memory_size"))

    disposed = item_data.get("disposed")
    is_inactive = meta.get("isInactive")
    availability = offers.get("availability")
    if disposed or is_inactive or (availability and "OutOfStock" in str(availability)):
        listing_status, record_type, sale_confidence = "removed", "delisted_unknown", 0.2
    else:
        listing_status, record_type, sale_confidence = "active", "active_listing", 0.3

    row = {
        "listing_id": f"blocket:{item.get('id')}",
        "source_platform": "Blocket",
        "marketplace_url": item.get("canonical_url"),
        "snapshot_id": int(time.time() * 1000) % 2_000_000_000,
        "record_type": record_type,
        "ingestion_method": "scrape",

        "brand": brand,
        "product_family": brand,
        "model": title,
        "product_category": category.get("value"),
        "product_subcategory": (category.get("parent") or {}).get("value"),
        "storage_capacity_gb": storage,
        "sku_variant_code": json_ld.get("sku"),

        "condition_grade_raw": json_ld.get("itemCondition") or infer_condition(title, description),

        "original_title": title,
        "original_description": description,
        "category": category.get("value"),
        "subcategory": (category.get("parent") or {}).get("value"),
        "listing_type": "fixed_price",
        "currency": item.get("price_currency_code") or "SEK",
        "current_asking_price": price,
        "listing_status": listing_status,
        "first_seen_at": datetime.now(timezone.utc).isoformat(),
        "listing_language": "sv",

        "confirmed_sold": False,
        "sale_confidence_score": sale_confidence,

        "country": "SE",
        "city": location.get("postalName"),
        "postal_code": location.get("postalCode"),
        "shipping_available": transactable.get("eligibleForShipping"),

        "professional_seller": bool(item_data.get("isWebstore")),
        "private_seller": not bool(item_data.get("isWebstore")),

        "image_urls": images if images else None,
        "image_count": len(images),

        "source_reliability_score": 0.65,
        "data_completeness_score": None,
        "last_verified_at": datetime.now(timezone.utc).isoformat(),
        "data_schema_version": "schema-v1.0",
        "snapshot_timestamp": datetime.now(timezone.utc).isoformat(),

        "raw_json_location": None,
    }

    non_null = sum(1 for v in row.values() if v is not None)
    row["data_completeness_score"] = round(non_null / len(row), 2)

    return row, category.get("value")

def upload_raw_json(listing_id, detail):
    path = f"blocket/{listing_id}_{int(time.time())}.json"
    payload = json.dumps(detail).encode()
    supabase.storage.from_("raw-archive").upload(
        path, payload, {"content-type": "application/json"}
    )
    return f"supabase://raw-archive/{path}"

def has_changed(new_row, existing_row):
    if existing_row is None:
        return True
    return (
        new_row["current_asking_price"] != existing_row["current_asking_price"]
        or new_row["listing_status"] != existing_row["listing_status"]
    )

def run_once(query, max_items=50):
    results = api.search(query)
    inserted = skipped_category = skipped_missing = skipped_unchanged = 0

    for item in results["docs"][:max_items]:
        try:
            detail = api.get_ad(RecommerceAd(item["id"]))
            row, category_value = map_to_schema(item, detail)

            if category_value not in PHONE_CATEGORIES:
                skipped_category += 1
                continue

            if not row["current_asking_price"] or not row["brand"]:
                skipped_missing += 1
                print(f"Hoppar {item.get('id')}: saknar pris eller märke ({row['original_title']})")
                continue

            existing = (
                supabase.table("current_listings")
                .select("current_asking_price, listing_status")
                .eq("listing_id", row["listing_id"])
                .eq("source_platform", "Blocket")
                .limit(1)
                .execute()
            )
            existing_row = existing.data[0] if existing.data else None

            if has_changed(row, existing_row):
                row["raw_json_location"] = upload_raw_json(row["listing_id"], detail)
                supabase.table("historical_transactions").insert(row).execute()
                inserted += 1
            else:
                skipped_unchanged += 1

        except Exception as e:
            print(f"Fel på {item.get('id')}: {e}")

        time.sleep(0.5)

    print(f"Klar: {inserted} nya, {skipped_category} fel kategori (tillbehör m.m.), "
          f"{skipped_missing} saknar pris/märke, {skipped_unchanged} oförändrade.")

if __name__ == "__main__":
    run_once("iPhone 15")
