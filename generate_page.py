import os
import re
import json
import html
import urllib.parse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests
from google import genai
from google.genai import types

# Initialize Gemini Client (reads GEMINI_API_KEY from environment)
client = genai.Client()

TEAM_LOGOS = {
    "Minnesota Twins": "https://a.espncdn.com/i/teamlogos/mlb/500/min.png",
    "Minnesota Vikings": "https://a.espncdn.com/i/teamlogos/nfl/500/min.png",
    "Minnesota Wild": "https://a.espncdn.com/i/teamlogos/nhl/500/min.png",
    "Minnesota Timberwolves": "https://a.espncdn.com/i/teamlogos/nba/500/min.png",
}

ESPN_TEAM_CONFIG = {
    "Minnesota Twins": {
        "sport": "baseball",
        "league": "mlb",
        "espn_slug": "mlb",
        "espn_team_id": "9",
        "team_url": "https://www.espn.com/mlb/team/_/name/min/minnesota-twins",
        "schedule_url": "https://www.espn.com/mlb/team/schedule/_/name/min",
        "aliases": {"minnesota twins", "twins", "min"},
    },
    "Minnesota Vikings": {
        "sport": "football",
        "league": "nfl",
        "espn_slug": "nfl",
        "espn_team_id": "16",
        "team_url": "https://www.espn.com/nfl/team/_/name/min/minnesota-vikings",
        "schedule_url": "https://www.espn.com/nfl/team/schedule/_/name/min",
        "aliases": {"minnesota vikings", "vikings", "min"},
    },
    "Minnesota Wild": {
        "sport": "hockey",
        "league": "nhl",
        "espn_slug": "nhl",
        "espn_team_id": "30",
        "team_url": "https://www.espn.com/nhl/team/_/name/min/minnesota-wild",
        "schedule_url": "https://www.espn.com/nhl/team/schedule/_/name/min",
        "aliases": {"minnesota wild", "wild", "min"},
    },
    "Minnesota Timberwolves": {
        "sport": "basketball",
        "league": "nba",
        "espn_slug": "nba",
        "espn_team_id": "16",
        "team_url": "https://www.espn.com/nba/team/_/name/min/minnesota-timberwolves",
        "schedule_url": "https://www.espn.com/nba/team/schedule/_/name/min",
        "aliases": {"minnesota timberwolves", "timberwolves", "wolves", "min"},
    },
}

ESPN_LEAGUE_FEEDS = [
    ("football", "nfl", "https://www.espn.com/nfl/"),
    ("basketball", "nba", "https://www.espn.com/nba/"),
    ("hockey", "nhl", "https://www.espn.com/nhl/"),
    ("baseball", "mlb", "https://www.espn.com/mlb/"),
]

# AirDNA benchmarks for Loveland, OH ($178 ADR, 60% occupancy -> ~$3,251/mo)
AIRDNA_LOVELAND_ADR = 178.0
AIRDNA_LOVELAND_OCCUPANCY = 0.60
AVG_DAYS_PER_MONTH = 365.25 / 12.0

_RESOLVED_LOGO_CACHE = {}


def get_team_logo_url(team_name):
    key = str(team_name or "").strip()
    if key in _RESOLVED_LOGO_CACHE:
        return _RESOLVED_LOGO_CACHE[key]

    fallback = TEAM_LOGOS.get(key, "")
    cfg = ESPN_TEAM_CONFIG.get(key)
    if not cfg:
        _RESOLVED_LOGO_CACHE[key] = fallback
        return fallback

    api_url = (
        f"https://site.api.espn.com/apis/site/v2/sports/"
        f"{cfg['sport']}/{cfg['league']}/teams/{cfg['espn_team_id']}"
    )
    try:
        resp = requests.get(api_url, headers=HEADERS, timeout=5)
        if resp.status_code == 200:
            logos = (resp.json().get("team") or {}).get("logos") or []
            for entry in logos:
                href = str(entry.get("href") or "").strip()
                rels = entry.get("rel") or []
                if href and ("default" in rels or "full" in rels):
                    _RESOLVED_LOGO_CACHE[key] = href
                    return href
            if logos and logos[0].get("href"):
                href = str(logos[0]["href"]).strip()
                _RESOLVED_LOGO_CACHE[key] = href
                return href
    except Exception:
        pass

    _RESOLVED_LOGO_CACHE[key] = fallback
    return fallback


def get_month_labels():
    now = datetime.now(ZoneInfo("America/New_York"))
    current_month = now.strftime("%B %Y")
    if now.month == 12:
        next_dt = now.replace(year=now.year + 1, month=1, day=1)
    else:
        next_dt = now.replace(month=now.month + 1, day=1)
    next_month = next_dt.strftime("%B %Y")
    today_str = now.strftime("%A, %B %d, %Y")
    return today_str, current_month, next_month


def build_prompt():
    today_str, current_month, next_month = get_month_labels()
    return f"""
You are generating a personal 4-hour digest for someone in their 40s/50s living in Loveland, OH (ZIP 45140).
Today's date is {today_str}. Current month is {current_month}; next month is {next_month}.
Use Google Search to find current, real-time information as of today.

CRITICAL URL RULES:
- Every URL MUST be copied verbatim from your Google Search grounding results. Never guess, construct, or shorten URLs.
- Every URL must point directly to the specific article, box score, schedule, property listing, or event page—NEVER to a site homepage or generic section root.
- For every item in "sports_news", "url" MUST be an ESPN.com URL (from www.espn.com) pointing to that specific ESPN sports story, and "source" MUST be "ESPN". Search specifically on espn.com for today's top sports headlines.
- For every item in "single_family_homes" and "multifamily_homes", "url" MUST be a Zillow.com listing URL (from www.zillow.com, such as a /homedetails/ property page) found via Google Search on zillow.com. Search specifically on site:zillow.com for active listings in 45140 and nearby communities.

Return ONLY valid JSON matching this exact schema (no markdown fences, no extra text):
{{
  "events_this_month": [
    {{
      "title": "Event Name",
      "category": "Concert | Karaoke | Comedy | Real Estate | Zoo/Aquarium | Museum | Community",
      "date_time": "Date & Time",
      "location": "Venue & City",
      "why_it_fits": "1-sentence note tailored for someone in their 40s/50s",
      "url": "https://..."
    }}
  ],
  "events_next_month": [
    {{
      "title": "Event Name",
      "category": "Concert | Karaoke | Comedy | Real Estate | Zoo/Aquarium | Museum | Community",
      "date_time": "Date & Time",
      "location": "Venue & City",
      "why_it_fits": "1-sentence note tailored for someone in their 40s/50s",
      "url": "https://..."
    }}
  ],
  "sports": [
    {{
      "team": "Minnesota Twins | Minnesota Vikings | Minnesota Wild | Minnesota Timberwolves",
      "status": "In-Season | Offseason",
      "top_headline": "Most important current news headline for this specific team",
      "headline_source": "Publisher name (e.g., Star Tribune, ESPN, The Athletic)",
      "headline_url": "https://...",
      "last_game_result": "Final Score (e.g., Vikings 24 - Bears 17) or Offseason note",
      "top_performers": "Key stat lines from last game",
      "box_score_url": "https://www.espn.com/...",
      "next_game": "Opponent, Date, Time (ET)",
      "schedule_url": "https://..."
    }}
  ],
  "sports_news": [
    {{
      "headline": "ESPN Sports Headline",
      "source": "ESPN",
      "summary": "2-sentence objective summary of the ESPN sports story",
      "url": "https://www.espn.com/..."
    }}
  ],
  "single_family_homes": [
    {{
      "address": "Street Address, City, OH ZIP",
      "price": "$XXX,XXX",
      "annual_tax_paid": "$X,XXX",
      "zillow_rent_estimate": "$X,XXX",
      "airdna_adr": "$XXX",
      "airdna_occupancy": "60%",
      "listing_status": "New Listing | Price Cut / Value Deal",
      "strategy": "Short-Term Rental (Airbnb) | Long-Term Rental | Value-Add",
      "metrics_pitch": "Beds/baths/sqft, days on market or recent price cut details, and rental/equity upside near Loveland bike trail, downtown, or commuter routes",
      "url": "https://www.zillow.com/homedetails/..."
    }}
  ],
  "multifamily_homes": [
    {{
      "address": "Street Address, City, OH ZIP",
      "price": "$X,XXX,XXX",
      "annual_tax_paid": "$XX,XXX",
      "zillow_rent_estimate": "$X,XXX",
      "airdna_adr": "$XXX",
      "airdna_occupancy": "60%",
      "listing_status": "New Listing | Price Cut / Value Deal",
      "strategy": "Duplex | Triplex | Quadplex | Small Apartment Building",
      "metrics_pitch": "Unit count, total beds/baths/sqft, days on market or price drop, and estimated gross rent/cap rate angle",
      "url": "https://www.zillow.com/homedetails/..."
    }}
  ],
  "local_news": [
    {{
      "headline": "Headline",
      "source": "Neutral source name (e.g., Loveland Local News, WLWT, WKRC, WVXU, AP)",
      "summary": "2-sentence objective summary",
      "url": "https://..."
    }}
  ],
  "national_news": [
    {{
      "headline": "Headline",
      "source": "Politically neutral source (e.g., Associated Press, Reuters, PBS NewsHour, NPR, Straight Arrow News)",
      "summary": "2-sentence objective summary",
      "url": "https://..."
    }}
  ],
  "quote": {{
    "text": "An uplifting, grounded quote offering comfort and resilience for someone navigating grief and loss.",
    "author": "Author Name",
    "source_url": "https://..."
  }}
}}

Strict Requirements:
1. "events_this_month": Top 10 upcoming events near 45140 (Loveland / Greater Cincinnati) occurring in the remainder of {current_month} (on or after {today_str}) spanning concerts, karaoke, comedy, real estate networking, Cincinnati Zoo, Newport Aquarium, local museums, and community events suited for ages 40-59.
2. "events_next_month": Top 10 upcoming events near 45140 (Loveland / Greater Cincinnati) occurring in {next_month} spanning concerts, karaoke, comedy, real estate networking, Cincinnati Zoo, Newport Aquarium, local museums, and community events suited for ages 40-59.
3. "sports": Cover ONLY these 4 Minnesota pro teams: Minnesota Twins, Minnesota Vikings, Minnesota Wild, and Minnesota Timberwolves. Include each team's single top current news headline with source and direct article URL, plus ONLY final score and top performers for the most recent completed game, an ESPN.com box score URL ("box_score_url" on espn.com), and direct team schedule URL.
4. "sports_news": Top 5 major sports headlines today sourced strictly from ESPN.com ("source": "ESPN", with direct "url" links on www.espn.com).
5. "single_family_homes": Exactly 3 active for-sale single-family homes on Zillow.com in 45140 (or immediately adjacent Loveland/Maineville/Milford) with a STRICT MAXIMUM asking price of $500,000. Focus specifically on brand-new listings OR older listings with recent price cuts / strong value-per-sqft deals suitable for STR (Airbnb) or LTR investment. Every "url" MUST be on zillow.com.
6. "multifamily_homes": Exactly 3 active for-sale multifamily properties (duplex, triplex, quadplex, or small multifamily) on Zillow.com in 45140 or immediately adjacent Greater Cincinnati communities (e.g., Milford, Mason, Lebanon, Northeast Cincinnati) with a STRICT MAXIMUM asking price of $1,500,000. Focus specifically on brand-new listings OR older listings with recent price cuts / strong cashflow potential. Every "url" MUST be on zillow.com.
7. "local_news": Top 5 local news stories near Loveland / Warren, Clermont, and Hamilton counties from neutral, fact-based outlets (e.g., Loveland Local News, WLWT, WVXU).
8. "national_news": Top 5 US national stories strictly from politically neutral wire/public outlets (Associated Press, Reuters, PBS, NPR).
9. "quote": 1 deeply comforting, uplifting quote about enduring loss and carrying love forward.
"""


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "over",
    "after", "amid", "says", "said", "will", "have", "has", "are", "was",
    "were", "been", "about", "near", "top", "news", "game", "vs", "at", "in", "on", "of", "to"
}


def extract_keywords(text):
    words = re.findall(r"[a-zA-Z0-9]{3,}", str(text or "").lower())
    return [w for w in words if w not in STOPWORDS]


def is_espn_domain(url):
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
        return host == "espn.com" or host.endswith(".espn.com")
    except Exception:
        return False


def is_zillow_domain(url):
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
        return host == "zillow.com" or host.endswith(".zillow.com")
    except Exception:
        return False


def slugify_address(address):
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", str(address or "").strip()).strip("-")
    return cleaned or "45140-loveland-oh"


def resolve_zillow_property_url(address, candidate_url, grounding_urls):
    addr_tokens = [
        t.lower() for t in re.findall(r"[a-zA-Z0-9]+", str(address or ""))
        if t.lower() not in {"oh", "loveland", "milford", "maineville", "mason", "lebanon", "cincinnati"}
    ]

    cand = str(candidate_url or "").strip()
    if cand and is_zillow_domain(cand):
        return cand, True

    zillow_grounded = [u for u in grounding_urls if is_zillow_domain(u)]
    for u in zillow_grounded:
        u_lower = u.lower()
        if addr_tokens and sum(1 for tok in addr_tokens if tok in u_lower) >= 2:
            return u, True

    for u in zillow_grounded:
        if "/homedetails/" in u.lower():
            return u, True

    slug = slugify_address(address)
    return f"https://www.zillow.com/homes/{slug}_rb/", True


def extract_espn_article_url(article_obj):
    if not isinstance(article_obj, dict):
        return ""
    links = article_obj.get("links") or {}
    for key in ("web", "mobile", "api"):
        entry = links.get(key) or {}
        href = entry.get("href")
