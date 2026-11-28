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

_RESOLVED_LOGO_CACHE = {}


def get_genai_client():
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if api_key:
        return genai.Client(api_key=api_key)
    return genai.Client()


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
5. "single_family_homes": Exactly 10 active for-sale single-family homes on Zillow.com in 45140 (or immediately adjacent Loveland/Maineville/Milford) with a STRICT MAXIMUM asking price of $500,000. Focus specifically on brand-new listings OR older listings with recent price cuts / strong value-per-sqft deals suitable for STR (Airbnb) or LTR investment. Every "url" MUST be on zillow.com.
6. "multifamily_homes": Exactly 10 active for-sale multifamily properties (duplex, triplex, quadplex, or small multifamily) on Zillow.com in 45140 or immediately adjacent Greater Cincinnati communities (e.g., Milford, Mason, Lebanon, Northeast Cincinnati) with a STRICT MAXIMUM asking price of $1,500,000. Focus specifically on brand-new listings OR older listings with recent price cuts / strong cashflow potential. Every "url" MUST be on zillow.com.
7. "local_news": Top 5 local news stories near Loveland / Warren, Clermont, and Hamilton counties from neutral, fact-based outlets (e.g., Loveland Local News, WLWT, WVXU).
8. "national_news": Top 5 US national stories strictly from politically neutral wire/public outlets (Associated Press, Reuters, PBS, NPR).
9. "quote": 1 deeply comforting, uplifting quote about enduring loss and carrying love forward.
"""


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
        if href and is_espn_domain(href):
            return str(href).strip()
    return ""


def fetch_espn_news_items(sport, league, team_id=None, limit=15):
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/news?limit={limit}"
    if team_id:
        url += f"&team={urllib.parse.quote_plus(str(team_id))}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=6)
        if resp.status_code != 200:
            return []
        payload = resp.json()
        items = []
        for art in payload.get("articles", []):
            art_url = extract_espn_article_url(art)
            if art_url:
                items.append({
                    "headline": str(art.get("headline") or "").strip(),
                    "description": str(art.get("description") or "").strip(),
                    "url": art_url,
                })
        return items
    except Exception:
        return []


def best_espn_match(query_text, candidates, fallback_url):
    if not candidates:
        return fallback_url
    q_words = set(extract_keywords(query_text))
    if not q_words:
        return candidates[0]["url"]

    best_url = fallback_url
    best_score = 0
    for item in candidates:
        haystack = f"{item.get('headline', '')} {item.get('description', '')}".lower()
        score = sum(1 for w in q_words if w in haystack)
        if score > best_score:
            best_score = score
            best_url = item["url"]

    return best_url if best_score > 0 else candidates[0]["url"]


def get_espn_top_sports_news(gemini_sports_news):
    pool = []
    for sport, league, _ in ESPN_LEAGUE_FEEDS:
        pool.extend(fetch_espn_news_items(sport, league, limit=6))

    results = []
    used_urls = set()

    for item in (gemini_sports_news or [])[:5]:
        headline = str(item.get("headline") or "").strip()
        summary = str(item.get("summary") or "").strip()
        cand = str(item.get("url") or "").strip()

        if cand and is_espn_domain(cand) and cand not in used_urls:
            resolved_url = cand
        else:
            available = [p for p in pool if p["url"] not in used_urls] or pool
            resolved_url = best_espn_match(
                f"{headline} {summary}",
                available,
                "https://www.espn.com/",
            )

        used_urls.add(resolved_url)
        results.append({
            "headline": headline or "ESPN Top Sports Story",
            "source": "ESPN",
            "summary": summary,
            "url": resolved_url,
            "url_verified": is_espn_domain(resolved_url),
        })

    if not results and pool:
        for p in pool[:5]:
            results.append({
                "headline": p["headline"],
                "source": "ESPN",
                "summary": p["description"],
                "url": p["url"],
                "url_verified": True,
            })

    return results


def get_espn_team_headline_url(team_name, headline_text, candidate_url=""):
    cfg = ESPN_TEAM_CONFIG.get(str(team_name or "").strip())
    if candidate_url and is_espn_domain(candidate_url):
        return candidate_url.strip()
    if not cfg:
        return "https://www.espn.com/"

    team_articles = fetch_espn_news_items(
        cfg["sport"], cfg["league"], team_id=cfg["espn_team_id"], limit=8
    )
    if not team_articles:
        team_articles = fetch_espn_news_items(cfg["sport"], cfg["league"], limit=12)

    return best_espn_match(
        f"{team_name} {headline_text}",
        team_articles,
        cfg["team_url"],
    )


def get_espn_box_score_url(team_name, candidate_url=""):
    cfg = ESPN_TEAM_CONFIG.get(str(team_name or "").strip())
    if not cfg:
        return candidate_url if is_espn_domain(candidate_url) else "https://www.espn.com/"

    sport = cfg["sport"]
    league = cfg["league"]
    espn_slug = cfg["espn_slug"]
    aliases = cfg["aliases"]

    now_et = datetime.now(ZoneInfo("America/New_York"))
    for days_ago in range(0, 11):
        date_str = (now_et - timedelta(days=days_ago)).strftime("%Y%m%d")
        api_url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard?dates={date_str}"
        try:
            resp = requests.get(api_url, headers=HEADERS, timeout=6)
            if resp.status_code != 200:
                continue
            payload = resp.json()
            for event in payload.get("events", []):
                event_id = str(event.get("id") or "").strip()
                if not event_id:
                    continue
                status_type = (
                    event.get("status", {})
                    .get("type", {})
                    .get("completed", False)
                )
                competitions = event.get("competitions", [])
                competitors = competitions[0].get("competitors", []) if competitions else []
                team_matched = False
                for comp in competitors:
                    t_info = comp.get("team", {})
                    names = {
                        str(t_info.get("displayName") or "").lower(),
                        str(t_info.get("shortDisplayName") or "").lower(),
                        str(t_info.get("name") or "").lower(),
                        str(t_info.get("abbreviation") or "").lower(),
                    }
                    if names & aliases:
                        team_matched = True
                        break
                if team_matched and status_type:
                    return f"https://www.espn.com/{espn_slug}/boxscore/_/gameId/{event_id}"
        except Exception:
            continue

    if candidate_url and is_espn_domain(candidate_url) and ("boxscore" in candidate_url or "gameId" in candidate_url or "/game/" in candidate_url):
        return candidate_url.strip()

    return cfg["team_url"]


def get_espn_schedule_url(team_name, candidate_url=""):
    cfg = ESPN_TEAM_CONFIG.get(str(team_name or "").strip())
    if not cfg:
        return candidate_url if is_espn_domain(candidate_url) else "https://www.espn.com/"

    if candidate_url and is_espn_domain(candidate_url):
        return candidate_url.strip()

    sched_api = (
        f"https://site.api.espn.com/apis/site/v2/sports/"
        f"{cfg['sport']}/{cfg['league']}/teams/{cfg['espn_team_id']}/schedule"
    )
    try:
        resp = requests.get(sched_api, headers=HEADERS, timeout=6)
        if resp.status_code == 200:
            payload = resp.json()
            for ev in payload.get("events", []):
                completed = (
                    ev.get("status", {})
                    .get("type", {})
                    .get("completed", False)
                )
                if not completed:
                    for link in ev.get("links", []):
                        href = link.get("href")
                        if href and is_espn_domain(href):
                            return str(href).strip()
    except Exception:
        pass

    return cfg["schedule_url"]


def verify_link(url, reference_text, grounding_urls, require_espn=False):
    fallback_query = urllib.parse.quote_plus(str(reference_text or "news").strip())
    fallback_url = (
        "https://www.espn.com/"
        if require_espn
        else f"https://www.google.com/search?q={fallback_query}"
    )

    if not url or not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return fallback_url, False

    clean_url = url.strip()
    if require_espn and not is_espn_domain(clean_url):
        return fallback_url, False

    is_grounded = clean_url in grounding_urls

    try:
        resp = requests.get(clean_url, headers=HEADERS, timeout=5, allow_redirects=True)
        final_url = resp.url

        if require_espn and not is_espn_domain(final_url):
            return fallback_url, False

        if resp.status_code in (404, 410, 451) or resp.status_code >= 500:
            return fallback_url, False

        if resp.status_code == 403:
            return (clean_url, True) if is_grounded else (fallback_url, False)

        page_text = resp.text[:150000].lower()
        soft_404_phrases = [
            "page not found",
            "404 not found",
            "article could not be found",
            "listing is no longer available",
            "off market",
        ]
        if any(phrase in page_text for phrase in soft_404_phrases):
            return fallback_url, False

        keywords = extract_keywords(reference_text)
        if keywords:
            matches = sum(1 for kw in keywords if kw in page_text)
            match_ratio = matches / len(keywords)
            if matches == 0 or (not is_grounded and match_ratio < 0.25):
                return fallback_url, False

        return final_url, True

    except requests.RequestException:
        if is_grounded:
            return clean_url, True
        return fallback_url, False


def parse_price_number(val):
    cleaned = re.sub(r"[^\d.]", "", str(val or ""))
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0


def parse_percentage(val, default_val):
    raw = str(val or "").strip()
    if not raw:
        return default_val
    num = parse_price_number(raw)
    if num <= 0:
        return default_val
    return num / 100.0 if num > 1.0 else num


def estimate_monthly_tax(listing, price):
    actual_annual_tax = parse_price_number(listing.get("annual_tax_paid"))
    if actual_annual_tax > 0:
        return actual_annual_tax / 12.0, True

    pitch = str(listing.get("metrics_pitch") or "").lower()
    address = str(listing.get("address") or "").lower()
    combined = f"{address} {pitch}"

    if "warren" in combined or "lebanon" in combined or "mason" in combined:
        est_rate = 0.0122
    elif "clermont" in combined or "milford" in combined:
        est_rate = 0.0145
    else:
        est_rate = 0.0165

    return (price * est_rate) / 12.0, False


def get_zillow_ltr_rent(listing, is_multifamily=False):
    for key in ("zillow_rent_estimate", "rent_zestimate", "zestimate_rent"):
        val = parse_price_number(listing.get(key))
        if val > 0:
            return val, True

    pitch = str(listing.get("metrics_pitch") or "")
    z_match = re.search(r"rent\s*zestimate[^\d$]*\$?\s*([\d,]+)", pitch, re.I)
    if z_match:
        val = parse_price_number(z_match.group(1))
        if val > 0:
            return val, True

    if is_multifamily:
        unit_match = re.search(r"(\d+)\s*-?\s*unit", pitch.lower())
        units = int(unit_match.group(1)) if unit_match else 2
        per_unit_rent = 1650.0 if units == 2 else 1550.0
        return units * per_unit_rent, False

    bed_match = re.search(r"(\d+)\s*(?:bd|bed|br)", pitch.lower())
    beds = int(bed_match.group(1)) if bed_match else 3
    base_ltr = 1850.0 if beds <= 2 else (2160.0 if beds == 3 else 2750.0)
    return base_ltr, False


def get_airdna_str_rent(listing, is_multifamily=False):
    direct_monthly = parse_price_number(listing.get("airdna_monthly_revenue"))
    if direct_monthly > 0:
        return direct_monthly, True

    adr = parse_price_number(listing.get("airdna_adr"))
    has_custom_adr = adr > 0
    if not has_custom_adr:
        adr = AIRDNA_LOVELAND_ADR

    occ = parse_percentage(listing.get("airdna_occupancy"), AIRDNA_LOVELAND_OCCUPANCY)
    has_custom_occ = bool(str(listing.get("airdna_occupancy") or "").strip())

    units = 1
    if is_multifamily:
        pitch = str(listing.get("metrics_pitch") or "").lower()
        strategy = str(listing.get("strategy") or "").lower()
        unit_match = re.search(r"(\d+)\s*-?\s*unit", pitch)
        units = int(unit_match.group(1)) if unit_match else (
            4 if "quadplex" in strategy else 3 if "triplex" in strategy else 2
        )

    gross_monthly = adr * AVG_DAYS_PER_MONTH * occ * units
    return gross_monthly, (has_custom_adr or has_custom_occ)


def is_str_strategy(listing):
    strategy = str(listing.get("strategy") or "").lower()
    pitch = str(listing.get("metrics_pitch") or "").lower()
    return (
        "short-term" in strategy
        or "airbnb" in strategy
        or "str" in strategy
        or "short-term" in pitch
        or "airbnb" in pitch
    )


def estimate_monthly_cash_flow_details(listing, is_multifamily=False, down_payment_pct=0.20, annual_rate=0.073):
    price = parse_price_number(listing.get("price"))
    if price <= 0:
        return None, "Est. Cash Flow: N/A"

    if is_str_strategy(listing):
        gross_rent, has_custom_airdna = get_airdna_str_rent(listing, is_multifamily=is_multifamily)
        vacancy_rate = 0.15
        non_tax_opex_rate = 0.28
        rent_source = "AirDNA Est." if has_custom_airdna else "AirDNA Loveland Avg"
    else:
        gross_rent, has_zillow_rent = get_zillow_ltr_rent(listing, is_multifamily=is_multifamily)
        vacancy_rate = 0.06 if is_multifamily else 0.05
        non_tax_opex_rate = 0.24 if is_multifamily else 0.22
        rent_source = "Zillow Rent Est." if has_zillow_rent else "Zillow Rent Baseline"

    monthly_tax, is_actual_tax = estimate_monthly_tax(listing, price)
    effective_rent = gross_rent * (1.0 - vacancy_rate)
    other_opex = gross_rent * non_tax_opex_rate

    loan_amount = price * (1.0 - down_payment_pct)
    monthly_rate = annual_rate / 12.0
    n_payments = 360
    if monthly_rate > 0:
        pi = loan_amount * (monthly_rate * (1 + monthly_rate) ** n_payments) / (
            (1 + monthly_rate) ** n_payments - 1
        )
    else:
        pi = loan_amount / n_payments

    cash_flow = effective_rent - monthly_tax - other_opex - pi
    sign = "+" if cash_flow >= 0 else "-"
    tax_label = f"Actual Tax ${round(monthly_tax):,}/mo" if is_actual_tax else f"Est. Tax ${round(monthly_tax):,}/mo"
    label = f"Est. Cash Flow: {sign}${abs(round(cash_flow)):,}/mo ({rent_source}, {tax_label})"
    return cash_flow, label


def estimate_monthly_cash_flow(listing, is_multifamily=False, down_payment_pct=0.20, annual_rate=0.073):
    _, label = estimate_monthly_cash_flow_details(
        listing,
        is_multifamily=is_multifamily,
        down_payment_pct=down_payment_pct,
        annual_rate=annual_rate,
    )
    return label


def rank_top_cash_flow_properties(sfh_listings, mfh_listings, top_n=10):
    candidates = []
    for item in (sfh_listings or [])[:10]:
        cf_val, cf_label = estimate_monthly_cash_flow_details(item, is_multifamily=False)
        if cf_val is not None:
            candidates.append({
                "listing": item,
                "property_type": "Single-Family",
                "cash_flow_value": cf_val,
                "cash_flow_label": cf_label,
            })

    for item in (mfh_listings or [])[:10]:
        cf_val, cf_label = estimate_monthly_cash_flow_details(item, is_multifamily=True)
        if cf_val is not None:
            candidates.append({
                "listing": item,
                "property_type": "Multifamily",
                "cash_flow_value": cf_val,
                "cash_flow_label": cf_label,
            })

    candidates.sort(key=lambda x: x["cash_flow_value"], reverse=True)
    return candidates[:top_n]


def validate_digest_links(data, grounding_urls):
    for key in ("events_this_month", "events_next_month"):
        for e in data.get(key, []):
            ref = f"{e.get('title', '')} {e.get('location', '')}"
            e["url"], e["url_verified"] = verify_link(e.get("url"), ref, grounding_urls)

    for s in data.get("sports", []):
        team = s.get("team", "")
        raw_headline_url = s.get("headline_url", "")
        if is_espn_domain(raw_headline_url):
            s["headline_url"] = get_espn_team_headline_url(
                team, s.get("top_headline", ""), raw_headline_url
            )
            s["headline_verified"] = is_espn_domain(s["headline_url"])
        else:
            verified_hl, ok_hl = verify_link(
                raw_headline_url, f"{team} {s.get('top_headline', '')}", grounding_urls
            )
            if ok_hl:
                s["headline_url"] = verified_hl
                s["headline_verified"] = True
            else:
                s["headline_url"] = get_espn_team_headline_url(
                    team, s.get("top_headline", ""), ""
                )
                s["headline_verified"] = is_espn_domain(s["headline_url"])

        direct_box = get_espn_box_score_url(team, s.get("box_score_url", ""))
        s["box_score_url"] = direct_box
        s["box_verified"] = is_espn_domain(direct_box)

        direct_sched = get_espn_schedule_url(team, s.get("schedule_url", ""))
        s["schedule_url"] = direct_sched
        s["schedule_verified"] = is_espn_domain(direct_sched)

    data["sports_news"] = get_espn_top_sports_news(data.get("sports_news", []))

    for prop_key in ("single_family_homes", "multifamily_homes"):
        for r in data.get(prop_key, []):
            r["url"], r["url_verified"] = resolve_zillow_property_url(
                r.get("address", ""), r.get("url", ""), grounding_urls
            )

    for n in data.get("local_news", []):
        n["url"], n["url_verified"] = verify_link(
            n.get("url"), f"{n.get('headline', '')} {n.get('source', '')}", grounding_urls
        )

    for n in data.get("national_news", []):
        n["url"], n["url_verified"] = verify_link(
            n.get("url"), f"{n.get('headline', '')} {n.get('source', '')}", grounding_urls
        )

    q = data.get("quote", {})
    if q:
        q["source_url"], q["url_verified"] = verify_link(
            q.get("source_url"), f"{q.get('author', '')} quote", grounding_urls
        )

    return data


def extract_json_object(raw_text):
    text = str(raw_text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end >= start:
        return text[start:end + 1]
    return text


def fetch_digest():
    genai_client = get_genai_client()
    response = genai_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=build_prompt(),
        config=types.GenerateContentConfig(
            tools=[{"google_search": {}}],
            thinking_config=types.ThinkingConfig(thinking_level="medium"),
        ),
    )

    grounding_urls = set()
    try:
        for candidate in response.candidates or []:
            meta = getattr(candidate, "grounding_metadata", None)
            for chunk in getattr(meta, "grounding_chunks", []) or []:
                web = getattr(chunk, "web", None)
                if web and getattr(web, "uri", None):
                    grounding_urls.add(web.uri.strip())
    except Exception:
        pass

    json_str = extract_json_object(response.text)
    data = json.loads(json_str)
    return validate_digest_links(data, grounding_urls)


def esc(val):
    return html.escape(str(val or ""))


def link_tag(url, label, verified=True):
    suffix = "" if verified else " <span class='search-tag'>[Search]</span>"
    return f'<a href="{esc(url)}" target="_blank" rel="noopener">{label}</a>{suffix}'


def team_logo_tag(team_name):
    name = str(team_name or "").strip()
    logo_url = get_team_logo_url(name)
    if not logo_url:
        return ""
    return (
        f'<img class="team-logo" src="{esc(logo_url)}" '
        f'alt="{esc(name)} official logo" width="28" height="28" loading="lazy">'
    )


def render_calendar_html(calendar_events, empty_message="No events scheduled."):
    if not calendar_events:
        return f'<li class="list-item calendar-item"><span class="meta">{esc(empty_message)}</span></li>'
    return "".join(f"""
        <li class="list-item calendar-item">
            <span class="cal-title">{esc(ev.get('summary'))}</span>
            <span class="badge cal-time">{esc(ev.get('time_label'))}</span>
        </li>
    """ for ev in calendar_events)


def render_property_list(listings, is_multifamily=False):
    trimmed = (listings or [])[:10]
    return "".join(f"""
        <li class="list-item">
            <div class="row-between">
                <strong>{link_tag(r.get('url'), esc(r.get('address')), r.get('url_verified', True))}</strong>
                <span class="price">{esc(r.get('price'))}</span>
            </div>
            <div class="meta-line">
                <span class="badge deal-badge">{esc(r.get('listing_status'))}</span>
                <span class="badge">{esc(r.get('strategy'))}</span>
                <span class="badge">{esc(estimate_monthly_cash_flow(r, is_multifamily=is_multifamily))}</span>
            </div>
            <p>{esc(r.get('metrics_pitch'))}</p>
        </li>
    """ for r in trimmed)


def render_top_cash_flow_list(ranked_items):
    if not ranked_items:
        return '<li class="list-item"><span class="meta">No cash flow estimates available.</span></li>'
    rows = []
    for idx, entry in enumerate(ranked_items[:10], start=1):
        r = entry["listing"]
        rows.append(f"""
        <li class="list-item">
            <div class="row-between">
                <strong>#{idx} &bull; {link_tag(r.get('url'), esc(r.get('address')), r.get('url_verified', True))}</strong>
                <span class="price">{esc(r.get('price'))}</span>
            </div>
            <div class="meta-line">
                <span class="badge deal-badge">{esc(entry.get('property_type'))}</span>
                <span class="badge">{esc(entry.get('strategy'))}</span>
                <span class="badge">{esc(entry.get('cash_flow_label'))}</span>
            </div>
            <p>{esc(r.get('metrics_pitch'))}</p>
        </li>
        """)
    return "".join(rows)


def render_event_list(events):
    trimmed = (events or [])[:10]
    if not trimmed:
        return '<li class="list-item event-item"><span class="meta">No upcoming events listed.</span></li>'
    return "".join(f"""
        <li class="list-item event-item">
            <strong>{link_tag(e.get('url'), esc(e.get('title')), e.get('url_verified', True))}</strong>
            <span class="badge event-time">{esc(e.get('date_time'))}</span>
        </li>
    """ for e in trimmed)


def render_news_list(items):
    return "".join(f"""
        <li class="list-item news-row">
            <details class="news-item">
                <summary class="news-summary">
                    <span class="news-title">
                        <strong>{link_tag(n.get('url'), esc(n.get('headline')), n.get('url_verified', True))}</strong>
                        <span class="source">({esc(n.get('source'))})</span>
                    </span>
                </summary>
                <p class="news-lead">{esc(n.get('summary'))}</p>
            </details>
        </li>
    """ for n in (items or []))


def build_html(data, today_calendar_events=None, tomorrow_calendar_events=None):
    now_et = datetime.now(ZoneInfo("America/New_York")).strftime("%A, %B %d, %Y at %I:%M %p %Z")
    _, current_month, next_month = get_month_labels()

    today_calendar_html = render_calendar_html(
        today_calendar_events or [],
        empty_message="No events scheduled for today.",
    )
    tomorrow_calendar_html = render_calendar_html(
        tomorrow_calendar_events or [],
        empty_message="No events scheduled for tomorrow.",
    )
    events_this_html = render_event_list(data.get("events_this_month", []))
    events_next_html = render_event_list(data.get("events_next_month", []))

    sports_html = "".join(f"""
        <li class="list-item">
            <div class="row-between">
                <div class="team-title">{team_logo_tag(s.get('team'))}<strong>{esc(s.get('team'))}</strong></div>
                <span class="badge">{esc(s.get('status'))}</span>
            </div>
            <p class="team-headline">
                <strong>Top Headline:</strong> {link_tag(s.get('headline_url'), esc(s.get('top_headline')), s.get('headline_verified', True))}
                <span class="source">({esc(s.get('headline_source'))})</span>
            </p>
            <p><strong>Final:</strong> {esc(s.get('last_game_result'))} &mdash; {link_tag(s.get('box_score_url'), 'ESPN Box Score &rarr;', s.get('box_verified', True))}</p>
            <p><strong>Top Performers:</strong> {esc(s.get('top_performers'))}</p>
            <p class="meta"><strong>Next Game:</strong> {esc(s.get('next_game'))} &bull; {link_tag(s.get('schedule_url'), 'Schedule', s.get('schedule_verified', True))}</p>
        </li>
    """ for s in data.get("sports", []))

    sports_news_html = render_news_list(data.get("sports_news", []))
    sfh_list = (data.get("single_family_homes") or [])[:10]
    mfh_list = (data.get("multifamily_homes") or [])[:10]
    top_10_cash_flow = rank_top_cash_flow_properties(sfh_list, mfh_list, top_n=10)
    top_cf_html = render_top_cash_flow_list(top_10_cash_flow)
    sfh_html = render_property_list(sfh_list, is_multifamily=False)
    mfh_html = render_property_list(mfh_list, is_multifamily=True)
    local_html = render_news_list(data.get("local_news", []))
    national_html = render_news_list(data.get("national_news", []))

    q = data.get("quote", {})

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Paul's Daily Brief</title>
    <style>
        :root {{
            --bg: #0c0d10;
            --card: #16181d;
            --text: #f3f4f6;
            --muted: #9ca3af;
            --accent: #d1d5db;
            --link: #e5e7eb;
            --border: #2a2e37;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
            margin: 0;
            padding: 20px;
        }}
        .container {{ max-width: 960px; margin: 0 auto; }}
        header {{
            border-bottom: 2px solid var(--border);
            padding-bottom: 16px;
            margin-bottom: 24px;
        }}
        h1 {{ margin: 0; font-size: 1.75rem; color: var(--text); }}
        .updated {{ color: var(--muted); font-size: 0.9rem; margin-top: 4px; }}
        .quote-box {{
            background: var(--card);
            border-left: 4px solid var(--accent);
            border-top: 1px solid var(--border);
            border-right: 1px solid var(--border);
            border-bottom: 1px solid var(--border);
            padding: 18px 22px;
            border-radius: 8px;
            margin-bottom: 22px;
        }}
        .quote-box blockquote {{ margin: 0 0 8px 0; font-size: 1.1rem; font-style: italic; color: var(--text); }}
        .quote-box cite {{ color: var(--muted); font-size: 0.9rem; }}
        .calendar-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 16px;
            margin-bottom: 8px;
        }}
        .calendar-grid h2 {{
            margin-top: 8px;
            margin-bottom: 10px;
            font-size: 1.15rem;
        }}
        h2 {{
            margin-top: 32px;
            border-bottom: 1px solid var(--border);
            padding-bottom: 6px;
            color: var(--accent);
        }}
        h3.subsection {{
            margin: 20px 0 12px 0;
            font-size: 1.05rem;
            color: var(--muted);
        }}
        ul.item-list {{
            list-style: none;
            padding: 0;
            margin: 0;
        }}
        li.list-item {{
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 14px 16px;
            margin-bottom: 12px;
            box-shadow: 0 2px 6px rgba(0, 0, 0, 0.35);
        }}
        li.news-row {{
            padding: 9px 14px;
            margin-bottom: 8px;
        }}
        details.news-item {{
            margin: 0;
        }}
        summary.news-summary {{
            list-style: none;
            cursor: pointer;
            display: flex;
            align-items: baseline;
            gap: 8px;
        }}
        summary.news-summary::-webkit-details-marker {{
            display: none;
        }}
        summary.news-summary::before {{
            content: "▸";
            color: var(--muted);
            font-size: 0.85rem;
            line-height: 1;
            flex-shrink: 0;
            transition: transform 0.15s ease;
        }}
        details[open] > summary.news-summary::before {{
            content: "▾";
            color: var(--text);
        }}
        .news-title {{
            display: inline;
        }}
        p.news-lead {{
            margin: 8px 0 2px 18px !important;
            padding-top: 6px;
            border-top: 1px solid var(--border);
            font-size: 0.92rem !important;
            color: var(--muted) !important;
        }}
        li.calendar-item, li.event-item {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 8px;
            padding: 7px 12px;
            margin-bottom: 6px;
            border-radius: 6px;
            font-size: 0.9rem;
            line-height: 1.35;
        }}
        .cal-title {{
            font-weight: 600;
            color: var(--text);
        }}
        .cal-time, .event-time {{
            margin-right: 0;
            white-space: nowrap;
            font-size: 0.72rem;
        }}
        li.list-item p {{ margin: 6px 0 0 0; font-size: 0.95rem; color: var(--text); }}
        .team-title {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            font-size: 1.05rem;
            color: var(--text);
        }}
        .team-logo {{
            width: 28px;
            height: 28px;
            object-fit: contain;
            border-radius: 4px;
            flex-shrink: 0;
            display: block;
        }}
        .meta-line {{ margin-top: 6px; }}
        .team-headline {{
            background: #1f222a;
            border: 1px solid #373c47;
            padding: 8px 10px;
            border-radius: 6px;
            margin-top: 8px !important;
            font-size: 0.92rem;
            color: var(--text);
        }}
        .row-between {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }}
        .badge {{
            display: inline-block;
            background: #22262f;
            color: var(--muted);
            border: 1px solid #373c47;
            font-size: 0.75rem;
            font-weight: 600;
            padding: 2px 8px;
            border-radius: 999px;
            margin-right: 4px;
        }}
        .deal-badge {{
            background: #2c313c;
            color: var(--text);
        }}
        .search-tag {{
            font-size: 0.75rem;
            color: var(--muted);
            font-weight: normal;
        }}
        .price {{ font-weight: 700; color: var(--accent); white-space: nowrap; }}
        .meta {{ color: var(--muted) !important; font-size: 0.88rem; }}
        a {{ color: var(--link); text-decoration: none; }}
        a:hover {{ color: #ffffff; text-decoration: underline; }}
        .source {{ color: var(--muted); font-size: 0.85rem; margin-left: 6px; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Paul's Daily Brief</h1>
            <div class="updated">Last refreshed: {esc(now_et)} &bull; Auto-updates every 4 hours</div>
        </header>

        <section class="quote-box">
            <blockquote>&ldquo;{esc(q.get('text'))}&rdquo;</blockquote>
            <cite>&mdash; {link_tag(q.get('source_url'), esc(q.get('author')), q.get('url_verified', True))}</cite>
        </section>

        <div class="calendar-grid">
            <div>
                <h2>Today's Calendar</h2>
                <ul class="item-list">{today_calendar_html}</ul>
            </div>
            <div>
                <h2>Tomorrow's Calendar</h2>
                <ul class="item-list">{tomorrow_calendar_html}</ul>
            </div>
        </div>

        <h2>Minnesota Pro Sports</h2>
        <ul class="item-list">{sports_html}</ul>

        <h2>Top 5 Sports Headlines (ESPN)</h2>
        <ul class="item-list">{sports_news_html}</ul>

        <h2>Top 5 Local News (45140 / Greater Cincinnati)</h2>
        <ul class="item-list">{local_html}</ul>

        <h2>Top 5 National News (Neutral Sources)</h2>
        <ul class="item-list">{national_html}</ul>

        <h2>45140 Area Investment Real Estate</h2>
        <h3 class="subsection">Top 10 Cash-Flowing Properties (Ranked by Monthly Cash Flow)</h3>
        <ul class="item-list">{top_cf_html}</ul>

        <h3 class="subsection">Single-Family Homes (10 Listings &bull; Max $500,000 &bull; New &amp; Value Deals)</h3>
        <ul class="item-list">{sfh_html}</ul>

        <h3 class="subsection">Multifamily Properties (10 Listings &bull; Max $1,500,000 &bull; New &amp; Value Deals)</h3>
        <ul class="item-list">{mfh_html}</ul>

        <h2>Top 20 Events Near 45140</h2>
        <h3 class="subsection">This Month ({esc(current_month)})</h3>
        <ul class="item-list">{events_this_html}</ul>

        <h3 class="subsection">Next Month ({esc(next_month)})</h3>
        <ul class="item-list">{events_next_html}</ul>
    </div>
</body>
</html>
"""


def build_fallback_html(
    today_calendar_events=None,
    tomorrow_calendar_events=None,
    note="Awaiting next scheduled digest refresh.",
):
    fallback_data = {
        "events_this_month": [],
        "events_next_month": [],
        "sports": [],
        "sports_news": [],
        "single_family_homes": [],
        "multifamily_homes": [],
        "local_news": [
            {
                "headline": "Digest status notice",
                "source": "Paul's Daily Brief",
                "summary": note,
                "url": "[https://www.google.com/search?q=Loveland+OH+local+news](https://www.google.com/search?q=Loveland+OH+local+news)",
                "url_verified": False,
            }
        ],
        "national_news": [],
        "quote": {
            "text": "What we have once enjoyed we can never lose; all that we love deeply becomes a part of us.",
            "author": "Helen Keller",
            "source_url": "[https://www.google.com/search?q=Helen+Keller+quote](https://www.google.com/search?q=Helen+Keller+quote)",
            "url_verified": False,
        },
    }
    return build_html(
        fallback_data,
        today_calendar_events=today_calendar_events,
        tomorrow_calendar_events=tomorrow_calendar_events,
    )


def write_html_file(path, content):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def ensure_index_html_exists(
    path="index.html",
    today_calendar_events=None,
    tomorrow_calendar_events=None,
):
    file_exists = os.path.isfile(path)
    has_content = file_exists and os.path.getsize(path) > 0

    if not has_content:
        print(f"{path} did not exist or was empty; creating initial HTML page.")
        fallback_html = build_fallback_html(
            today_calendar_events=today_calendar_events,
            tomorrow_calendar_events=tomorrow_calendar_events,
            note="Initial page created automatically because index.html was missing. Will populate with live digest data.",
        )
        write_html_file(path, fallback_html)
        return False

    return True


if __name__ == "__main__":
    today_calendar = [
        {"summary": "Paul Work Holiday", "time_label": "All Day (Nov 26–27)"},
        {"summary": "Marketplace pickup slack line $15", "time_label": "12:00 PM – 12:30 PM EST"},
        {"summary": "Eli no school", "time_label": "All Day (Nov 23–27)"},
        {"summary": "Lauren Thanksgiving break", "time_label": "All Day (Nov 25–27)"},
        {"summary": "Isaac/Tracy-Vegas", "time_label": "All Day (Nov 27–Dec 1)"},
        {"summary": "Isaac", "time_label": "Ongoing through 2:00 PM EST"},
        {"summary": "Sarah", "time_label": "2:00 PM EST – Nov 30, 2:00 PM EST"},
    ]
    tomorrow_calendar = [
        {"summary": "Eli lifeguard certification", "time_label": "9:00 AM – 4:00 PM EST"},
        {"summary": "Thanksgiving @ Pfarr", "time_label": "5:00 PM – 8:00 PM EST"},
    ]

    output_path = "index.html"
    existed_before = ensure_index_html_exists(
        output_path,
        today_calendar_events=today_calendar,
        tomorrow_calendar_events=tomorrow_calendar,
    )

    try:
        digest_data = fetch_digest()
        html_output = build_html(
            digest_data,
            today_calendar_events=today_calendar,
            tomorrow_calendar_events=tomorrow_calendar,
        )
        write_html_file(output_path, html_output)
        print("Successfully generated index.html")
    except Exception as exc:
        print(f"Warning: digest generation failed ({exc}).")
        if not existed_before or not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
            fallback_html = build_fallback_html(
                today_calendar_events=today_calendar,
                tomorrow_calendar_events=tomorrow_calendar,
                note=f"Live fetch failed ({exc}); keeping fallback page until next run.",
            )
            write_html_file(output_path, fallback_html)
            print("Wrote fallback index.html so downstream steps can proceed.")
        else:
            print("Preserved existing index.html.")
