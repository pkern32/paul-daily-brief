import os
import re
import json
import html
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from google import genai
from google.genai import types

# Initialize Gemini Client (reads GEMINI_API_KEY from environment)
client = genai.Client()


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
      "box_score_url": "https://...",
      "next_game": "Opponent, Date, Time (ET)",
      "schedule_url": "https://..."
    }}
  ],
  "sports_news": [
    {{
      "headline": "Major Sports Headline",
      "source": "Reputable sports outlet (e.g., ESPN, Associated Press, The Athletic, CBS Sports)",
      "summary": "2-sentence objective summary of the sports story",
      "url": "https://..."
    }}
  ],
  "single_family_homes": [
    {{
      "address": "Street Address, City, OH ZIP",
      "price": "$XXX,XXX",
      "listing_status": "New Listing | Price Cut / Value Deal",
      "strategy": "Short-Term Rental (Airbnb) | Long-Term Rental | Value-Add",
      "metrics_pitch": "Beds/baths/sqft, days on market or recent price cut details, and rental/equity upside near Loveland bike trail, downtown, or commuter routes",
      "url": "https://..."
    }}
  ],
  "multifamily_homes": [
    {{
      "address": "Street Address, City, OH ZIP",
      "price": "$X,XXX,XXX",
      "listing_status": "New Listing | Price Cut / Value Deal",
      "strategy": "Duplex | Triplex | Quadplex | Small Apartment Building",
      "metrics_pitch": "Unit count, total beds/baths/sqft, days on market or price drop, and estimated gross rent/cap rate angle",
      "url": "https://..."
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
1. "events_this_month": 5 upcoming events near 45140 (Loveland / Greater Cincinnati) occurring in the remainder of {current_month} (on or after {today_str}) spanning concerts, karaoke, comedy, real estate networking, Cincinnati Zoo, Newport Aquarium, local museums, and community events suited for ages 40-59.
2. "events_next_month": 5 upcoming events near 45140 (Loveland / Greater Cincinnati) occurring in {next_month} spanning concerts, karaoke, comedy, real estate networking, Cincinnati Zoo, Newport Aquarium, local museums, and community events suited for ages 40-59.
3. "sports": Cover ONLY these 4 Minnesota pro teams: Minnesota Twins, Minnesota Vikings, Minnesota Wild, and Minnesota Timberwolves. Include each team's single top current news headline with source and direct article URL, plus ONLY final score and top performers for the most recent completed game, direct box score URL, and direct team schedule URL.
4. "sports_news": Top 5 major US/global sports headlines today from reputable, fact-based sports outlets (e.g., Associated Press Sports, ESPN, CBS Sports, Yahoo Sports).
5. "single_family_homes": Exactly 3 active for-sale single-family homes in 45140 (or immediately adjacent Loveland/Maineville/Milford) with a STRICT MAXIMUM asking price of $500,000. Focus specifically on brand-new listings OR older listings with recent price cuts / strong value-per-sqft deals suitable for STR (Airbnb) or LTR investment.
6. "multifamily_homes": Exactly 3 active for-sale multifamily properties (duplex, triplex, quadplex, or small multifamily) in 45140 or immediately adjacent Greater Cincinnati communities (e.g., Milford, Mason, Lebanon, Northeast Cincinnati) with a STRICT MAXIMUM asking price of $1,500,000. Focus specifically on brand-new listings OR older listings with recent price cuts / strong cashflow potential.
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


def verify_link(url, reference_text, grounding_urls):
    """
    Verifies that a URL:
    1. Is a valid HTTP(S) URL.
    2. Resolves without 404/410/5xx errors or soft-404 pages.
    3. Matches the referenced story/topic (either in Gemini grounding chunks or page text).
    Returns (verified_url, is_verified_direct).
    """
    fallback_query = urllib.parse.quote_plus(str(reference_text or "news"))
    fallback_url = f"https://www.google.com/search?q={fallback_query}"

    if not url or not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return fallback_url, False

    clean_url = url.strip()
    is_grounded = clean_url in grounding_urls

    try:
        resp = requests.get(clean_url, headers=HEADERS, timeout=8, allow_redirects=True)
        final_url = resp.url

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
            "off market"
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


def validate_digest_links(data, grounding_urls):
    """Audits every URL in the generated digest against its referenced content."""
    for key in ("events_this_month", "events_next_month"):
        for e in data.get(key, []):
            ref = f"{e.get('title', '')} {e.get('location', '')}"
            e["url"], e["url_verified"] = verify_link(e.get("url"), ref, grounding_urls)

    for s in data.get("sports", []):
        team = s.get("team", "")
        s["headline_url"], s["headline_verified"] = verify_link(
            s.get("headline_url"), f"{team} {s.get('top_headline', '')}", grounding_urls
        )
        s["box_score_url"], s["box_verified"] = verify_link(
            s.get("box_score_url"), f"{team} box score {s.get('last_game_result', '')}", grounding_urls
        )
        s["schedule_url"], s["schedule_verified"] = verify_link(
            s.get("schedule_url"), f"{team} schedule", grounding_urls
        )

    for sn in data.get("sports_news", []):
        sn["url"], sn["url_verified"] = verify_link(
            sn.get("url"), f"{sn.get('headline', '')} {sn.get('source', '')}", grounding_urls
        )

    for r in data.get("single_family_homes", []):
        r["url"], r["url_verified"] = verify_link(
            r.get("url"), f"{r.get('address', '')} real estate for sale", grounding_urls
        )

    for r in data.get("multifamily_homes", []):
        r["url"], r["url_verified"] = verify_link(
            r.get("url"), f"{r.get('address', '')} multifamily for sale", grounding_urls
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


def fetch_digest():
    response = client.models.generate_content(
        model="gemini-3.8-flash",
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

    raw_text = (response.text or "").strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[1]
        if raw_text.endswith("```"):
            raw_text = raw_text.rsplit("```", 1)[0]

    data = json.loads(raw_text.strip())
    return validate_digest_links(data, grounding_urls)


def esc(val):
    return html.escape(str(val or ""))


def link_tag(url, label, verified=True):
    suffix = "" if verified else " <span class='search-tag'>[Search]</span>"
    return f'<a href="{esc(url)}" target="_blank" rel="noopener">{label}</a>{suffix}'


def render_property_cards(listings):
    return "".join(f"""
        <div class="card">
            <div class="row-between">
                <h3>{link_tag(r.get('url'), esc(r.get('address')), r.get('url_verified', True))}</h3>
                <span class="price">{esc(r.get('price'))}</span>
            </div>
            <div style="margin-top: 6px;">
                <span class="badge deal-badge">{esc(r.get('listing_status'))}</span>
                <span class="badge">{esc(r.get('strategy'))}</span>
            </div>
            <p style="margin-top:10px;">{esc(r.get('metrics_pitch'))}</p>
        </div>
    """ for r in listings)


def render_event_cards(events):
    return "".join(f"""
        <div class="card">
            <span class="badge">{esc(e.get('category'))}</span>
            <h3>{link_tag(e.get('url'), esc(e.get('title')), e.get('url_verified', True))}</h3>
            <p class="meta">📅 {esc(e.get('date_time'))} &nbsp;|&nbsp; 📍 {esc(e.get('location'))}</p>
            <p>{esc(e.get('why_it_fits'))}</p>
        </div>
    """ for e in events)


def build_html(data):
    now_et = datetime.now(ZoneInfo("America/New_York")).strftime("%A, %B %d, %Y at %I:%M %p %Z")
    _, current_month, next_month = get_month_labels()

    events_this_html = render_event_cards(data.get("events_this_month", []))
    events_next_html = render_event_cards(data.get("events_next_month", []))

    sports_html = "".join(f"""
        <div class="card">
            <div class="row-between">
                <h3>{esc(s.get('team'))}</h3>
                <span class="badge">{esc(s.get('status'))}</span>
            </div>
            <div class="team-headline">
                <strong>Top Headline:</strong> {link_tag(s.get('headline_url'), esc(s.get('top_headline')), s.get('headline_verified', True))}
                <span class="source">({esc(s.get('headline_source'))})</span>
            </div>
            <p><strong>Final:</strong> {esc(s.get('last_game_result'))} &mdash; {link_tag(s.get('box_score_url'), 'Box Score &rarr;', s.get('box_verified', True))}</p>
            <p><strong>Top Performers:</strong> {esc(s.get('top_performers'))}</p>
            <p class="meta"><strong>Next Game:</strong> {esc(s.get('next_game'))} &bull; {link_tag(s.get('schedule_url'), 'Schedule', s.get('schedule_verified', True))}</p>
        </div>
    """ for s in data.get("sports", []))

    sports_news_html = "".join(f"""
        <li>
            <strong>{link_tag(sn.get('url'), esc(sn.get('headline')), sn.get('url_verified', True))}</strong>
            <span class="source">({esc(sn.get('source'))})</span>
            <p>{esc(sn.get('summary'))}</p>
        </li>
    """ for sn in data.get("sports_news", []))

    sfh_html = render_property_cards(data.get("single_family_homes", []))
    mfh_html = render_property_cards(data.get("multifamily_homes", []))

    local_html = "".join(f"""
        <li>
            <strong>{link_tag(n.get('url'), esc(n.get('headline')), n.get('url_verified', True))}</strong>
            <span class="source">({esc(n.get('source'))})</span>
            <p>{esc(n.get('summary'))}</p>
        </li>
    """ for n in data.get("local_news", []))

    national_html = "".join(f"""
        <li>
            <strong>{link_tag(n.get('url'), esc(n.get('headline')), n.get('url_verified', True))}</strong>
            <span class="source">({esc(n.get('source'))})</span>
            <p>{esc(n.get('summary'))}</p>
        </li>
    """ for n in data.get("national_news", []))

    q = data.get("quote", {})

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Paul's Daily Brief</title>
    <style>
        :root {{
            --bg: #f8fafc;
            --card: #ffffff;
            --text: #1e293b;
            --muted: #64748b;
            --accent: #0f766e;
            --border: #e2e8f0;
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
        h1 {{ margin: 0; font-size: 1.75rem; }}
        .updated {{ color: var(--muted); font-size: 0.9rem; margin-top: 4px; }}
        .quote-box {{
            background: #f0fdfa;
            border-left: 4px solid var(--accent);
            padding: 18px 22px;
            border-radius: 8px;
            margin-bottom: 28px;
        }}
        .quote-box blockquote {{ margin: 0 0 8px 0; font-size: 1.1rem; font-style: italic; }}
        .quote-box cite {{ color: var(--muted); font-size: 0.9rem; }}
        h2 {{
            margin-top: 32px;
            border-bottom: 1px solid var(--border);
            padding-bottom: 6px;
            color: var(--accent);
        }}
        h3.subsection {{
            margin: 20px 0 12px 0;
            font-size: 1.05rem;
            color: #334155;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 16px;
        }}
        .card {{
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 16px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.03);
        }}
        .card h3 {{ margin: 6px 0; font-size: 1.05rem; }}
        .team-headline {{
            background: #f1f5f9;
            padding: 8px 10px;
            border-radius: 6px;
            margin: 10px 0;
            font-size: 0.92rem;
        }}
        .row-between {{ display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }}
        .badge {{
            display: inline-block;
            background: #e2e8f0;
            color: #334155;
            font-size: 0.75rem;
            font-weight: 600;
            padding: 2px 8px;
            border-radius: 999px;
            margin-right: 4px;
        }}
        .deal-badge {{
            background: #ccfbf1;
            color: #0f766e;
        }}
        .search-tag {{
            font-size: 0.75rem;
            color: var(--muted);
            font-weight: normal;
        }}
        .price {{ font-weight: 700; color: var(--accent); white-space: nowrap; }}
        .meta {{ color: var(--muted); font-size: 0.88rem; }}
        a {{ color: #0369a1; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        ul.news-list {{ list-style: none; padding: 0; }}
        ul.news-list li {{
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 14px 16px;
            margin-bottom: 12px;
        }}
        ul.news-list p {{ margin: 6px 0 0 0; font-size: 0.95rem; }}
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

        <h2>Minnesota Pro Sports</h2>
        <div class="grid">{sports_html}</div>

        <h2>Top 5 Sports Headlines</h2>
        <ul class="news-list">{sports_news_html}</ul>

        <h2>Top 5 Local News (45140 / Greater Cincinnati)</h2>
        <ul class="news-list">{local_html}</ul>

        <h2>Top 5 National News (Neutral Sources)</h2>
        <ul class="news-list">{national_html}</ul>

        <h2>45140 Area Investment Real Estate</h2>
        <h3 class="subsection">Single-Family Homes (Max $500,000 &bull; New &amp; Value Deals)</h3>
        <div class="grid">{sfh_html}</div>

        <h3 class="subsection">Multifamily Properties (Max $1,500,000 &bull; New &amp; Value Deals)</h3>
        <div class="grid">{mfh_html}</div>

        <h2>Top 10 Events Near 45140</h2>
        <h3 class="subsection">This Month ({esc(current_month)})</h3>
        <div class="grid">{events_this_html}</div>

        <h3 class="subsection">Next Month ({esc(next_month)})</h3>
        <div class="grid">{events_next_html}</div>
    </div>
</body>
</html>
"""

if __name__ == "__main__":
    digest_data = fetch_digest()
    html_output = build_html(digest_data)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_output)
    print("Successfully generated index.html")
