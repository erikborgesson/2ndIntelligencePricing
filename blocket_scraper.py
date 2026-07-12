import os
import json
import hashlib
import time
from datetime import datetime, timezone
from supabase import create_client
from blocket_api import BlocketAPI, RecommerceAd

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]  # never in code, always env var
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

api = BlocketAPI()

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

    # Best-effort sold/removed inference -- STILL NEEDS the verification test
    # from last time (check disposed/is_inactive/availability against an ad
    # you know is closed). Until confirmed, default stays conservative.
    disposed = item_data.get("disposed")
    is_inactive = meta.get("isInactive")
    availability = offers.get("availability")
    if disposed or is_inactive or (availability and "OutOfStock" in str(availability)):
        listing_status = "removed"          # NOT "sold" -- Blocket can't confirm a sale
        record_type = "delisted_unknown"
        confirmed_sold = False
        sale_confidence = 0.2
    else:
        listing_status = "active"
        record_type = "active_listing"
        confirmed_sold = False
        sale_confidence = 0.3               # low, by design -- Blocket never confirms sales

    return {
        "listing_id": f"blocket:{item.get('id')}",
        "source_platform": "Blocket",
        "marketplace_url": item.get("canonical_url"),
        "snapshot_id": int(time.time() * 1000) % 2_000_000_000,
        "record_type": record_type,
        "ingestion_method": "scrape",

        "brand": item.get("brand") or json_ld.get("brand"),
        "product_family": item.get("brand") or json_ld.get("brand"),  # refine once product catalog matching runs
        "model": item_data.get("title") or item.get("heading"),
        "product_category": category.get("value"),
        "product_subcategory": (category.get("parent") or {}).get("value"),
        "storage_capacity_gb": item.get("memory_size"),
        "sku_variant_code": json_ld.get("sku"),

        "condition_grade_raw": json_ld.get("itemCondition"),

        "original_title": item_data.get("title") or item.get("heading"),
        "original_description": item_data.get("description"),
        "category": category.get("value"),
        "subcategory": (category.get("parent") or {}).get("value"),
        "listing_type": "fixed_price",
        "currency": item.get("price_currency_code") or "SEK",
        "current_asking_price": item.get("price_amount"),
        "listing_status": listing_status,
        "first_seen_at": datetime.now(timezone.utc).isoformat(),
        "listing_language": "sv",

        "confirmed_sold": confirmed_sold,
        "sale_confidence_score": sale_confidence,

        "country": "SE",
        "city": location.get("postalName"),
        "postal_code": location.get("postalCode"),
        "shipping_available": transactable.get("eligibleForShipping"),

        "professional_seller": bool(item_data.get("isWebstore")),
        "private_seller": not bool(item_data.get("isWebstore")),

        "image_urls": images[0] if images else None,
        "image_count": len(images),

        "source_reliability_score": 0.65,
        "data_completeness_score": None,  # computed below
        "last_verified_at": datetime.now(timezone.utc).isoformat(),
        "data_schema_version": "schema-v1.0",
        "snapshot_timestamp": datetime.now(timezone.utc).isoformat(),

        "raw_json_location": None,  # filled in after upload, see below
    }

def upload_raw_json(listing_id, detail):
    """Store the raw API response in Supabase Storage so bad parsing logic
    is never a reason to re-hit Blocket's API."""
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
    inserted, skipped = 0, 0

    for item in results["docs"][:max_items]:
        try:
            detail = api.get_ad(RecommerceAd(item["id"]))
            row = map_to_schema(item, detail)

            non_null = sum(1 for v in row.values() if v is not None)
            row["data_completeness_score"] = round(non_null / len(row), 2)

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
                skipped += 1

        except Exception as e:
            print(f"Fel på {item.get('id')}: {e}")

        time.sleep(0.5)  # polite pause between detail calls

    print(f"Klar: {inserted} nya rader, {skipped} oförändrade (hoppade över).")

if __name__ == "__main__":
    run_once("iPhone 15")
