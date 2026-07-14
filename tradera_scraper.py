# -*- coding: utf-8 -*-
import os
import re
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
    {"query": "Google Pixel", "max_pages": 2},
    {"query": "OnePlus", "max_pages": 2},
    {"query": "MacBook", "max_pages": 3},
    {"query": "Dell XPS", "max_pages": 2},
    {"query": "Lenovo ThinkPad", "max_pages": 2},
    {"query": "Canon EOS", "max_pages": 2},
    {"query": "Sony Alpha", "max_pages": 2},
    {"query": "PlayStation 5", "max_pages": 2},
    {"query": "Xbox Series", "max_pages": 2},
    {"query": "Nintendo Switch", "max_pages": 2},
    {"query": "iPad", "max_pages": 2},
    {"query": "Samsung Galaxy Tab", "max_pages": 2},
    {"query": "Bose hörlurar", "max_pages": 2},
    {"query": "Samsung TV", "max_pages": 2},
    {"query": "DJI drönare", "max_pages": 2},
]

ACCESSORY_KEYWORDS = [
    "skärmskydd", "skal", "case", "härdat glas", "laddare", "kabel",
    "hölje", "fodral", "screen protector", "väska", "adapter",
    "reservdel", "reparation", "linsskydd", "objektivlock",
    "kamera", "headset", "handkontroll", "kontroll", "styrspak",
    "mystery chest", "samlarobjekt", "figur",
]

def looks_like_accessory(title):
    if not title:
        return False
    lowered = title.lower()
    return any(kw in lowered for kw in ACCESSORY_KEYWORDS)

HARDWARE_QUERY_TERMS = {
    "PlayStation 5": ["playstation 5", "ps5"],
    "Xbox Series": ["xbox series"],
    "Nintendo Switch": ["nintendo switch", "switch oled", "switch lite"],
}

def looks_like_platform_tag_suffix(title, terms):
    if not title:
        return False
    lowered = title.lower()
    for term in terms:
        pattern = r'[-–(]\s*' + re.escape(term)
        if re.search(pattern, lowered):
            return True
    return False

def looks_like_genuine_hardware(query, title):
    terms = HARDWARE_QUERY_TERMS.get(query)
    if not terms:
        return True
    return not looks_like_platform_tag_suffix(title, terms)

BRAND_KEYWORDS = {
    "Apple": ["iphone", "macbook", "ipad", "imac", "apple watch", "airpods"],
    "Samsung": ["samsung", "galaxy"],
    "Google": ["pixel"],
    "Sony": ["xperia", "playstation", "ps5", "ps4", "alpha", "wh-1000"],
    "OnePlus": ["oneplus"],
    "Dell": ["dell", "xps"],
    "HP": ["hp pavilion", "hp spectre", "hp envy"]
