import os
import json
import re
import time
import httpx 
from datetime import datetime, timezone
from supabase import create_client
from blocket_api import BlocketAPI, RecommerceAd

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

api = BlocketAPI()

# ---- Confirmed-good category filters. Add new ones only after verifying
# them via discovery mode below -- never guess a category string blind. ----
ALLOWED_CATEGORIES = {
    "Mobiltelefoner": {"Apple", "Samsung", "Google", "Sony", "OnePlus", "Huawei", "Xiaomi", "Nokia", "Motorola"},
    "Laptops": {"Apple", "Dell", "HP", "Lenovo", "Asus"},
    "Systemkameror": {"Canon", "Nikon", "Sony", "Fujifilm"},
    "Hybridkameror": {"Canon", "Nikon", "Sony", "Fujifilm"},
    "Spelkonsoler": {"Sony", "Microsoft", "Nintendo"},
    # Nya, bekräftade efter upptäcktsläge körts en gång -- se instruktion nedan
}

BRAND_KEYWORDS = {
    "Apple": ["iphone", "macbook", "ipad", "imac", "apple watch", "airpods"],
    "Samsung": ["samsung", "galaxy"],
    "Google": ["pixel"],
    "Sony": ["xperia", "playstation", "ps5", "ps4", "alpha", "wh-1000"],
    "OnePlus": ["oneplus"],
    "Huawei": ["huawei"],
    "Xiaomi": ["xiaomi", "redmi", "poco"],
    "Nokia": ["nokia"],
    "Motorola": ["motorola", "moto g"],
    "Microsoft": ["xbox", "surface"],
    "Nintendo": ["nintendo switch"],
    "Dell": ["dell", "xps"],
    "HP": ["hp pavilion", "hp spectre", "hp envy"],
    "Lenovo": ["lenovo", "thinkpad"],
    "Asus": ["asus", "rog"],
    "Canon": ["canon eos", "canon"],
    "Nikon": ["nikon"],
    "Fujifilm": ["fujifilm", "fuji x"],
    "Bose": ["bose"],
    "Sonos": ["sonos"],
    "DJI": ["dji", "mavic", "phantom"],
    "GoPro": ["gopro"],
    "LG": ["lg oled", "lg tv"],
    "Garmin": ["garmin"],
}

CONDITION_KEYWORDS = {
    "Ny / oanvänd": ["helt ny", "oanvänd", "nyskick", "ny skick"],
    "Mycket bra skick": ["mycket fint skick", "mycket bra skick", "toppskick"],
    "Bra skick": ["fint skick", "bra skick", "fungerar perfekt"],
    "Begagnad": ["begagnad", "använd", "sliten"],
}

# ---- Searches to run. Add more here as you expand categories/brands. ----
SEARCHES = [
    # ---- Redan bekräftade kategorier -- fler modeller inom samma kategori ----
    {"query": "iPhone 15"},
    {"query": "iPhone 14"},
    {"query": "iPhone 13"},
    {"query": "Samsung Galaxy S23"},
    {"query": "Samsung Galaxy S24"},
    {"query": "Google Pixel"},
    {"query": "OnePlus"},
    {"query": "MacBook"},
    {"query": "Dell XPS"},
    {"query": "Lenovo ThinkPad"},
    {"query": "Canon EOS"},
    {"query": "Sony Alpha"},
    {"query": "PlayStation 5"},
    {"query": "Xbox Series"},
    {"query": "Nintendo Switch"},

    # ---- Nya kategorier -- upptäcktsläge, sätts INTE in i databasen än ----
    {"query": "iPad", "discovery_mode": True},
    {"query": "Samsung Galaxy Tab", "discovery_mode": True},
    {"query": "Apple Watch", "discovery_mode": True},
    {"query": "AirPods", "discovery_mode": True},
    {"query": "Bose hörlurar", "discovery_mode": True},
    {"query": "Sonos", "discovery_mode": True},
    {"query": "Samsung TV", "discovery_mode": True},
    {"query": "DJI drönare", "discovery_mode": True},
    {"query": "Garmin klocka", "discovery_mode": True},
]

def infer_condition(title, description):
    text = f"{title or ''} {description or ''}".lower()
    for condition, keywords in CONDITION_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return condition
    return "Ej specificerat"

def infer_brand(title, description):
    text = f"{title or ''} {description or ''}".lower()
    for brand, keywords in BRAND_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return brand
    return None

def parse_storage_gb(raw):
    if not raw:
        return None
    match = re.search(r"([\d.]+)\s*(GB|TB)", str(raw), re.IGNORECASE)
    if not match:
        return None
    value, unit = float(match.group(1)), match.group(2).upper()
    return int(value * 1024) if unit == "TB" else int(value)

def find_price(item, root):
    if item.get("price_amount") is not None:
        return item["price_amount"]
    offer_price = (root.get("jsonLd", {}) or {}).get("offers", {}).get("price")
    try:
        return float(offer_price) if offer_price is not None else None
    except (TypeError, ValueError):
        return None

def search_all_pages(query, max_pages=3):
    """Blocket's own API caps results per call regardless of max_items --
    real pagination is required to get more than ~50 results."""
    all_docs = []
    for page in range(1, max_pages + 1):
        response = httpx.get(
            "https://blocket-api.se/v1/search",
            params={"query": query, "page": page},
        )
        docs = response.json().get("docs", [])
        if not docs:
            break
        all_docs.extend(docs)
    return all_docs

def map_to_schema(item, detail):
    root = detail.get("loaderData", {}).get("item-recommerce", {}) or {}
    item_data = root.get("itemData", {}) or {}
    json_ld = root.get("jsonLd", {}) or {}
    offers = json_ld.get("offers", {}) or {}
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
    condition = json_ld.get("itemCondition") or infer_condition(title, description)

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
        "condition_grade_raw": condition,
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
    supabase.storage.from_("raw-archive").upload(
        path, json.dumps(detail).encode(), {"content-type": "application/json"}
    )
    return f"supabase://raw-archive/{path}"

def has_changed(new_row, existing_row):
    if existing_row is None:
        return True
    return (
        new_row["current_asking_price"] != existing_row["current_asking_price"]
        or new_row["listing_status"] != existing_row["listing_status"]
    )

def get_previously_active_ids(source_platform):
    """Everything we currently think is still active for this source."""
    res = (
        supabase.table("current_listings")
        .select("listing_id")
        .eq("source_platform", source_platform)
        .eq("listing_status", "active")
        .execute()
    )
    return {row["listing_id"] for row in res.data}

def mark_disappeared_as_removed(source_platform, previously_active_ids, seen_today_ids):
    """The core history fix: anything active before but missing from today's
    results gets a new 'removed' row -- never deleted, never overwritten."""
    disappeared = previously_active_ids - seen_today_ids
    marked = 0
    for listing_id in disappeared:
        existing = (
            supabase.table("current_listings")
            .select("*")
            .eq("listing_id", listing_id)
            .eq("source_platform", source_platform)
            .limit(1)
            .execute()
        )
        if not existing.data:
            continue
        old_row = existing.data[0]
        new_row = {**old_row}
        for key in ("transaction_id", "inserted_at", "updated_at"):
            new_row.pop(key, None)
        new_row["snapshot_id"] = int(time.time() * 1000) % 2_000_000_000
        new_row["listing_status"] = "removed"
        new_row["record_type"] = "delisted_unknown"
        new_row["sale_confidence_score"] = 0.2
        new_row["last_verified_at"] = datetime.now(timezone.utc).isoformat()
        new_row["snapshot_timestamp"] = datetime.now(timezone.utc).isoformat()
        supabase.table("historical_transactions").insert(new_row).execute()
        marked += 1
    return marked, len(disappeared)

def run_all_searches(searches, max_items=150):
    previously_active_ids = get_previously_active_ids("Blocket")  # fetched ONCE, before anything runs
    all_seen_today_ids = set()  # accumulated across ALL searches in this run

    for search_config in searches:
        query = search_config["query"]
        discovery = search_config.get("discovery_mode", False)
        all_docs = search_all_pages(query, max_pages=3)
        inserted = skipped_category = skipped_missing = skipped_unchanged = 0
        category_counts = {}

        for item in all_docs[:max_items]:
            try:
                detail = api.get_ad(RecommerceAd(item["id"]))
                row, category_value = map_to_schema(item, detail)
                category_counts[category_value] = category_counts.get(category_value, 0) + 1

                if discovery:
                    continue

                allowed_brands = ALLOWED_CATEGORIES.get(category_value)
                if allowed_brands is None or (row["brand"] and allowed_brands and row["brand"] not in allowed_brands):
                    skipped_category += 1
                    continue
                if not row["current_asking_price"] or not row["brand"]:
                    skipped_missing += 1
                    continue

                all_seen_today_ids.add(row["listing_id"])  # <-- accumulated globally, not per-search

                existing = (
                    supabase.table("current_listings")
                    .select("current_asking_price, listing_status, first_seen_at")  # lägg till first_seen_at
                    .eq("listing_id", row["listing_id"])
                    .eq("source_platform", "Blocket")
                    .limit(1)
                    .execute()
                )
                existing_row = existing.data[0] if existing.data else None
                
                if has_changed(row, existing_row):
                    if existing_row is not None:
                        row["first_seen_at"] = existing_row["first_seen_at"]  # bevara det sanna ursprungsdatumet
                    row["raw_json_location"] = upload_raw_json(row["listing_id"], detail)
                    supabase.table("historical_transactions").insert(row).execute()
                    inserted += 1

            except Exception as e:
                print(f"Fel på {item.get('id')}: {e}")
            time.sleep(0.5)

        if discovery:
            print(f"[UPPTÄCKTSLÄGE] '{query}' -> {category_counts}")
        else:
            print(f"'{query}': {inserted} nya, {skipped_category} fel kategori, "
                  f"{skipped_missing} saknar pris/märke, {skipped_unchanged} oförändrade.")

    # ---- The ONE, correct removal pass -- after every search has run ----
    marked_removed, total_disappeared = mark_disappeared_as_removed(
        "Blocket", previously_active_ids, all_seen_today_ids
    )
    print(f"Borttagningskontroll: {marked_removed}/{total_disappeared} verkligen borttagna markerade.")

if __name__ == "__main__":
    run_all_searches(SEARCHES)
