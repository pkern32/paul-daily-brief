import os
import re
import json
import html
import urllib.parse
from datetime import datetime, date, timedelta, timezone
from email.utils import parsedate_to_datetime
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

# STR expense assumptions
STR_MGMT_FEE_RATE = 0.18
STR_CLEANING_FEE_RATE = 0.22
STR_BOOKING_FEE_RATE = 0.15
STR_OTHER_EXPENSE_RATE = 0.05
STR_ANNUAL_INSURANCE_RATE = 0.01

# News recency window (past 24 hours)
MAX_NEWS_AGE = timedelta(hours=24)

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


def get_et_now():
    return datetime.now(ZoneInfo("America/New_York"))


def parse_timestamp_utc(raw_ts):
    if raw_ts is None:
        return None
    if isinstance(raw_ts, datetime):
        dt = raw_ts
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)

    s = str(raw_ts).strip()
    if not s:
        return None

    rel_match = re.fullmatch(
        r"(\d+(?:\.\d+)?)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)\s*(?:ago)?",
        s.lower(),
    )
    if rel_match:
        val = float(rel_match.group(1))
        unit = rel_match.group(2)
        now_utc = datetime.now(timezone.utc)
        if unit.startswith("s"):
            return now_utc - timedelta(seconds=val)
        if unit.startswith("m"):
            return now_utc - timedelta(minutes=val)
        if unit.startswith("h"):
            return now_utc - timedelta(hours=val)
        if unit.startswith("d"):
            return now_utc - timedelta(days=val)

    iso_candidate = s[:-1] + "+00:00" if s.endswith("Z") else s
    try:
        dt = datetime.fromisoformat(iso_candidate)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except ValueError:
        pass

    try:
        dt = parsedate_to_datetime(s)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except Exception:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%b %d, %Y %I:%M %p"):
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=ZoneInfo("America/New_York"))
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue

    return None


def is_within_past_24_hours(raw_ts, now_utc=None, require_timestamp=True):
    ref_now = now_utc or datetime.now(timezone.utc)
    dt_utc = parse_timestamp_utc(raw_ts)
    if dt_utc is None:
        return not require_timestamp
    age = ref_now - dt_utc
    return timedelta(minutes=-15) <= age <= MAX_NEWS_AGE


def filter_recent_news(items, max_items=5, require_timestamp=True):
    now_utc = datetime.now(timezone.utc)
    recent = []
    for item in (items or []):
        ts_val = (
            item.get("published_at")
            or item.get("published")
            or item.get("pubDate")
            or item.get("timestamp")
            or item.get("time_text")
        )
        if is_within_past_24_hours(ts_val, now_utc=now_utc, require_timestamp=require_timestamp):
            recent.append(item)
            if len(recent) >= max_items:
                break
    return recent


def normalize_7day_weather(raw_days):
    normalized = []
    for entry in (raw_days or [])[:7]:
        if not isinstance(entry, dict):
            continue
        day_label = str(entry.get("day") or entry.get("forecast_day") or "").strip()
        hi_temp = str(entry.get("high_temp") or entry.get("high") or "").strip()
        low_temp = str(entry.get("low_temp") or entry.get("low") or "").strip()
        precip = str(entry.get("precip_chance") or entry.get("precipitation_chance") or "").strip()
        if precip and not precip.endswith("%"):
            precip = f"{precip}%"
        if day_label and hi_temp and low_temp and precip:
            normalized.append({
                "day": day_label,
                "high_temp": hi_temp,
                "low_temp": low_temp,
                "precip_chance": precip,
            })
    return normalized[:7]


def get_7day_weather_forecast(digest_data=None, sample_weather_7day=None):
    from_digest = normalize_7day_weather((digest_data or {}).get("weather_7day"))
    if len(from_digest) == 7:
        return from_digest
    if sample_weather_7day:
        return normalize_7day_weather(sample_weather_7day)
    return from_digest


def parse_ical_dt(raw_val, tz_name="America/New_York"):
    val = str(raw_val or "").strip()
    local_tz = ZoneInfo(tz_name)
    if re.fullmatch(r"\d{8}", val):
        d = datetime.strptime(val, "%Y%m%d").date()
        return d, d, True
    if val.endswith("Z"):
        dt_utc = datetime.strptime(val, "%Y%m%dT%H%M%SZ").replace(tzinfo=ZoneInfo("UTC"))
        dt_local = dt_utc.astimezone(local_tz)
        return dt_local.date(), dt_local, False
    match = re.fullmatch(r"(\d{8})T(\d{6})", val)
    if match:
        dt_local = datetime.strptime(f"{match.group(1)}{match.group(2)}", "%Y%m%d%H%M%S").replace(tzinfo=local_tz)
        return dt_local.date(), dt_local, False
    return None, None, False


def is_birthday_summary(summary):
    text = str(summary or "").lower()
    return bool(re.search(r"\bbirthday\b|\bbday\b", text))


def next_annual_occurrence(month, day, ref_date):
    for year in (ref_date.year, ref_date.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            candidate = date(year, 2, 28)
        if candidate >= ref_date:
            return candidate
    return date(ref_date.year + 1, month, min(day, 28))


def fetch_ical_all_events(ical_url):
    if not ical_url:
        return []
    try:
        resp = requests.get(ical_url, headers=HEADERS, timeout=10)
        if resp.status_code != 200 or not resp.text:
            return []
    except Exception:
        return []

    unfolded = re.sub(r"\r?\n[ \t]", "", resp.text)
    blocks = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", unfolded, flags=re.S)
    parsed_events = []

    for block in blocks:
        summary_m = re.search(r"^SUMMARY[^:]*:(.*)$", block, flags=re.M)
        dtstart_m = re.search(r"^DTSTART([^:]*):(.*)$", block, flags=re.M)
        dtend_m = re.search(r"^DTEND([^:]*):(.*)$", block, flags=re.M)
        rrule_m = re.search(r"^RRULE[^:]*:(.*)$", block, flags=re.M)
        if not summary_m or not dtstart_m:
            continue

        summary = summary_m.group(1).strip().replace("\\,", ",").replace("\\;", ";")
        start_date, start_obj, is_all_day = parse_ical_dt(dtstart_m.group(2))
        if not start_date:
            continue

        end_date = start_date
        end_obj = None
        if dtend_m:
            parsed_end_date, parsed_end_obj, _ = parse_ical_dt(dtend_m.group(2))
            if parsed_end_date:
                end_obj = parsed_end_obj
                if is_all_day and parsed_end_date > start_date:
                    end_date = parsed_end_date - timedelta(days=1)
                else:
                    end_date = parsed_end_date

        parsed_events.append({
            "summary": summary,
            "start_date": start_date,
            "end_date": end_date,
            "start_obj": start_obj,
            "end_obj": end_obj,
            "is_all_day": is_all_day,
            "rrule": rrule_m.group(1).strip().upper() if rrule_m else "",
        })

    return parsed_events


def format_event_time_label(ev):
    if ev.get("time_label"):
        return str(ev["time_label"])
    start_date = ev["start_date"]
    end_date = ev["end_date"]
    start_obj = ev.get("start_obj")
    end_obj = ev.get("end_obj")
    if ev.get("is_all_day"):
        if start_date == end_date:
            return f"All Day ({start_date.strftime('%b %d')})"
        return f"All Day ({start_date.strftime('%b %d')}–{end_date.strftime('%b %d')})"
    if start_obj:
        start_str = start_obj.strftime("%I:%M %p %Z").lstrip("0")
        if end_obj and end_obj.date() == start_date:
            end_str = end_obj.strftime("%I:%M %p %Z").lstrip("0")
            return f"{start_str} – {end_str}"
        if end_obj:
            end_str = end_obj.strftime("%b %d, %I:%M %p %Z")
            return f"{start_str} – {end_str}"
        return start_str
    return start_date.strftime("%b %d, %Y")


def get_calendar_events_for_day(target_date, sample_dated_events=None):
    ical_url = os.environ.get("ICAL_URL", "").strip()
    if ical_url:
        matched = []
        for ev in fetch_ical_all_events(ical_url):
            if ev["start_date"] <= target_date <= ev["end_date"]:
                matched.append({
                    "summary": ev["summary"],
                    "time_label": format_event_time_label(ev),
                })
        return matched

    matched = []
    for item in (sample_dated_events or []):
        start_d = item.get("start_date")
        end_d = item.get("end_date") or start_d
        if isinstance(start_d, str):
            start_d = date.fromisoformat(start_d)
        if isinstance(end_d, str):
            end_d = date.fromisoformat(end_d)
        if start_d and end_d and start_d <= target_date <= end_d:
            matched.append({
                "summary": item.get("summary", ""),
                "time_label": item.get("time_label", ""),
            })
    return matched


def get_upcoming_birthdays(target_date, sample_dated_events=None, count=2):
    ical_url = os.environ.get("ICAL_URL", "").strip()
    candidates = []

    if ical_url:
        for ev in fetch_ical_all_events(ical_url):
            summary = ev.get("summary", "")
            if not is_birthday_summary(summary):
                continue
            start_d = ev["start_date"]
            is_recurring = "FREQ=YEARLY" in ev.get("rrule", "")
            if is_recurring or start_d < target_date:
                occ_date = next_annual_occurrence(start_d.month, start_d.day, target_date)
            else:
                occ_date = start_d
            if occ_date >= target_date:
                candidates.append({
                    "summary": summary,
                    "date": occ_date,
                    "date_label": occ_date.strftime("%b %d, %Y"),
                })
    else:
        for item in (sample_dated_events or []):
            summary = item.get("summary", "")
            if not is_birthday_summary(summary):
                continue
            start_d = item.get("start_date")
            if isinstance(start_d, str):
                start_d = date.fromisoformat(start_d)
            if not start_d:
                continue
            is_recurring = bool(item.get("recurring", True))
            if is_recurring or start_d < target_date:
                occ_date = next_annual_occurrence(start_d.month, start_d.day, target_date)
            else:
                occ_date = start_d
            if occ_date >= target_date:
                candidates.append({
                    "summary": summary,
                    "date": occ_date,
                    "date_label": occ_date.strftime("%b %d, %Y"),
                })

    candidates.sort(key=lambda x: (x["date"], x["summary"].lower()))
    deduped = []
    seen = set()
    for c in candidates:
        key = (c["date"], c["summary"].lower())
        if key not in seen:
            seen.add(key)
            deduped.append(c)
        if len(deduped) >= count:
            break
    return deduped


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
    now = get_et_now()
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
    cutoff_utc = (datetime.now(timezone.utc) - MAX_NEWS_AGE).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"""
You are generating a personal 4-hour digest for someone in their 40s/50s living in Loveland, OH (ZIP 45140).
Today's date is {today_str}. Current month is {current_month}; next month is {next_month}.
Current UTC time is {datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}. Any news story MUST have been published on or after {cutoff_utc} (strictly within the past 24 hours).
Use Google Search to find current, real-time information as of today.

CRITICAL URL RULES:
- Every URL MUST be copied verbatim from your Google Search grounding results. Never guess, construct, or shorten URLs.
- Every URL must point directly to the specific article, box score, schedule, property listing, or event page—NEVER to a site homepage or generic section root.
- For weather in Loveland, OH ("weather_7day"), search specifically on wunderground.com (Weather Underground) for Loveland, OH 45140.
- For every item in "sports_news", "url" MUST be an ESPN.com URL (from www.espn.com) pointing to that specific ESPN sports story, and "source" MUST be "ESPN". Search specifically on espn.com for sports headlines published in the past 24 hours.
- For every item in "single_family_homes" and "multifamily_homes", "url" MUST be a Zillow.com listing URL (from www.zillow.com, such as a /homedetails/ property page) found via Google Search on zillow.com. Search specifically on site:zillow.com for active listings in 45140 and nearby communities.

Return ONLY valid JSON matching this exact schema (no markdown fences, no extra text):
{{
  "weather_7day": [
    {{
      "day": "Sat, Nov 28",
      "high_temp": "52°F",
      "low_temp": "39°F",
      "precip_chance": "10%"
    }}
  ],
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
      "published_at": "ISO 8601 UTC timestamp within past 24 hours (e.g., 2026-11-28T06:30:00Z)",
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
      "strategy": "Short-Term Rental (Airbnb) | Long-Term Rental | Value-Add",
      "metrics_pitch": "Beds/baths/sqft, property notes, and rental/equity upside near Loveland bike trail, downtown, or commuter routes",
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
      "strategy": "Duplex | Triplex | Quadplex | Small Apartment Building",
      "metrics_pitch": "Unit count, total beds/baths/sqft, and estimated gross rent/cash-flow angle",
      "url": "https://www.zillow.com/homedetails/..."
    }}
  ],
  "local_news": [
    {{
      "headline": "Headline",
      "source": "Neutral source name (e.g., Loveland Local News, WLWT, WKRC, WVXU, AP)",
      "published_at": "ISO 8601 UTC timestamp within past 24 hours (e.g., 2026-11-28T07:00:00Z)",
      "summary": "2-sentence objective summary",
      "url": "https://..."
    }}
  ],
  "national_news": [
    {{
      "headline": "Headline",
      "source": "Politically neutral source (e.g., Associated Press, Reuters, PBS NewsHour, NPR, Straight Arrow News)",
      "published_at": "ISO 8601 UTC timestamp within past 24 hours (e.g., 2026-11-28T05:15:00Z)",
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
0. "weather_7day": Exact 7-day daily weather forecast for Loveland, OH (ZIP 45140) from wunderground.com starting on {today_str}, with "day", "high_temp", "low_temp", and "precip_chance" for all 7 days.
1. "events_this_month": Top 10 upcoming events near 45140 (Loveland / Greater Cincinnati) occurring in the remainder of {current_month} (on or after {today_str}) spanning concerts, karaoke, comedy, real estate networking, Cincinnati Zoo, Newport Aquarium, local museums, and community events suited for ages 40-59.
2. "events_next_month": Top 10 upcoming events near 45140 (Loveland / Greater Cincinnati) occurring in {next_month} spanning concerts, karaoke, comedy, real estate networking, Cincinnati Zoo, Newport Aquarium, local museums, and community events suited for ages 40-59.
3. "sports": Cover ONLY these 4 Minnesota pro teams: Minnesota Twins, Minnesota Vikings, Minnesota Wild, and Minnesota Timberwolves. Include each team's single top current news headline with source and direct article URL, plus ONLY final score and top performers for the most recent completed game, an ESPN.com box score URL ("box_score_url" on espn.com), and direct team schedule URL.
4. "sports_news": Up to 5 major sports headlines published strictly within the past 24 hours (after {cutoff_utc}) sourced strictly from ESPN.com ("source": "ESPN", with direct "url" links on www.espn.com and accurate UTC "published_at"). Omit any story older than 24 hours.
5. "single_family_homes": Top 10 active for-sale single-family homes on Zillow.com in 45140 (or immediately adjacent Loveland/Maineville/Milford) with a STRICT MAXIMUM asking price of $500,000 suitable for STR (Airbnb) or LTR investment. Every "url" MUST be on zillow.com.
6. "multifamily_homes": Top 10 active for-sale multifamily properties (duplex, triplex, quadplex, or small multifamily) on Zillow.com in 45140 or immediately adjacent Greater Cincinnati communities (e.g., Milford, Mason, Lebanon, Northeast Cincinnati) with a STRICT MAXIMUM asking price of $1,500,000 suitable for rental cash flow. Every "url" MUST be on zillow.com.
7. "local_news": Up to 5 local news stories near Loveland / Warren, Clermont, and Hamilton counties published strictly within the past 24 hours (after {cutoff_utc}) from neutral, fact-based outlets (e.g., Loveland Local News, WLWT, WVXU) with accurate UTC "published_at". Omit any story older than 24 hours.
8. "national_news": Up to 5 US national stories published strictly within the past 24 hours (after {cutoff_utc}) from politically neutral wire/public outlets (Associated Press, Reuters, PBS, NPR) with accurate UTC "published_at". Omit any story older than 24 hours.
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


def fetch_espn_news_items(sport, league, team_id=None, limit=15, only_past_24h=False):
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/news?limit={limit}"
    if team_id:
        url += f"&team={urllib.parse.quote_plus(str(team_id))}"
    now_utc = datetime.now(timezone.utc)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=6)
        if resp.status_code != 200:
            return []
        payload = resp.json()
        items = []
        for art in payload.get("articles", []):
            art_url = extract_espn_article_url(art)
            if not art_url:
                continue
            pub_raw = str(art.get("published") or art.get("lastModified") or "").strip()
            if only_past_24h and not is_within_past_24_hours(pub_raw, now_utc=now_utc, require_timestamp=True):
                continue
            items.append({
                "headline": str(art.get("headline") or "").strip(),
                "description": str(art.get("description") or "").strip(),
                "published_at": pub_raw,
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
        pool.extend(fetch_espn_news_items(sport, league, limit=10, only_past_24h=True))

    now_utc = datetime.now(timezone.utc)
    results = []
    used_urls = set()

    for item in (gemini_sports_news or [])[:5]:
        headline = str(item.get("headline") or "").strip()
        summary = str(item.get("summary") or "").strip()
        cand = str(item.get("url") or "").strip()
        pub_at = str(item.get("published_at") or "").strip()

        matched_pool_item = None
        if cand and is_espn_domain(cand):
            matched_pool_item = next((p for p in pool if p["url"] == cand), None)
        if matched_pool_item is None and pool:
            q_words = set(extract_keywords(f"{headline} {summary}"))
            best_p = None
            best_score = 0
            for p in pool:
                if p["url"] in used_urls:
                    continue
                haystack = f"{p.get('headline', '')} {p.get('description', '')}".lower()
                score = sum(1 for w in q_words if w in haystack)
                if score > best_score:
                    best_score = score
                    best_p = p
            if best_p and best_score > 0:
                matched_pool_item = best_p

        effective_ts = matched_pool_item["published_at"] if matched_pool_item else pub_at
        if not is_within_past_24_hours(effective_ts, now_utc=now_utc, require_timestamp=True):
            continue

        resolved_url = (
            matched_pool_item["url"]
            if matched_pool_item
            else (cand if cand and is_espn_domain(cand) else "https://www.espn.com/")
        )
        if resolved_url in used_urls:
            continue
        used_urls.add(resolved_url)

        results.append({
            "headline": headline or (matched_pool_item["headline"] if matched_pool_item else "ESPN Sports Story"),
            "source": "ESPN",
            "published_at": effective_ts,
            "summary": summary or (matched_pool_item["description"] if matched_pool_item else ""),
            "url": resolved_url,
            "url_verified": is_espn_domain(resolved_url),
        })

    if not results and pool:
        for p in pool[:5]:
            results.append({
                "headline": p["headline"],
                "source": "ESPN",
                "published_at": p["published_at"],
                "summary": p["description"],
                "url": p["url"],
                "url_verified": True,
            })

    return results[:5]


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

    now_et = get_et_now()
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


def get_actual_annual_tax(listing):
    for key in ("annual_tax_paid", "property_tax_annual", "annual_property_tax"):
        val = parse_price_number(listing.get(key))
        if val > 0:
            return val

    pitch = str(listing.get("metrics_pitch") or "")
    tax_match = re.search(
        r"(?:actual\s+)?(?:property\s+)?tax(?:es)?(?:\s+paid)?[^\d$]*\$?\s*([\d,]+)(?:\s*/\s*yr|\s+per\s+year|\s+annually)?",
        pitch,
        re.I,
    )
    if tax_match:
        val = parse_price_number(tax_match.group(1))
        if val > 0:
            return val

    return 0.0


def estimate_monthly_tax(listing, price):
    actual_annual_tax = get_actual_annual_tax(listing)
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


def calculate_monthly_pi(price, down_payment_pct=0.20, annual_rate=0.0721):
    loan_amount = price * (1.0 - down_payment_pct)
    monthly_rate = annual_rate / 12.0
    n_payments = 360
    if monthly_rate > 0:
        return loan_amount * (monthly_rate * (1 + monthly_rate) ** n_payments) / (
            (1 + monthly_rate) ** n_payments - 1
        )
    return loan_amount / n_payments


def compute_cash_flow_breakdown(listing, is_multifamily=False, down_payment_pct=0.20, annual_rate=0.0721):
    price = parse_price_number(listing.get("price"))
    if price <= 0:
        return {
            "cash_flow": float("-inf"),
            "label": "Est. Cash Flow: N/A",
            "gross_rent": 0.0,
            "mgmt_fee": 0.0,
            "cleaning_fee": 0.0,
            "booking_fee": 0.0,
            "other_opex": 0.0,
            "monthly_insurance": 0.0,
            "monthly_tax": 0.0,
            "pi": 0.0,
            "is_actual_tax": False,
            "rent_source": "N/A",
        }

    monthly_tax, is_actual_tax = estimate_monthly_tax(listing, price)
    pi = calculate_monthly_pi(price, down_payment_pct=down_payment_pct, annual_rate=annual_rate)

    if is_str_strategy(listing):
        gross_rent, has_custom_airdna = get_airdna_str_rent(listing, is_multifamily=is_multifamily)
        mgmt_fee = gross_rent * STR_MGMT_FEE_RATE
        cleaning_fee = gross_rent * STR_CLEANING_FEE_RATE
        booking_fee = gross_rent * STR_BOOKING_FEE_RATE
        other_opex = gross_rent * STR_OTHER_EXPENSE_RATE
        monthly_insurance = (price * STR_ANNUAL_INSURANCE_RATE) / 12.0

        total_str_expenses = mgmt_fee + cleaning_fee + booking_fee + other_opex + monthly_insurance
        cash_flow = gross_rent - total_str_expenses - monthly_tax - pi
        rent_source = "AirDNA Est." if has_custom_airdna else "AirDNA Loveland Avg"
    else:
        gross_rent, has_zillow_rent = get_zillow_ltr_rent(listing, is_multifamily=is_multifamily)
        vacancy_rate = 0.06 if is_multifamily else 0.05
        non_tax_opex_rate = 0.24 if is_multifamily else 0.22
        mgmt_fee = 0.0
        cleaning_fee = 0.0
        booking_fee = gross_rent * vacancy_rate
        other_opex = gross_rent * non_tax_opex_rate
        monthly_insurance = (price * STR_ANNUAL_INSURANCE_RATE) / 12.0
        cash_flow = gross_rent - booking_fee - other_opex - monthly_insurance - monthly_tax - pi
        rent_source = "Zillow Rent Est." if has_zillow_rent else "Zillow Rent Baseline"

    sign = "+" if cash_flow >= 0 else "-"
    tax_label = f"Actual Tax ${round(monthly_tax):,}/mo" if is_actual_tax else f"Est. Tax ${round(monthly_tax):,}/mo"
    label = f"Est. Cash Flow: {sign}${abs(round(cash_flow)):,}/mo ({rent_source}, {tax_label})"
    return {
        "cash_flow": cash_flow,
        "label": label,
        "gross_rent": gross_rent,
        "mgmt_fee": mgmt_fee,
        "cleaning_fee": cleaning_fee,
        "booking_fee": booking_fee,
        "other_opex": other_opex,
        "monthly_insurance": monthly_insurance,
        "monthly_tax": monthly_tax,
        "pi": pi,
        "is_actual_tax": is_actual_tax,
        "rent_source": rent_source,
    }


def estimate_monthly_cash_flow_details(listing, is_multifamily=False, down_payment_pct=0.20, annual_rate=0.0721):
    bd = compute_cash_flow_breakdown(
        listing,
        is_multifamily=is_multifamily,
        down_payment_pct=down_payment_pct,
        annual_rate=annual_rate,
    )
    return bd["cash_flow"], bd["label"]


def rank_top_cash_flow_by_type(listings, is_multifamily=False, top_n=10):
    ranked = []
    for item in (listings or []):
        bd = compute_cash_flow_breakdown(item, is_multifamily=is_multifamily)
        enriched = dict(item)
        enriched["_cash_flow_value"] = bd["cash_flow"]
        enriched["_cash_flow_label"] = bd["label"]
        enriched["_breakdown"] = bd
        ranked.append(enriched)

    ranked.sort(key=lambda x: x["_cash_flow_value"], reverse=True)
    return ranked[:top_n]


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

    recent_local = filter_recent_news(data.get("local_news", []), max_items=5, require_timestamp=True)
    for n in recent_local:
        n["url"], n["url_verified"] = verify_link(
            n.get("url"), f"{n.get('headline', '')} {n.get('source', '')}", grounding_urls
        )
    data["local_news"] = recent_local

    recent_national = filter_recent_news(data.get("national_news", []), max_items=5, require_timestamp=True)
    for n in recent_national:
        n["url"], n["url_verified"] = verify_link(
            n.get("url"), f"{n.get('headline', '')} {n.get('source', '')}", grounding_urls
        )
    data["national_news"] = recent_national

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
        model="gemini-4-hydrogen",
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


def render_7day_weather_html(weather_days, empty_message="7-day forecast unavailable."):
    trimmed = normalize_7day_weather(weather_days)
    if not trimmed:
        return f'<li class="list-item calendar-item"><span class="meta">{esc(empty_message)}</span></li>'
    return "".join(f"""
        <li class="list-item calendar-item">
            <span class="cal-title">{esc(d.get('day'))}</span>
            <span class="badge cal-time">Hi {esc(d.get('high_temp'))} &bull; Lo {esc(d.get('low_temp'))} &bull; Precip {esc(d.get('precip_chance'))}</span>
        </li>
    """ for d in trimmed)


def render_calendar_html(calendar_events, empty_message="No events scheduled."):
    if not calendar_events:
        return f'<li class="list-item calendar-item"><span class="meta">{esc(empty_message)}</span></li>'
    return "".join(f"""
        <li class="list-item calendar-item">
            <span class="cal-title">{esc(ev.get('summary'))}</span>
            <span class="badge cal-time">{esc(ev.get('time_label'))}</span>
        </li>
    """ for ev in calendar_events)


def render_upcoming_birthdays_html(birthdays, empty_message="No upcoming birthdays found on calendar."):
    trimmed = (birthdays or [])[:2]
    if not trimmed:
        return f'<li class="list-item calendar-item"><span class="meta">{esc(empty_message)}</span></li>'
    return "".join(f"""
        <li class="list-item calendar-item">
            <span class="cal-title">{esc(b.get('summary'))}</span>
            <span class="badge cal-time">{esc(b.get('date_label'))}</span>
        </li>
    """ for b in trimmed)


def render_ranked_property_list(ranked_listings, is_multifamily=False, empty_message="No properties available."):
    trimmed = (ranked_listings or [])[:10]
    if not trimmed:
        return f'<li class="list-item"><span class="meta">{esc(empty_message)}</span></li>'
    rows = []
    for idx, r in enumerate(trimmed, start=1):
        bd = r.get("_breakdown")
        if not bd:
            bd = compute_cash_flow_breakdown(r, is_multifamily=is_multifamily)
        cf_label = r.get("_cash_flow_label") or bd["label"]
        gross_r = round(bd["gross_rent"])
        mgmt_r = round(bd["mgmt_fee"])
        clean_r = round(bd["cleaning_fee"])
        book_r = round(bd["booking_fee"])
        other_r = round(bd["other_opex"])
        ins_r = round(bd["monthly_insurance"])
        tax_r = round(bd["monthly_tax"])
        pi_r = round(bd["pi"])
        net_r = round(bd["cash_flow"]) if bd["cash_flow"] != float("-inf") else 0
        net_sign = "+" if net_r >= 0 else "-"
        net_formatted = f"{net_sign}${abs(net_r):,}/mo"

        rows.append(f"""
        <li class="list-item property-item" data-cf-card>
            <div class="row-between">
                <strong>#{idx} &bull; {link_tag(r.get('url'), esc(r.get('address')), r.get('url_verified', True))}</strong>
                <span class="price">{esc(r.get('price'))}</span>
            </div>
            <div class="meta-line property-summary-row">
                <details class="cf-details">
                    <summary class="cf-summary">
                        <span class="cf-triangle" aria-hidden="true">▸</span>
                        <span class="badge">{esc(r.get('strategy'))}</span>
                        <span class="badge cf-badge" data-cf-badge>{esc(cf_label)}</span>
                    </summary>
                    <div class="cf-editor" data-cf-editor>
                        <div class="cf-grid">
                            <label class="cf-field">
                                <span>Gross Monthly Rent ($)</span>
                                <input type="number" step="25" min="0" value="{gross_r}" data-cf-input="gross_rent">
                            </label>
                            <label class="cf-field">
                                <span>Property Mgmt ($/mo)</span>
                                <input type="number" step="10" min="0" value="{mgmt_r}" data-cf-input="mgmt_fee">
                            </label>
                            <label class="cf-field">
                                <span>Cleaning Fees ($/mo)</span>
                                <input type="number" step="10" min="0" value="{clean_r}" data-cf-input="cleaning_fee">
                            </label>
                            <label class="cf-field">
                                <span>Booking Fees ($/mo)</span>
                                <input type="number" step="10" min="0" value="{book_r}" data-cf-input="booking_fee">
                            </label>
                            <label class="cf-field">
                                <span>Other Expenses ($/mo)</span>
                                <input type="number" step="10" min="0" value="{other_r}" data-cf-input="other_opex">
                            </label>
                            <label class="cf-field">
                                <span>Insurance ($/mo)</span>
                                <input type="number" step="5" min="0" value="{ins_r}" data-cf-input="monthly_insurance">
                            </label>
                            <label class="cf-field">
                                <span>Property Tax ($/mo)</span>
                                <input type="number" step="10" min="0" value="{tax_r}" data-cf-input="monthly_tax">
                            </label>
                            <label class="cf-field">
                                <span>Mortgage P&amp;I ($/mo)</span>
                                <input type="number" step="10" min="0" value="{pi_r}" data-cf-input="pi">
                            </label>
                        </div>
                        <div class="cf-footer">
                            <span class="cf-net-readout">Calculated Monthly Cash Flow: <strong data-cf-net>{esc(net_formatted)}</strong></span>
                            <button type="button" class="cf-reset-btn" data-cf-reset>Reset Defaults</button>
                        </div>
                    </div>
                </details>
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


def render_news_list(items, empty_message="No stories from the past 24 hours."):
    trimmed = list(items or [])
    if not trimmed:
        return f'<li class="list-item news-row"><span class="meta">{esc(empty_message)}</span></li>'
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
    """ for n in trimmed)


def build_html(
    data,
    today_calendar_events=None,
    tomorrow_calendar_events=None,
    upcoming_birthdays=None,
    weather_7day=None,
):
    now_et = get_et_now().strftime("%A, %B %d, %Y at %I:%M %p %Z")
    _, current_month, next_month = get_month_labels()

    resolved_weather = get_7day_weather_forecast(data, sample_weather_7day=weather_7day)
    weather_7day_html = render_7day_weather_html(resolved_weather)
    today_calendar_html = render_calendar_html(
        today_calendar_events or [],
        empty_message="No events scheduled for today.",
    )
    tomorrow_calendar_html = render_calendar_html(
        tomorrow_calendar_events or [],
        empty_message="No events scheduled for tomorrow.",
    )
    upcoming_birthdays_html = render_upcoming_birthdays_html(upcoming_birthdays or [])
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

    sports_news_html = render_news_list(
        data.get("sports_news", []),
        empty_message="No ESPN sports stories from the past 24 hours.",
    )
    top_10_sfh = rank_top_cash_flow_by_type(data.get("single_family_homes"), is_multifamily=False, top_n=10)
    top_10_mfh = rank_top_cash_flow_by_type(data.get("multifamily_homes"), is_multifamily=True, top_n=10)
    sfh_html = render_ranked_property_list(top_10_sfh, is_multifamily=False, empty_message="No single-family cash flow listings available.")
    mfh_html = render_ranked_property_list(top_10_mfh, is_multifamily=True, empty_message="No multifamily cash flow listings available.")
    local_html = render_news_list(
        data.get("local_news", []),
        empty_message="No local stories from the past 24 hours.",
    )
    national_html = render_news_list(
        data.get("national_news", []),
        empty_message="No national stories from the past 24 hours.",
    )

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
        .meta-line {{ margin-top: 8px; }}
        details.cf-details {{ margin: 0; }}
        summary.cf-summary {{
            list-style: none;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            flex-wrap: wrap;
            gap: 6px;
        }}
        summary.cf-summary::-webkit-details-marker {{ display: none; }}
        .cf-triangle {{
            color: var(--muted);
            font-size: 0.85rem;
            line-height: 1;
            display: inline-block;
            transition: transform 0.15s ease;
        }}
        details.cf-details[open] > summary.cf-summary .cf-triangle {{
            transform: rotate(90deg);
            color: var(--text);
        }}
        .cf-editor {{
            margin-top: 10px;
            padding: 12px;
            background: #121419;
            border: 1px solid var(--border);
            border-radius: 6px;
        }}
        .cf-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(185px, 1fr));
            gap: 10px;
        }}
        .cf-field {{
            display: flex;
            flex-direction: column;
            gap: 4px;
            font-size: 0.78rem;
            color: var(--muted);
        }}
        .cf-field input {{
            background: #1c1f27;
            color: var(--text);
            border: 1px solid #373c47;
            border-radius: 4px;
            padding: 5px 8px;
            font-size: 0.88rem;
        }}
        .cf-field input:focus {{
            outline: none;
            border-color: #60a5fa;
        }}
        .cf-footer {{
            margin-top: 12px;
            padding-top: 10px;
            border-top: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
            font-size: 0.88rem;
        }}
        .cf-reset-btn {{
            background: #22262f;
            color: var(--text);
            border: 1px solid #373c47;
            border-radius: 4px;
            padding: 4px 10px;
            font-size: 0.78rem;
            cursor: pointer;
        }}
        .cf-reset-btn:hover {{
            background: #2d323e;
        }}
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

        <h2>7-Day Weather (Loveland, OH &bull; Weather Underground)</h2>
        <ul class="item-list">{weather_7day_html}</ul>

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

        <h2>Upcoming Birthdays</h2>
        <ul class="item-list">{upcoming_birthdays_html}</ul>

        <h2>Minnesota Pro Sports</h2>
        <ul class="item-list">{sports_html}</ul>

        <h2>Top 5 Sports Headlines (ESPN)</h2>
        <ul class="item-list">{sports_news_html}</ul>

        <h2>Top 5 Local News (45140 / Greater Cincinnati)</h2>
        <ul class="item-list">{local_html}</ul>

        <h2>Top 5 National News (Neutral Sources)</h2>
        <ul class="item-list">{national_html}</ul>

        <h2>45140 Area Investment Real Estate</h2>
        <h3 class="subsection">Top 10 Cash-Flowing Single-Family Homes (Max $500,000)</h3>
        <ul class="item-list">{sfh_html}</ul>

        <h3 class="subsection">Top 10 Cash-Flowing Multifamily Properties (Max $1,500,000)</h3>
        <ul class="item-list">{mfh_html}</ul>

        <h2>Top 20 Events Near 45140</h2>
        <h3 class="subsection">This Month ({esc(current_month)})</h3>
        <ul class="item-list">{events_this_html}</ul>

        <h3 class="subsection">Next Month ({esc(next_month)})</h3>
        <ul class="item-list">{events_next_html}</ul>
    </div>
    <script>
        (function() {{
            function readNum(inputEl) {{
                const parsed = parseFloat(inputEl.value);
                return Number.isFinite(parsed) ? parsed : 0;
            }}

            function formatMonthlyCashFlow(val) {{
                const rounded = Math.round(val);
                const sign = rounded >= 0 ? "+" : "-";
                return `${{sign}}$${{Math.abs(rounded).toLocaleString("en-US")}}/mo`;
            }}

            document.querySelectorAll("[data-cf-card]").forEach((card) => {{
                const inputs = Array.from(card.querySelectorAll("[data-cf-input]"));
                const netEl = card.querySelector("[data-cf-net]");
                const badgeEl = card.querySelector("[data-cf-badge]");
                const resetBtn = card.querySelector("[data-cf-reset]");

                inputs.forEach((inp) => {{
                    inp.dataset.defaultVal = inp.value;
                }});

                const originalBadgeText = badgeEl ? badgeEl.textContent : "";

                function recalculate() {{
                    const map = {{}};
                    inputs.forEach((inp) => {{
                        map[inp.dataset.cfInput] = readNum(inp);
                    }});
                    const grossRent = map.gross_rent || 0;
                    const expenses =
                        (map.mgmt_fee || 0) +
                        (map.cleaning_fee || 0) +
                        (map.booking_fee || 0) +
                        (map.other_opex || 0) +
                        (map.monthly_insurance || 0) +
                        (map.monthly_tax || 0) +
                        (map.pi || 0);
                    const netCf = grossRent - expenses;
                    const formatted = formatMonthlyCashFlow(netCf);
                    if (netEl) {{
                        netEl.textContent = formatted;
                    }}
                    if (badgeEl) {{
                        const taxRounded = Math.round(map.monthly_tax || 0).toLocaleString("en-US");
                        badgeEl.textContent = `Est. Cash Flow: ${{formatted}} (Custom/Edited, Tax $${{taxRounded}}/mo)`;
                    }}
                }}

                inputs.forEach((inp) => {{
                    inp.addEventListener("input", recalculate);
                    inp.addEventListener("change", recalculate);
                }});

                if (resetBtn) {{
                    resetBtn.addEventListener("click", () => {{
                        inputs.forEach((inp) => {{
                            inp.value = inp.dataset.defaultVal;
                        }});
                        if (badgeEl && originalBadgeText) {{
                            badgeEl.textContent = originalBadgeText;
                        }}
                        const initialNet = readNum(card.querySelector('[data-cf-input="gross_rent"]')) - (
                            readNum(card.querySelector('[data-cf-input="mgmt_fee"]')) +
                            readNum(card.querySelector('[data-cf-input="cleaning_fee"]')) +
                            readNum(card.querySelector('[data-cf-input="booking_fee"]')) +
                            readNum(card.querySelector('[data-cf-input="other_opex"]')) +
                            readNum(card.querySelector('[data-cf-input="monthly_insurance"]')) +
                            readNum(card.querySelector('[data-cf-input="monthly_tax"]')) +
                            readNum(card.querySelector('[data-cf-input="pi"]'))
                        );
                        if (netEl) {{
                            netEl.textContent = formatMonthlyCashFlow(initialNet);
                        }}
                    }});
                }}
            }});
        }})();
    </script>
</body>
</html>
"""


def build_fallback_html(
    today_calendar_events=None,
    tomorrow_calendar_events=None,
    upcoming_birthdays=None,
    weather_7day=None,
    note="Awaiting next scheduled digest refresh.",
):
    fallback_data = {
        "weather_7day": normalize_7day_weather(weather_7day),
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
                "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
        upcoming_birthdays=upcoming_birthdays,
        weather_7day=weather_7day,
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
    upcoming_birthdays=None,
    weather_7day=None,
):
    file_exists = os.path.isfile(path)
    has_content = file_exists and os.path.getsize(path) > 0

    if not has_content:
        print(f"{path} did not exist or was empty; creating initial HTML page.")
        fallback_html = build_fallback_html(
            today_calendar_events=today_calendar_events,
            tomorrow_calendar_events=tomorrow_calendar_events,
            upcoming_birthdays=upcoming_birthdays,
            weather_7day=weather_7day,
            note="Initial page created automatically because index.html was missing. Will populate with live digest data.",
        )
        write_html_file(path, fallback_html)
        return False

    return True


if __name__ == "__main__":
    today_date = get_et_now().date()
    tomorrow_date = today_date + timedelta(days=1)

    sample_weather_7day = [
        {"day": "Saturday, Nov 28", "high_temp": "52°F", "low_temp": "39°F", "precip_chance": "10%"},
        {"day": "Sunday, Nov 29", "high_temp": "52°F", "low_temp": "35°F", "precip_chance": "80%"},
        {"day": "Monday, Nov 30", "high_temp": "43°F", "low_temp": "29°F", "precip_chance": "10%"},
        {"day": "Tuesday, Dec 1", "high_temp": "49°F", "low_temp": "33°F", "precip_chance": "15%"},
        {"day": "Wednesday, Dec 2", "high_temp": "54°F", "low_temp": "38°F", "precip_chance": "10%"},
        {"day": "Thursday, Dec 3", "high_temp": "60°F", "low_temp": "44°F", "precip_chance": "50%"},
        {"day": "Friday, Dec 4", "high_temp": "56°F", "low_temp": "39°F", "precip_chance": "15%"},
    ]

    sample_dated_events = [
        {"start_date": "2026-11-26", "end_date": "2026-11-27", "summary": "Paul Work Holiday", "time_label": "All Day (Nov 26–27)"},
        {"start_date": "2026-11-26", "end_date": "2026-11-26", "summary": "Marketplace pickup slack line $15", "time_label": "12:00 PM – 12:30 PM EST"},
        {"start_date": "2026-11-23", "end_date": "2026-11-27", "summary": "Eli no school", "time_label": "All Day (Nov 23–27)"},
        {"start_date": "2026-11-25", "end_date": "2026-11-27", "summary": "Lauren Thanksgiving break", "time_label": "All Day (Nov 25–27)"},
        {"start_date": "2026-11-27", "end_date": "2026-12-01", "summary": "Isaac/Tracy-Vegas", "time_label": "All Day (Nov 27–Dec 1)"},
        {"start_date": "2026-11-26", "end_date": "2026-11-26", "summary": "Isaac", "time_label": "Ongoing through 2:00 PM EST"},
        {"start_date": "2026-11-26", "end_date": "2026-11-30", "summary": "Sarah", "time_label": "2:00 PM EST – Nov 30, 2:00 PM EST"},
        {"start_date": "2026-11-27", "end_date": "2026-11-27", "summary": "Eli lifeguard certification", "time_label": "9:00 AM – 4:00 PM EST"},
        {"start_date": "2026-11-27", "end_date": "2026-11-27", "summary": "Thanksgiving @ Pfarr", "time_label": "5:00 PM – 8:00 PM EST"},
    ]

    today_calendar = get_calendar_events_for_day(today_date, sample_dated_events)
    tomorrow_calendar = get_calendar_events_for_day(tomorrow_date, sample_dated_events)
    upcoming_birthdays = get_upcoming_birthdays(today_date, sample_dated_events, count=2)

    output_path = "index.html"
    existed_before = ensure_index_html_exists(
        output_path,
        today_calendar_events=today_calendar,
        tomorrow_calendar_events=tomorrow_calendar,
        upcoming_birthdays=upcoming_birthdays,
        weather_7day=sample_weather_7day,
    )

    try:
        digest_data = fetch_digest()
        html_output = build_html(
            digest_data,
            today_calendar_events=today_calendar,
            tomorrow_calendar_events=tomorrow_calendar,
            upcoming_birthdays=upcoming_birthdays,
            weather_7day=sample_weather_7day,
        )
        write_html_file(output_path, html_output)
        print("Successfully generated index.html")
    except Exception as exc:
        print(f"Warning: digest generation failed ({exc}).")
        if not existed_before or not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
            fallback_html = build_fallback_html(
                today_calendar_events=today_calendar,
                tomorrow_calendar_events=tomorrow_calendar,
                upcoming_birthdays=upcoming_birthdays,
                weather_7day=sample_weather_7day,
                note=f"Live fetch failed ({exc}); keeping fallback page until next run.",
            )
            write_html_file(output_path, fallback_html)
            print("Wrote fallback index.html so downstream steps can proceed.")
        else:
            print("Preserved existing index.html.")
