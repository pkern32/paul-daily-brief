import os
import json
import html
from datetime import datetime
from zoneinfo import ZoneInfo
from google import genai
from google.genai import types

# Initialize Gemini Client (reads GEMINI_API_KEY from environment)
client = genai.Client()

PROMPT = """
You are generating a personal 4-hour digest for someone in their 40s/50s living in Loveland, OH (ZIP 45140).
Use Google Search to find current, real-time information as of today. Every item MUST include a real, working source URL found via search.

Return ONLY valid JSON matching this exact schema (no markdown fences, no extra text):
{
  "events": [
    {
      "title": "Event Name",
      "category": "Concert | Karaoke | Comedy | Real Estate | Zoo/Aquarium | Museum | Community",
      "date_time": "Date & Time",
      "location": "Venue & City",
      "why_it_fits": "1-sentence note tailored for someone in their 40s/50s",
      "url": "https://..."
    }
  ],
  "sports": [
    {
      "team": "Minnesota Vikings | Minnesota Timberwolves | Minnesota Wild | Minnesota Twins | Minnesota United FC | Minnesota Lynx",
      "status": "In-Season | Offseason",
      "last_game_result": "Final Score (e.g., Vikings 24 - Bears 17) or Offseason note",
      "top_performers": "Key stat lines from last game",
      "box_score_url": "https://...",
      "next_game": "Opponent, Date, Time (ET)",
      "schedule_url": "https://..."
    }
  ],
  "real_estate": [
    {
      "address": "Street Address, City, OH 45140",
      "price": "$XXX,XXX",
      "strategy": "Short-Term Rental (Airbnb) | Long-Term Rental (Multifamily)",
      "metrics_pitch": "Beds/baths, estimated rental yield/cashflow angle near Loveland bike trail/downtown/commuter routes",
      "url": "https://..."
    }
  ],
  "local_news": [
    {
      "headline": "Headline",
      "source": "Neutral source name (e.g., Loveland Local News, WLWT, WKRC, WVXU, AP)",
      "summary": "2-sentence objective summary",
      "url": "https://..."
    }
  ],
  "national_news": [
    {
      "headline": "Headline",
      "source": "Politically neutral source (e.g., Associated Press, Reuters, PBS NewsHour, NPR, Straight Arrow News)",
      "summary": "2-sentence objective summary",
      "url": "https://..."
    }
  ],
  "quote": {
    "text": "An uplifting, grounded quote offering comfort and resilience for someone navigating grief and loss.",
    "author": "Author Name",
    "source_url": "https://..."
  }
}

Strict Requirements:
1. "events": Exactly 10 upcoming events near 45140 (Loveland / Greater Cincinnati) spanning concerts, karaoke, comedy, real estate networking, Cincinnati Zoo, Newport Aquarium, local museums, and community events suited for ages 40-59.
2. "sports": Cover all 6 Minnesota pro teams (Vikings, Timberwolves, Wild, Twins, United FC, Lynx). Report ONLY final score and top performers for the most recent completed game, plus box score URL and next scheduled game.
3. "real_estate": Active for-sale listings in or immediately adjacent to 45140 suitable for profitable Airbnb/STR (e.g., near Little Miami Scenic Trail / Historic Downtown Loveland) or multifamily/duplex/triplex LTR.
4. "local_news": Top 5 local news stories near Loveland / Warren, Clermont, and Hamilton counties from neutral, fact-based outlets (e.g., Loveland Local News, WLWT, WVXU).
5. "national_news": Top 5 US national stories strictly from politically neutral wire/public outlets (Associated Press, Reuters, PBS, NPR).
6. "quote": 1 deeply comforting, uplifting quote about enduring loss and carrying love forward.
"""

def fetch_digest():
    response = client.models.generate_content(
        model="gemini-3.8-flash",
        contents=PROMPT,
        config=types.GenerateContentConfig(
            tools=[{"google_search": {}}],
            thinking_config=types.ThinkingConfig(thinking_level="medium"),
        ),
    )
    raw_text = (response.text or "").strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[1]
        if raw_text.endswith("```"):
            raw_text = raw_text.rsplit("```", 1)[0]
    return json.loads(raw_text.strip())

def esc(val):
    return html.escape(str(val or ""))

def build_html(data):
    now_et = datetime.now(ZoneInfo("America/New_York")).strftime("%A, %B %d, %Y at %I:%M %p %Z")

    events_html = "".join(f"""
        <div class="card">
            <span class="badge">{esc(e.get('category'))}</span>
            <h3><a href="{esc(e.get('url'))}" target="_blank" rel="noopener">{esc(e.get('title'))}</a></h3>
            <p class="meta">📅 {esc(e.get('date_time'))} &nbsp;|&nbsp; 📍 {esc(e.get('location'))}</p>
            <p>{esc(e.get('why_it_fits'))}</p>
        </div>
    """ for e in data.get("events", []))

    sports_html = "".join(f"""
        <div class="card">
            <div class="row-between">
                <h3>{esc(s.get('team'))}</h3>
                <span class="badge">{esc(s.get('status'))}</span>
            </div>
            <p><strong>Final:</strong> {esc(s.get('last_game_result'))} &mdash; <a href="{esc(s.get('box_score_url'))}" target="_blank" rel="noopener">Box Score &rarr;</a></p>
            <p><strong>Top Performers:</strong> {esc(s.get('top_performers'))}</p>
            <p class="meta"><strong>Next Game:</strong> {esc(s.get('next_game'))} &bull; <a href="{esc(s.get('schedule_url'))}" target="_blank" rel="noopener">Schedule</a></p>
        </div>
    """ for s in data.get("sports", []))

    re_html = "".join(f"""
        <div class="card">
            <div class="row-between">
                <h3><a href="{esc(r.get('url'))}" target="_blank" rel="noopener">{esc(r.get('address'))}</a></h3>
                <span class="price">{esc(r.get('price'))}</span>
            </div>
            <span class="badge">{esc(r.get('strategy'))}</span>
            <p style="margin-top:8px;">{esc(r.get('metrics_pitch'))}</p>
        </div>
    """ for r in data.get("real_estate", []))

    local_html = "".join(f"""
        <li>
            <a href="{esc(n.get('url'))}" target="_blank" rel="noopener"><strong>{esc(n.get('headline'))}</strong></a>
            <span class="source">({esc(n.get('source'))})</span>
            <p>{esc(n.get('summary'))}</p>
        </li>
    """ for n in data.get("local_news", []))

    national_html = "".join(f"""
        <li>
            <a href="{esc(n.get('url'))}" target="_blank" rel="noopener"><strong>{esc(n.get('headline'))}</strong></a>
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
    <title>45140 Daily Brief &amp; Minnesota Sports</title>
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
        .row-between {{ display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }}
        .badge {{
            display: inline-block;
            background: #e2e8f0;
            color: #334155;
            font-size: 0.75rem;
            font-weight: 600;
            padding: 2px 8px;
            border-radius: 999px;
        }}
        .price {{ font-weight: 700; color: var(--accent); }}
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
            <h1>Loveland (45140) &amp; Minnesota Sports Brief</h1>
            <div class="updated">Last refreshed: {esc(now_et)} &bull; Auto-updates every 4 hours</div>
        </header>

        <section class="quote-box">
            <blockquote>&ldquo;{esc(q.get('text'))}&rdquo;</blockquote>
            <cite>&mdash; <a href="{esc(q.get('source_url'))}" target="_blank" rel="noopener">{esc(q.get('author'))}</a></cite>
        </section>

        <h2>Minnesota Pro Sports</h2>
        <div class="grid">{sports_html}</div>

        <h2>Top 5 Local News (45140 / Greater Cincinnati)</h2>
        <ul class="news-list">{local_html}</ul>

        <h2>Top 5 National News (Neutral Sources)</h2>
        <ul class="news-list">{national_html}</ul>

        <h2>45140 Investment Real Estate (STR &amp; Multifamily)</h2>
        <div class="grid">{re_html}</div>

        <h2>Top 10 Events Near 45140</h2>
        <div class="grid">{events_html}</div>
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
