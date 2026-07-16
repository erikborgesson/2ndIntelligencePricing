import os
import json
import re
import time
import httpx
from datetime import datetime, timezone, timedelta
from supabase import create_client
from blocket_api import BlocketAPI, RecommerceAd
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL")
api = BlocketAPI()
# ---- Confirmed-good category filters. Add new ones only after verifying
# them via discovery mode below -- never guess a category string blind. ----
ALLOWED_CATEGORIES = {
    "Mobiltelefoner": {"Apple", "Samsung", "Google", "Sony", "OnePlus", "Huawei", "Xiaomi", "Nokia", "Motorola"},
    "Laptops": {"Apple", "Dell", "HP", "Lenovo", "Asus"},
    "Systemkameror": {"Canon", "Nikon", "Sony", "Fujifilm"},
    "Hybridkameror": {"Canon", "Nikon", "Sony", "Fujifilm"},
    "Spelkonsoler": {"Sony", "Microsoft", "Nintendo"},
    "Surfplattor och läsplattor": {"Apple", "Samsung"},
    "Hörlurar": {"Bose"},
    "TV": {"Samsung"},
    "Drönare": {"DJI"},
    "Träningsklockor och aktivitetsarmband": {"Apple", "Garmin"},
    "Klockor och armbandsur": {"Apple", "Garmin"},
    "Högtalare": {"Sonos"},
    "Hemmabiosystem": {"Sonos"},
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
    # Höga träffar, rena kategorier -- djupare sökning
    {"query": "iPhone 15", "max_pages": 3},
    {"query": "iPhone 14", "max_pages": 3},
    {"query": "iPhone 13", "max_pages": 3},
    # Tidigare kända luckor -- lades till för att undvika permanent förlorad
    # prishistorik för generationer som redan omsätts på andrahandsmarknaden,
    # även om run_product_matching_v2 ännu inte har egna regex-grenar för dem
    # (matchningen är retroaktiv, så det tas igen den dagen SQL-katalogen byggs ut).
    {"query": "iPhone 12", "max_pages": 3},
    {"query": "iPhone 11", "max_pages": 2},
    {"query": "iPhone SE", "max_pages": 2},
    {"query": "Samsung Galaxy S23", "max_pages": 3},
    {"query": "Samsung Galaxy S24", "max_pages": 3},
    {"query": "Samsung Galaxy S22", "max_pages": 2},
    {"query": "Samsung Galaxy S21", "max_pages": 2},
    {"query": "Google Pixel", "max_pages": 3},
    {"query": "OnePlus", "max_pages": 2},
    {"query": "MacBook", "max_pages": 3},
    {"query": "Dell XPS", "max_pages": 2},
    {"query": "Lenovo ThinkPad", "max_pages": 3},
    {"query": "Canon EOS", "max_pages": 3},
    {"query": "Sony Alpha", "max_pages": 1},  # nästan uttömd, bara 1 ny senast
    # Mest brus -- grundare sökning
    {"query": "PlayStation 5", "max_pages": 3},
    {"query": "Xbox Series", "max_pages": 3},
    {"query": "Nintendo Switch", "max_pages": 3},
    # Nyligen bekräftade rena kategorier -- aktiveras nu på riktigt
    {"query": "iPad", "max_pages": 3},
    {"query": "Samsung Galaxy Tab", "max_pages": 2},
    {"query": "Bose hörlurar", "max_pages": 3},
    {"query": "Samsung TV", "max_pages": 3},
    {"query": "DJI drönare", "max_pages": 3},
    # Fortfarande obeslutade -- kvar i upptäcktsläge, grunt för att spara tid
    {"query": "Apple Watch", "max_pages": 2},
    {"query": "Sonos", "max_pages": 2},
    {"query": "Garmin klocka", "max_pages": 2},
    # Borttagen: "AirPods" -- söktermen matchar inte Blockets kategoristruktur (2 träffar totalt över flera körningar)

]
def send_summary_email(run_stats, marked_removed, total_disappeared):
    if not RESEND_API_KEY or not NOTIFY_EMAIL:
        print("RESEND_API_KEY eller NOTIFY_EMAIL saknas -- hoppar över mejl.")
        return
    total_new = sum(s["inserted"] for s in run_stats)
    rows_html = "".join(
        f"<tr><td>{s['query']}</td><td>{s['inserted']}</td><td>{s['skipped_category']}</td>"
        f"<td>{s['skipped_missing']}</td><td>{s['skipped_unchanged']}</td></tr>"
        for s in run_stats
    )
    html = f"""
    <h2>Blocket-skrapning: sammanfattning</h2>
    <p><b>{total_new}</b> nya annonser totalt.
       <b>{marked_removed}/{total_disappeared}</b> annonser borttagna.</p>
    <table border="1" cellpadding="4" cellspacing="0">
      <tr><th>Sökning</th><th>Nya</th><th>Fel kategori</th><th>Saknar data</th><th>Oförändrade</th></tr>
      {rows_html}
    </table>
    """
    response = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
        json={
            "from": "Blocket Scraper <onboarding@resend.dev>",
            "to": [NOTIFY_EMAIL],
            "subject": f"Blocket-skrapning: {total_new} nya, {marked_removed} borttagna",
            "html": html,
        },
    )
    if response.status_code >= 400:
        print(f"Mejl misslyckades: {response.status_code} {response.text}")
    else:
        print("Sammanfattningsmejl skickat.")
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
def search_all_pages(query, max_pages=3, max_retries=3):
    """Blocket's own API caps results per call regardless of max_items --
    real pagination is required to get more than ~50 results.
    FIX: httpx.get() had no timeout and no retry handling -- a single
    network hiccup (ReadTimeout) raised an uncaught exception straight out
    of this function, which killed the whole script (skipping every later
    search, the removal check, product matching, and the summary email).
    Now it retries transient network errors with backoff, and if a page
    still fails after max_retries it logs and returns what it has instead
    of crashing the run."""
    all_docs = []
    for page in range(1, max_pages + 1):
        for attempt in range(1, max_retries + 1):
            try:
                response = httpx.get(
                    "https://blocket-api.se/v1/search",
                    params={"query": query, "page": page},
                    timeout=30.0,
                )
                break
            except (httpx.TimeoutException, httpx.TransportError) as e:
                if attempt == max_retries:
                    print(f"Sökning '{query}' sida {page} misslyckades efter {max_retries} försök: {e}")
                    return all_docs
                wait = 2 ** attempt
                print(f"Nätverksfel på '{query}' sida {page} (försök {attempt}/{max_retries}): {e} -- väntar {wait}s")
                time.sleep(wait)
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
def get_previously_active_ids(source_platform, stale_after_hours=24):
    """Only consider listings 'previously active' if they were confirmed
    active reasonably recently -- not just 'ever active at some point'.
    This keeps the comparison fair as the accumulated dataset grows."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=stale_after_hours)).isoformat()
    res = (
        supabase.table("current_listings")
        .select("listing_id, last_verified_at")
        .eq("source_platform", source_platform)
        .eq("listing_status", "active")
        .gte("last_verified_at", cutoff)
        .execute()
    )
    return {row["listing_id"] for row in res.data}
def mark_disappeared_as_removed(source_platform, previously_active_ids, seen_today_ids):
    """A listing that wasn't seen in today's search results is only
    confirmed removed if a direct check of its ad page also fails
    (404/not found). This avoids flapping caused by search pagination
    simply not reaching every active listing every run."""
    candidates = previously_active_ids - seen_today_ids
    if previously_active_ids and len(candidates) / len(previously_active_ids) > 0.5:
        print(f"OBS: {len(candidates)}/{len(previously_active_ids)} annonser syntes inte i dagens sökningar "
              f"-- kontrollerar varje kandidat direkt innan något markeras borttaget.")
    marked = 0
    checked = 0
    for listing_id in candidates:
        raw_id = listing_id.split(":")[-1]  # "blocket:24738525" -> "24738525"
        checked += 1
        try:
            api.get_ad(RecommerceAd(raw_id))
            continue  # still exists -- just missed by pagination, do nothing
        except Exception:
            pass  # genuinely gone (404 or similar) -- proceed to mark removed
        finally:
            time.sleep(0.3)
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
    return marked, checked
def run_all_searches(searches, max_items=50):
    previously_active_ids = get_previously_active_ids("Blocket")
    all_seen_today_ids = set()
    run_stats = []
    for search_config in searches:
        query = search_config["query"]
        discovery = search_config.get("discovery_mode", False)
        max_pages = search_config.get("max_pages", 3)
        try:
            all_docs = search_all_pages(query, max_pages=max_pages)
        except Exception as e:
            # Extra skyddsnät: oavsett vad som går fel i sökningen ska en
            # trasig sökterm inte stoppa resten av körningen (borttagningskontroll,
            # produktmatchning, mejl).
            print(f"Sökning '{query}' misslyckades helt, hoppar över: {e}")
            continue
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
                all_seen_today_ids.add(row["listing_id"])
                existing = (
                    supabase.table("current_listings")
                    .select("current_asking_price, listing_status, first_seen_at")
                    .eq("listing_id", row["listing_id"])
                    .eq("source_platform", "Blocket")
                    .limit(1)
                    .execute()
                )
                existing_row = existing.data[0] if existing.data else None
                if has_changed(row, existing_row):
                    if existing_row is not None:
                        row["first_seen_at"] = existing_row["first_seen_at"]
                    row["raw_json_location"] = upload_raw_json(row["listing_id"], detail)
                    supabase.table("historical_transactions").insert(row).execute()
                    inserted += 1
                else:
                    skipped_unchanged += 1
            except Exception as e:
                print(f"Fel på {item.get('id')}: {e}")
            time.sleep(0.5)
        if discovery:
            print(f"[UPPTÄCKTSLÄGE] '{query}' -> {category_counts}")
        else:
            print(f"'{query}': {inserted} nya, {skipped_category} fel kategori, "
                  f"{skipped_missing} saknar pris/märke, {skipped_unchanged} oförändrade.")
            run_stats.append({
                "query": query, "inserted": inserted, "skipped_category": skipped_category,
                "skipped_missing": skipped_missing, "skipped_unchanged": skipped_unchanged,
            })
    marked_removed, total_disappeared = mark_disappeared_as_removed(
        "Blocket", previously_active_ids, all_seen_today_ids
    )
    print(f"Borttagningskontroll: {marked_removed}/{total_disappeared} verkligen borttagna markerade.")
    try:
        # FIX: "execute_with_retry" var aldrig definierad/importerad i den här
        # filen -- anropet kraschade med NameError. Kör RPC:n direkt istället.
        match_result = supabase.rpc("run_product_matching_v2").execute()
        print(f"Produktmatchning: {match_result.data}")
    except Exception as e:
        print(f"Produktmatchning misslyckades: {e}")
    send_summary_email(run_stats, marked_removed, total_disappeared)
if __name__ == "__main__":
    run_all_searches(SEARCHES)
