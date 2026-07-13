# -*- coding: utf-8 -*-
import os
import time
import httpx
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

APP_ID = os.environ["TRADERA_APP_ID"]
APP_KEY = os.environ["TRADERA_APP_KEY"]
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL")

NS = "{http://api.tradera.com}"

TRADERA_SEARCHES = [
    {"query": "iPhone 15", "max_pages": 3},
    {"query": "iPhone 14", "max_pages": 3},
    {"query": "iPhone 13", "max_pages": 3},
    {"query": "Samsung Galaxy S23", "max_pages": 2},
    {"query": "Samsung Galaxy S24", "max_pages": 2},
    {"query": "MacBook", "max_pages": 3},
    {"query": "PlayStation 5", "max_pages": 2},
    {"query": "Nintendo Switch", "max_pages": 2},
]

# ---------------- search ----------------

def search_tradera(query, page=1):
    url = "https://api.tradera.com/v3/searchservice.asmx/Search"
    params = {"appId": APP_ID, "appKey": APP_KEY, "query": query, "categoryId": 0, "pageNumber": page}
    response = httpx.get(url, params=params, timeout=20.0, follow_redirects=True)
    response.raise_for_status()
    return ET.fromstring(response.text)

def search_all_pages(query, max_pages=3):
    all_items = []
    for page in range(1, max_pages + 1):
        try:
            root = search_tradera(query, page=page)
        except Exception as e:
            print(f"Sökning misslyckades för '{query}' sida {page}: {e}")
            break
        items = root.findall(f"{NS}Items")
        if not items:
            break
        all_items.extend(items)
        total_pages_el = root.find(f"{NS}TotalNumberOfPages")
        if total_pages_el is not None and page >= int(total_pages_el.text):
            break
        time.sleep(0.3)
    return all_items

# ---------------- parsing ----------------

def get_attr(item_el, name):
    for tav in item_el.iter(f"{NS}TermAttributeValue"):
        name_el = tav.find(f"{NS}Name")
        if name_el is not None and name_el.text == name:
            values = [v.text for v in tav.findall(f"{NS}Values/{NS}string")]
            if values:
                return values[0]
    return None

def parse_search_item(item_el):
    def text(tag):
        el = item_el.find(f"{NS}{tag}")
        return el.text if el is not None else None

    item_id = text("Id")
    title = text("ShortDescription")
    description = text("LongDescription")
    buy_it_now = text("BuyItNowPrice")
    max_bid = text("MaxBid")
    next_bid = text("NextBid")
    bid_count = text("BidCount")
    has_bids = text("HasBids") == "true"
    is_ended = text("IsEnded") == "true"
    item_type = text("ItemType")
    item_url = text("ItemUrl")
    category_id = text("CategoryId")
    end_date = text("EndDate")
    seller_id = text("SellerId")
    seller_alias = text("SellerAlias")
    seller_rating = text("SellerDsrAverage")

    images = []
    for link in item_el.findall(f"{NS}ImageLinks/{NS}ImageLink"):
        fmt = link.find(f"{NS}Format")
        url_el = link.find(f"{NS}Url")
        if url_el is not None and fmt is not None and fmt.text == "normal":
            images.append(url_el.text)
    if not images:
        thumb = text("ThumbnailLink")
        if thumb:
            images = [thumb]

    brand = get_attr(item_el, "mobile_brand") or get_attr(item_el, "brand")
    model = get_attr(item_el, "mobile_model") or title
    condition = get_attr(item_el, "condition") or "Ej specificerat"
    storage_raw = get_attr(item_el, "mobile_disk_memory")

    is_auction = item_type != "PureBuyItNow"
    price = float(buy_it_now) if buy_it_now else (float(max_bid) if max_bid else None)

    return {
        "listing_id": f"tradera:{item_id}",
        "source_platform": "Tradera",
        "marketplace_url": item_url,
        "snapshot_id": int(time.time() * 1000) % 2_000_000_000,
        "record_type": "active_listing",
        "ingestion_method": "api",
        "brand": brand,
        "product_family": brand,
        "model": model,
        "product_category": category_id,  # raw numeric ID -- GetCategories lookup not built yet
        "condition_grade_raw": condition,
        "original_title": title,
        "original_description": description,
        "listing_type": "auction" if is_auction else "fixed_price",
        "currency": "SEK",
        "current_asking_price": price,
        "listing_status": "active",
        "first_seen_at": datetime.now(timezone.utc).isoformat(),
        "listing_language": "sv",
        "confirmed_sold": False,
        "sale_confidence_score": 0.5,
        "auction_bid_count": int(bid_count) if bid_count else None,
        "buy_it_now_price": float(buy_it_now) if buy_it_now else None,
        "country": "SE",
        "seller_rating": float(seller_rating) if seller_rating else None,
        "image_urls": images if images else None,
        "image_count": len(images),
        "source_reliability_score": 0.9,
        "data_completeness_score": None,
        "last_verified_at": datetime.now(timezone.utc).isoformat(),
        "data_schema_version": "schema-v1.0",
        "snapshot_timestamp": datetime.now(timezone.utc).isoformat(),
        "raw_json_location": None,
        "_end_date": end_date,  # internal use only, stripped before insert
        "_has_bids": has_bids,
        "_is_ended": is_ended,
    }

# ---------------- GetItem: only used to confirm resolution ----------------

def get_item_status(raw_item_id):
    url = "https://api.tradera.com/v3/publicservice.asmx/GetItem"
    params = {"appId": APP_ID, "appKey": APP_KEY, "itemId": raw_item_id}
    response = httpx.get(url, params=params, timeout=15.0, follow_redirects=True)
    response.raise_for_status()
    root = ET.fromstring(response.text)
    status_el = root.find(f"{NS}Status")
    ended = status_el.find(f"{NS}Ended").text == "true" if status_el is not None else False
    got_winner = status_el.find(f"{NS}GotWinner").text == "true" if status_el is not None else False
    max_bid_el = root.find(f"{NS}MaxBid")
    end_date_el = root.find(f"{NS}EndDate")
    return {
        "ended": ended,
        "got_winner": got_winner,
        "final_price": float(max_bid_el.text) if max_bid_el is not None else None,
        "end_date": end_date_el.text if end_date_el is not None else None,
    }

# ---------------- Supabase helpers (same pattern as Blocket) ----------------

def has_changed(new_row, existing_row):
    if existing_row is None:
        return True
    return (
        new_row["current_asking_price"] != existing_row["current_asking_price"]
        or new_row["listing_status"] != existing_row["listing_status"]
    )

def get_previously_active_ids(source_platform):
    res = (
        supabase.table("current_listings")
        .select("listing_id")
        .eq("source_platform", source_platform)
        .eq("listing_status", "active")
        .execute()
    )
    return {row["listing_id"] for row in res.data}

def resolve_disappeared(previously_active_ids, seen_today_ids):
    """Unlike Blocket's 404-only check, Tradera's GetItem tells us the true
    outcome directly: confirmed sold (Ended + GotWinner), ended-unsold, or
    still active (just missed by search pagination -- do nothing)."""
    candidates = previously_active_ids - seen_today_ids
    sold, removed, still_active = 0, 0, 0

    for listing_id in candidates:
        raw_id = listing_id.split(":")[-1]
        existing = (
            supabase.table("current_listings")
            .select("*")
            .eq("listing_id", listing_id)
            .eq("source_platform", "Tradera")
            .limit(1)
            .execute()
        )
        if not existing.data:
            continue
        old_row = existing.data[0]

        try:
            status = get_item_status(raw_id)
        except Exception:
            status = {"ended": True, "got_winner": False, "final_price": None, "end_date": None}
        time.sleep(0.3)

        if not status["ended"]:
            still_active += 1
            continue  # genuinely still active, just missed by pagination -- self-heals

        new_row = {**old_row}
        for key in ("transaction_id", "inserted_at", "updated_at"):
            new_row.pop(key, None)
        new_row["snapshot_id"] = int(time.time() * 1000) % 2_000_000_000
        new_row["last_verified_at"] = datetime.now(timezone.utc).isoformat()
        new_row["snapshot_timestamp"] = datetime.now(timezone.utc).isoformat()

        if status["got_winner"]:
            new_row["listing_status"] = "sold"
            new_row["confirmed_sold"] = True
            new_row["final_sale_price"] = status["final_price"]
            new_row["sale_date"] = status["end_date"]
            new_row["sale_confidence_score"] = 0.95
            new_row["record_type"] = "auction_close"
            sold += 1
        else:
            new_row["listing_status"] = "removed"
            new_row["confirmed_sold"] = False
            new_row["sale_confidence_score"] = 0.3
            new_row["record_type"] = "delisted_unknown"
            removed += 1

        supabase.table("historical_transactions").insert(new_row).execute()

    return sold, removed, still_active, len(candidates)

# ---------------- main run ----------------

def run_all_tradera_searches(searches):
    previously_active_ids = get_previously_active_ids("Tradera")
    seen_today_ids = set()
    run_stats = []

    for search_config in searches:
        query = search_config["query"]
        max_pages = search_config.get("max_pages", 3)
        items = search_all_pages(query, max_pages=max_pages)
        inserted = skipped_missing = skipped_unchanged = 0

        for item_el in items:
            try:
                row = parse_search_item(item_el)
                row.pop("_end_date", None)
                row.pop("_has_bids", None)
                is_ended = row.pop("_is_ended", False) if "_is_ended" in row else False
            except Exception as e:
                print(f"Fel vid tolkning: {e}")
                continue

            if not row["current_asking_price"] or not row["brand"]:
                skipped_missing += 1
                continue

            seen_today_ids.add(row["listing_id"])

            existing = (
                supabase.table("current_listings")
                .select("current_asking_price, listing_status, first_seen_at")
                .eq("listing_id", row["listing_id"])
                .eq("source_platform", "Tradera")
                .limit(1)
                .execute()
            )
            existing_row = existing.data[0] if existing.data else None

            non_null = sum(1 for v in row.values() if v is not None)
            row["data_completeness_score"] = round(non_null / len(row), 2)

            if has_changed(row, existing_row):
                if existing_row is not None:
                    row["first_seen_at"] = existing_row["first_seen_at"]
                supabase.table("historical_transactions").insert(row).execute()
                inserted += 1
            else:
                skipped_unchanged += 1

        print(f"'{query}': {inserted} nya, {skipped_missing} saknar pris/märke, {skipped_unchanged} oförändrade.")
        run_stats.append({"query": query, "inserted": inserted, "skipped_missing": skipped_missing, "skipped_unchanged": skipped_unchanged})

    sold, removed, still_active, total_candidates = resolve_disappeared(previously_active_ids, seen_today_ids)
    print(f"Upplösning: {sold} bekräftat sålda, {removed} avslutade utan köpare, "
          f"{still_active} fortfarande aktiva (missade av paginering), av {total_candidates} kandidater.")

    if RESEND_API_KEY and NOTIFY_EMAIL:
        total_new = sum(s["inserted"] for s in run_stats)
        html = f"<h2>Tradera-skrapning</h2><p>{total_new} nya annonser. {sold} sålda, {removed} avslutade utan köpare.</p>"
        httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={"from": "Tradera Scraper <onboarding@resend.dev>", "to": [NOTIFY_EMAIL],
                  "subject": f"Tradera: {total_new} nya, {sold} sålda", "html": html},
        )

if __name__ == "__main__":
    run_all_tradera_searches(TRADERA_SEARCHES)
