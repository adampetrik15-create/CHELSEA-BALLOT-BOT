#!/usr/bin/env python3
"""Chelsea ballot (ticket application window) bot -> Discord webhook.

Zdroje:
  1. oficialny web chelseafc.com (clanky "Premier League ticket news ... at home") - cez Playwright
  2. X ucet @OK_Chelsea_1905 (oficialne X API, X_BEARER_TOKEN; volitelne RSS_FEED_URL z rss.app)

Pouzitie:
    python chelsea_bot.py --test      # testovacia sprava
    python chelsea_bot.py --dry-run   # nic neposiela, len vypise co by nasiel
"""
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.request
from urllib.parse import urljoin, urlencode

try:
    from zoneinfo import ZoneInfo
    UK = ZoneInfo("Europe/London")
except Exception:
    UK = None

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state.json")
LAST_RUN = os.path.join(HERE, "last_run.txt")
EXTRA_FILE = os.path.join(HERE, "urls.txt")
DIAG = []

CLUB = "Chelsea FC"
COLOR = 0x034694                         # modra: ticket okna
GOLD = 0xDBA111                          # zlata: zapas
HOME_URL = "https://www.chelseafc.com/"
FOOTER = "Chelsea Ballot Bot • časy sa zobrazujú vo vašom časovom pásme"
BOT_NAME = "Chelsea Ballot Bot"
X_USER = "OK_Chelsea_1905"
X_API_QUERY = f'from:{X_USER} (ballot OR "application window") -is:retweet'
X_KEYWORD = re.compile(r"ballot|application\s+window", re.I)
X_MAX_AGE_DAYS = 4
X_SINCE = {}
X_SINCE_MAX_AGE = dt.timedelta(days=6)   # recent search siaha 7 dni dozadu; starsie since_id nepouzivat
GAP_ALERT_DEFAULT = 180                  # minuty bez behu, po ktorych poslat upozornenie (max raz za 24 h)
RADAR_HOUR_DEFAULT = 8                   # daily radar: prvy beh od tejto hodiny (slovensky cas)
try:
    LOCAL = ZoneInfo("Europe/Bratislava")
except Exception:
    LOCAL = dt.timezone.utc
FIXTURE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{}/teams/363/schedule?fixture=true"
FIXTURE_LEAGUES = {"eng.1": "Premier League", "eng.fa": "FA Cup", "eng.league_cup": "Carabao Cup",
                   "uefa.champions": "Champions League", "uefa.europa": "Europa League",
                   "uefa.europa.conf": "Conference League"}
SITE_PAGES = ["https://www.chelseafc.com/en/all-on-sale-dates-men--",
              "https://www.chelseafc.com/en/tickets/mens-tickets",
              "https://www.chelseafc.com/en/ticket-information-for-all-games",
              "https://www.chelseafc.com/en/news/latest-news/all/all/news-type_Tickets",
              "https://www.chelseafc.com/en/news"]
ARTICLE_RE = re.compile(r'(/en/news/article/[a-z0-9\-]*(?:ticket-news|ticket-sales|application-window)[a-z0-9\-]*)', re.I)
MAX_ARTICLES = 25

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], 1)}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_WD = r"(?:(" + "|".join(w[:3] + r"\w*" for w in WEEKDAYS) + r")[ ,]+)?"
_DM = r"(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([A-Za-z]{3,9})"
_MD = r"([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?"
_TM = r"(\d{1,2}(?::\d{2})?\s*(?:am|pm)|noon|midnight|\d{1,2}:\d{2})"


def _mon(name):
    n = (name or "").lower()[:3]
    for k, v in MONTHS.items():
        if k[:3] == n:
            return v
    return None


def _wd(name):
    n = (name or "").lower()[:3]
    for i, w in enumerate(WEEKDAYS):
        if w[:3] == n:
            return i
    return None


def to_date(day, mon_name, wd_name, ref):
    """Datum bez roka -> rok urci den v tyzdni (ak je uvedeny), inak najblizsi rok k ref."""
    m = _mon(mon_name)
    if not m:
        return None
    cands = []
    for y in (ref.year - 1, ref.year, ref.year + 1):
        try:
            cands.append(dt.date(y, m, int(day)))
        except ValueError:
            pass
    w = _wd(wd_name)
    if w is not None:
        cands = [d for d in cands if d.weekday() == w]
    if not cands:
        return None
    return min(cands, key=lambda d: abs((d - ref).days))


def to_time(raw):
    if not raw:
        return None
    r = raw.lower().strip()
    if r == "noon":
        return 12, 0
    if r == "midnight":
        return 0, 0
    m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", r)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if m.group(3):
        h = h % 12 + (12 if m.group(3) == "pm" else 0)
    return h, mi


def _when(rx_text, label, ref, default_time):
    """Najde 'opens/closes ... <den> <datum> <cas>' za slovom label."""
    pat = (label + r"\b(?:\s+at|\s+on)?\W{0,6}" + _WD + r"(?:" + _DM + r"|" + _MD + r")"
           r"(?:\W{1,4}(?:at\s+)?" + _TM + r")?")
    m = re.search(pat, rx_text, re.I)
    if not m:
        return None, None
    wd, d1, m1, m2, d2, tm = m.groups()
    day, mon = (d1, m1) if d1 else (d2, m2)
    d = to_date(day, mon, wd, ref)
    if not d:
        return None, m.group(0)
    t = to_time(tm) or default_time
    when = dt.datetime(d.year, d.month, d.day, t[0], t[1], tzinfo=UK) if UK else \
        dt.datetime(d.year, d.month, d.day, t[0], t[1], tzinfo=dt.timezone.utc)
    return when, m.group(0)


def parse_window(text, ref=None):
    """Z textu clanku / tweetu vytiahne okno ziadosti o listky."""
    ref = ref or dt.date.today()
    t = re.sub(r"\s+", " ", text)
    out = {"opens": None, "closes": None, "opponent": None, "match_date": None, "kickoff": None,
           "category": None, "points": None, "tiers": None, "max_tickets": None, "results": None}
    out["opens"], _ = _when(t, r"(?:window\s+|ballot\s+|applications?\s+)?opens?", ref, (0, 0))
    out["closes"], _ = _when(t, r"(?:window\s+|ballot\s+|applications?\s+)?clos(?:es|ing)", ref, (23, 59))
    W = r"(?!Premier\b|Stamford\b|Saturday\b|Sunday\b|Monday\b|Tuesday\b|Wednesday\b|Thursday\b|Friday\b)[A-Z][A-Za-z.&'\-]*"
    m = re.search(r"Chelsea\s+(?:vs?\.?)\s+(" + W + r"(?:\s+" + W + r"){0,3})", t)
    if m:
        out["opponent"] = m.group(1).strip()
    else:
        m = re.search(r"\b(" + W + r"(?:\s+" + W + r"){0,3})\s+(?:vs?\.?)\s+Chelsea", t)
        if m:
            out["opponent"] = m.group(1).strip()
    m = re.search(r"(?:Stamford Bridge|kick[- ]?off|match(?:\s*date)?|Premier League)\W{0,6}" + _WD +
                  r"(?:" + _DM + r"|" + _MD + r")(?:\W{1,4}(?:at\s+)?(\d{1,2}[:.]\d{2}\s*(?:am|pm)?|\d{1,2}\s*(?:am|pm)))?",
                  t, re.I)
    if m:
        wd, d1, m1, m2, d2, tm = m.groups()
        d = to_date(d1 or d2, m1 or m2, wd, ref)
        if d:
            out["match_date"] = d
            out["kickoff"] = tm.replace(".", ":") if tm else None
    m = re.search(r"\bcategory\W{0,3}(A{1,2}|B|C|D)\b", t, re.I)
    if m:
        out["category"] = "Cat " + m.group(1).upper()
    m = re.search(r"(\d+)\s+loyalty\s+points?", t, re.I)
    if m:
        out["points"] = int(m.group(1))
    m = re.search(r"open\s+for\s+((?:True Blue\+?|CFC Blue|Junior Blue|[A-Z][\w+]*(?: [A-Z][\w+]*)?)"
                  r"(?:,?\s+(?:and\s+)?(?:True Blue\+?|CFC Blue|Junior Blue|[A-Z][\w+]*(?: [A-Z][\w+]*)?))*)\s+members", t)
    if m:
        out["tiers"] = re.sub(r"\s+and\s+", ", ", m.group(1)).replace(",,", ",")
    m = re.search(r"maximum\s+of\s+(\w+)\s+(?:tickets|per)", t, re.I) or re.search(r"max(?:imum)?\.?\s+(\d+)\s+tickets", t, re.I)
    if m:
        w = m.group(1).lower()
        out["max_tickets"] = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6"}.get(w, w)
    return out


def status_of(opens, closes, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    if closes and now >= closes:
        return "🔴 ZATVORENÉ"
    if opens and now < opens:
        return "🟡 ČOSKORO"
    if opens or closes:
        return "🟢 OTVORENÉ"
    return "⚪ NEZNÁME"


def _ts(d, style="f"):
    return f"<t:{int(d.timestamp())}:{style}>"


def field(name, value, inline=False):
    return {"name": name, "value": value[:1024], "inline": inline}


def card(author, title, description="", fields=(), color=COLOR):
    out = {"author": {"name": author}, "title": title[:256], "color": color, "fields": list(fields)}
    if description:
        out["description"] = description[:4000]
    return out


def finish(cards, article=None):
    """Odkaz na web a pata. url ma len prva karta: Discord zlucuje karty s rovnakou url."""
    cards[0]["url"] = HOME_URL
    link = f"**[Otvoriť chelseafc.com]({HOME_URL})**"
    if article:
        link += f" · [Článok o lístkoch]({article})"
    cards[-1]["fields"].append(field("🔗 Oficiálny web", link))
    cards[-1]["footer"] = {"text": FOOTER}
    return cards


def match_date_text(p):
    d = p["match_date"]
    t = to_time(re.sub(r"\s+", "", p["kickoff"])) if p.get("kickoff") else None
    if t and UK:
        return _ts(dt.datetime(d.year, d.month, d.day, t[0], t[1], tzinfo=UK), "F")
    return f"{d.day}. {d.month}. {d.year}"


def window_card(p, match=None, now=None, extra=None):
    opp = p.get("opponent")
    match = match or (f"Chelsea  vs  {opp}" if opp else "Chelsea ticket application window")
    fields = []
    if p.get("match_date"):
        fields.append(field("📅 Zápas", match_date_text(p)))
    fields.append(field("🔓 Otvorenie", _ts(p["opens"]) if p["opens"] else "Nepotvrdené", True))
    fields.append(field("🔒 Zatvorenie", _ts(p["closes"]) if p["closes"] else "Nepotvrdené", True))
    now = now or dt.datetime.now(dt.timezone.utc)
    if p["opens"] and now < p["opens"]:
        fields.append(field("⏳ Otvára sa", _ts(p["opens"], "R"), True))
    elif p["closes"] and now < p["closes"]:
        fields.append(field("⏳ Zatvára sa", _ts(p["closes"], "R"), True))
    if p.get("category"):
        fields.append(field("🎫 Kategória", p["category"], True))
    rules = []
    if p.get("tiers"):
        rules.append(f"Pre členov: {p['tiers']}.")
    if p.get("points"):
        rules.append(f"Potrebné vernostné body: aspoň {p['points']}.")
    if p.get("max_tickets"):
        rules.append(f"Max. lístkov na žiadosť: {p['max_tickets']}.")
    if rules:
        fields.append(field("⚠️ Podmienky", "\n".join(rules)))
    if extra:
        fields.append(field("ℹ️ Detaily", extra))
    return card("CHELSEA BALLOT RADAR", f"🎟️  {match}",
                f"Ticket application window\n**{status_of(p['opens'], p['closes'], now)}**", fields)


def window_embed(p, match=None, now=None, extra=None, article=None):
    return finish([window_card(p, match, now, extra)], article)[0]


def notice(title, text):
    return finish([card("CHELSEA BALLOT BOT", title, text)])[0]


# ---------- siet ----------
def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


_PW = {}


def fetch_js(url):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        DIAG.append("Playwright nie je nainstalovany, pouzivam obycajne stiahnutie")
        return fetch(url)
    if "ctx" not in _PW:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(args=["--no-sandbox"])
        _PW.update(pw=pw, browser=browser, ctx=browser.new_context(
            user_agent=UA, locale="en-GB", viewport={"width": 1366, "height": 900}))
    page = _PW["ctx"].new_page()
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(1500)
        if resp is not None and resp.status >= 400:
            raise OSError(f"HTTP {resp.status}")
        return page.content()
    finally:
        page.close()


def close_browser():
    try:
        if "browser" in _PW:
            _PW["browser"].close()
            _PW["pw"].stop()
    except Exception:
        pass


def to_text(page_html):
    t = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", page_html)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def clean_text(raw):
    t = html.unescape(re.sub(r"(?s)<[^>]+>", " ", raw or ""))
    t = re.sub(r"(?:https?://|pic\.twitter\.com/|www\.)\S+", "", t)
    t = re.sub(r"@?" + X_USER, "", t, flags=re.I)
    return re.sub(r"[ \t]+", " ", t).strip()


# ---------- stav ----------
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f), True
    except (OSError, ValueError):
        return {"seen": []}, False


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_webhooks():
    raw = [v for k, v in sorted(os.environ.items()) if k.upper().startswith("DISCORD_WEBHOOK_URL")]
    try:
        secrets = json.loads(os.environ.get("ALL_SECRETS", "") or "{}")
        raw += [v for k, v in sorted(secrets.items()) if k.upper().startswith("DISCORD_WEBHOOK_URL")]
    except ValueError:
        pass
    out = []
    for chunk in raw:
        for u in re.split(r"[\s,;]+", (chunk or "").strip()):
            if u.startswith("https://") and u not in out:
                out.append(u)
    return out


def post_discord(webhook, payload):
    urls = list(webhook) if isinstance(webhook, (list, tuple)) else [webhook]
    avatar = os.environ.get("AVATAR_URL", "").strip()
    if avatar.startswith("https://") and "avatar_url" not in payload:
        payload = dict(payload, avatar_url=avatar)
    data = json.dumps(payload).encode("utf-8")
    ok, last = 0, None
    for i, u in enumerate(urls, 1):
        try:
            req = urllib.request.Request(u, data=data, method="POST",
                                         headers={"Content-Type": "application/json", "User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                r.read()
            ok += 1
        except Exception as e:
            last = e
            DIAG.append(f"[Discord] webhook #{i} zlyhal: {type(e).__name__} {getattr(e, 'code', '')}")
    if ok == 0 and last is not None:
        raise last
    return ok


# ---------- zdroj 1: oficialny web ----------
def load_extra_urls():
    try:
        with open(EXTRA_FILE, encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip().startswith("http")]
    except OSError:
        return []


def find_articles(base, page_html):
    unesc = page_html.replace("\\/", "/")
    out = []
    for m in ARTICLE_RE.finditer(unesc):
        u = urljoin(base, m.group(1))
        if u not in out:
            out.append(u)
    return out


def article_title(page_html, url):
    m = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", page_html) or re.search(r"(?is)<title>(.*?)</title>", page_html)
    t = html.unescape(re.sub(r"<[^>]+>|\s+", " ", m.group(1))).strip() if m else ""
    return t or url.rsplit("/", 1)[-1].replace("-", " ")


def opponent_from_title(title, url):
    m = re.search(r"ticket news:?\s*(.+?)\s+at home", title, re.I)
    if not m:
        m = re.search(r"ticket-news-(.+?)-at-home", url)
        if m:
            return m.group(1).replace("-", " ").title().replace("Afc ", "AFC ")
        return None
    return m.group(1).strip()


def scan_site(state, fetch_fn=None):
    fetch_fn = fetch_fn or fetch_js
    closed = set(state.get("closed", []))
    urls = []
    for page in SITE_PAGES:
        try:
            h = fetch_fn(page)
            found = find_articles(page, h)
            DIAG.append(f"[web] {page}: {len(h)} znakov, clankov s ticket info: {len(found)}")
            for u in found:
                if u not in urls:
                    urls.append(u)
        except Exception as e:
            DIAG.append(f"[web] {page} CHYBA: {type(e).__name__} {e}")
    for u in load_extra_urls():
        if u not in urls:
            urls.append(u)
    out = []
    for u in urls[:MAX_ARTICLES]:
        if u in closed:
            continue
        try:
            h = fetch_fn(u)
        except Exception as e:
            DIAG.append(f"[web] {u} CHYBA: {type(e).__name__} {e}")
            continue
        text = to_text(h)
        if not re.search(r"application\s+window", text, re.I):
            DIAG.append(f"[web] {u}: bez 'application window'")
            continue
        title = article_title(h, u)
        p = parse_window(text, dt.date.today())
        if not p["opponent"]:
            p["opponent"] = opponent_from_title(title, u)
        DIAG.append(f"[web] {u}: otvara {p['opens']}, zatvara {p['closes']}, supper {p['opponent']}, "
                    f"cat {p['category']}, body {p['points']}")
        out.append({"url": u, "title": title, "parsed": p})
    return out


# ---------- zdroj 2: X ----------
def parse_feed(xml_text):
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml_text.lstrip("﻿").strip())
    loc = lambda el: el.tag.rsplit("}", 1)[-1]
    out = []
    for it in [e for e in root.iter() if loc(e) in ("item", "entry")]:
        g = lambda *n: next((c for c in it if loc(c) in n and (c.text or c.attrib)), None)
        title = (g("title").text if g("title") is not None else "") or ""
        d = g("encoded", "description", "summary", "content")
        desc = (d.text if d is not None else "") or ""
        le = g("link")
        link = (le.attrib.get("href") or le.text or "") if le is not None else ""
        ge = g("guid", "id")
        guid = (ge.text or "") if ge is not None else ""
        de = g("pubDate", "published", "updated")
        m = re.search(r"/status/(\d+)", link + " " + guid)
        t = clean_text(title)
        if len(t) < 30 or t.endswith(("…", "...")):
            dd = clean_text(desc)
            t = dd if len(dd) > len(t) else t
        out.append({"id": m.group(1) if m else (guid or link or title)[:80], "text": t,
                    "created": (de.text or "").strip() if de is not None else ""})
    return out


def parse_date(s):
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(s)
    except Exception:
        try:
            return dt.datetime.fromisoformat((s or "").replace("Z", "+00:00"))
        except Exception:
            return None


def env_int(name, default):
    try:
        return int(os.environ.get(name, "").strip())
    except ValueError:
        return default


def since_id_usable(since_id, now=None):
    """Cas vzniku tweetu je zakodovany v jeho ID (snowflake)."""
    try:
        ms = (int(since_id) >> 22) + 1288834974657
        created = dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return False
    return (now or dt.datetime.now(dt.timezone.utc)) - created < X_SINCE_MAX_AGE


def fetch_x_api(token, since_id=None, opener=None):
    params = {"query": X_API_QUERY, "max_results": "10", "tweet.fields": "created_at"}
    if since_id and since_id_usable(since_id):
        params["since_id"] = since_id
    else:
        params["start_time"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=X_MAX_AGE_DAYS)
                                ).strftime("%Y-%m-%dT%H:%M:%SZ")
    req = urllib.request.Request("https://api.x.com/2/tweets/search/recent?" + urlencode(params),
                                 headers={"Authorization": "Bearer " + token, "User-Agent": UA})
    with (opener or urllib.request.urlopen)(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8"))
    return [{"id": t["id"], "text": clean_text(t.get("text", "")), "created": t.get("created_at", "")}
            for t in data.get("data", []) or []]


def scan_x(fetch_fn=None):
    fetch_fn = fetch_fn or fetch
    res = []
    now = dt.datetime.now(dt.timezone.utc)
    feed = os.environ.get("RSS_FEED_URL", "").strip()   # volitelne (rss.app) - ked vyprsi, bot ho preskoci
    if feed.startswith("http"):
        try:
            items = parse_feed(fetch_fn(feed))
            hits = [t for t in items if X_KEYWORD.search(t["text"]) and
                    (not parse_date(t["created"]) or now - parse_date(t["created"]) <= dt.timedelta(days=X_MAX_AGE_DAYS))]
            DIAG.append(f"[RSS] poloziek: {len(items)}, relevantnych: {len(hits)}")
            res += hits
        except Exception as e:
            DIAG.append(f"[RSS] CHYBA: {e}")
    token = os.environ.get("X_BEARER_TOKEN", "").strip()
    if token:
        force = os.environ.get("FORCE_X_API", "").strip().lower() == "true"
        try:
            items = fetch_x_api(token, since_id=None if force else X_SINCE.get("id"))
            DIAG.append(f"[X API] novych tweetov: {len(items)}")
            res += [t for t in items if t["id"] not in {r["id"] for r in res} and X_KEYWORD.search(t["text"])]
        except Exception as e:
            DIAG.append(f"[X API] CHYBA: {e}")
    elif not feed:
        DIAG.append("[X] nie je nastaveny ani X_BEARER_TOKEN ani RSS_FEED_URL - tweety sa necitaju")
    return res


def tweet_payload(t, now=None):
    created = parse_date(t.get("created", ""))
    p = parse_window(t["text"], created.date() if created else dt.date.today())
    if not p["opens"] and not p["closes"]:
        return p, finish([card("CHELSEA BALLOT RADAR", "📰  Novinka o lístkoch", t["text"][:3500])])[0]
    if not p["opens"] and created:
        p["opens"] = created
    return p, window_embed(p, now=now)


# ---------- daily radar ----------
def next_match(fetch_fn=None, now=None):
    """Najblizsi zapas zo vsetkych sutazi (neoficialne ESPN API). Vrati None, ak sa nic nenacita."""
    fetch_fn = fetch_fn or fetch
    now = now or dt.datetime.now(dt.timezone.utc)
    found = []
    for code, name in FIXTURE_LEAGUES.items():
        try:
            for ev in json.loads(fetch_fn(FIXTURE_URL.format(code))).get("events", []) or []:
                when = dt.datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
                if when < now - dt.timedelta(hours=2):
                    continue
                comp = ev["competitions"][0]
                teams = {c["homeAway"]: c["team"]["displayName"] for c in comp["competitors"]}
                found.append({"when": when, "home": teams["home"], "away": teams["away"], "league": name,
                              "venue": (comp.get("venue") or {}).get("fullName")})
        except Exception as e:
            DIAG.append(f"[zapasy] {code} CHYBA: {type(e).__name__} {e}")
    return min(found, key=lambda m: m["when"]) if found else None


def radar_due(state, now):
    if os.environ.get("SEND_RADAR", "").strip().lower() == "true":
        return True
    if os.environ.get("DAILY_RADAR", "").strip().lower() == "false":
        return False
    local = now.astimezone(LOCAL)
    return (local.hour >= env_int("RADAR_HOUR", RADAR_HOUR_DEFAULT)
            and state.get("radar_date") != local.date().isoformat())


def radar_embeds(match, pages, now):
    local = now.astimezone(LOCAL)
    day = f"{local.day}. {local.month}. {local.year}"
    if match:
        fields = [field("📅 Výkop", _ts(match["when"], "F")), field("🏆 Súťaž", match["league"], True)]
        if match.get("venue"):
            fields.append(field("🏟️ Štadión", match["venue"], True))
        cards = [card(f"CHELSEA DAILY RADAR • {day}", f"⚽  {match['home']}  vs  {match['away']}",
                      f"## {_ts(match['when'], 'R')}", fields, GOLD)]
    else:
        cards = [card(f"CHELSEA DAILY RADAR • {day}", "⚽  Najbližší zápas", "Zápas sa nepodarilo načítať.",
                      color=GOLD)]
    windows = {}
    for pg in pages:
        p = pg["parsed"]
        if (p["opens"] or p["closes"]) and not (p["closes"] and now >= p["closes"]):
            windows.setdefault(match_key(p), pg)
    far = dt.datetime.max.replace(tzinfo=dt.timezone.utc)
    for pg in sorted(windows.values(), key=lambda x: x["parsed"]["closes"] or far)[:5]:
        c = window_card(pg["parsed"], now=now)
        c["fields"].append(field("📄 Zdroj", f"[Článok o lístkoch]({pg['url']})"))
        cards.append(c)
    if not windows:
        cards.append(card("CHELSEA BALLOT RADAR", "🎟️  Ticket application windows",
                          "Bot nenašiel otvorené ani pripravované okno. Web sa mohol aj nepodariť prečítať, "
                          "preto si to over na stránke klubu."))
    return finish(cards)


# ---------- hlavny beh ----------
def match_key(p):
    c = p["closes"]
    return f"m:{(p.get('opponent') or '?').lower()}:{c.date().isoformat() if c else '?'}"


def send(webhook, embeds):
    post_discord(webhook, {"username": BOT_NAME,
                           "embeds": embeds if isinstance(embeds, list) else [embeds]})


def main():
    args = sys.argv[1:]
    webhook = get_webhooks()
    dry = "--dry-run" in args or os.environ.get("DRY_RUN", "").strip().lower() == "true"
    if not dry and not webhook:
        sys.exit("Chyba: nastav DISCORD_WEBHOOK_URL (premenna prostredia alebo GitHub secret).")

    if "--test" in args or os.environ.get("TEST_MESSAGE", "").strip().lower() == "true":
        ok = post_discord(webhook, {"username": BOT_NAME, "embeds": [notice(
            "✅  Bot funguje", "Budem posielať upozornenia na ticket application window-y Chelsea.")]})
        msg = f"Testovacia sprava: webhookov {len(webhook)}, uspesne odoslana na {ok}"
        print(msg)
        with open(LAST_RUN, "w", encoding="utf-8") as fh:
            fh.write(msg + "\n" + "\n".join(DIAG) + "\n")
        return

    state, existed = load_state()
    seen = set(state["seen"])
    sent_matches = set(state.get("sent_matches", []))
    ids = [int(x[2:]) for x in seen if x.startswith("x:") and x[2:].isdigit()]
    if ids:
        X_SINCE["id"] = str(max(ids))
    now = dt.datetime.now(dt.timezone.utc)

    site_on = os.environ.get("CHECK_SITE", "true").strip().lower() != "false"
    pages = scan_site(state) if site_on else []
    if os.environ.get("RESEND_LAST", "").strip().lower() == "true":
        x_new = sorted(scan_x(), key=lambda t: int(t["id"]))[-1:]
    else:
        x_new = sorted([t for t in scan_x() if "x:" + t["id"] not in seen], key=lambda t: int(t["id"]))[-5:]

    # co sa poslat: clanky s oknom, ktore este nie je zatvorene, a este neposlane
    to_send, new_keys = [], set()
    for pg in pages:
        p = pg["parsed"]
        if p["closes"] and now >= p["closes"]:
            state.setdefault("closed", [])
            if pg["url"] not in state["closed"]:
                state["closed"].append(pg["url"])
            continue
        key = f"page:{pg['url']}:{p['closes'].isoformat() if p['closes'] else '?'}"
        if key in seen or (p["closes"] and match_key(p) in sent_matches):
            seen.add(key)
            continue
        if not p["opens"] and not p["closes"]:
            continue
        to_send.append((key, match_key(p), window_embed(p, now=now, article=pg["url"])))
    for t in x_new:
        p, emb = tweet_payload(t, now)
        if p["closes"] and now >= p["closes"]:
            seen.add("x:" + t["id"])     # uz zatvorene -> nic neposielat
            continue
        if p["closes"] and match_key(p) in sent_matches:
            seen.add("x:" + t["id"])     # rovnaky zapas uz bol poslany z webu
            continue
        to_send.append(("x:" + t["id"], match_key(p) if p["closes"] else None, emb))

    radar = radar_embeds(next_match(now=now), pages, now) if radar_due(state, now) else None
    DIAG.append(f"clankov s oknom: {len(pages)}, novych tweetov: {len(x_new)}, na odoslanie: {len(to_send)}, "
                f"daily radar: {'ano' if radar else 'nie'}")
    for line in DIAG:
        print(line)
    if dry:
        for k, _, e in to_send:
            print("-", k, json.dumps(e, ensure_ascii=False))
        if radar:
            print("- radar", json.dumps(radar, ensure_ascii=False))
        return
    with open(LAST_RUN, "w", encoding="utf-8") as fh:
        fh.write("\n".join(DIAG) + "\n")

    if existed:
        try:
            last = dt.datetime.fromisoformat(state.get("last_run", ""))
            gap = int((now - last).total_seconds() // 60)
        except ValueError:
            gap = 0
        try:
            alerted = now - dt.datetime.fromisoformat(state.get("last_gap_alert", "")) < dt.timedelta(hours=24)
        except ValueError:
            alerted = False
        if gap > env_int("GAP_ALERT_MINUTES", GAP_ALERT_DEFAULT) and not alerted:
            state["last_gap_alert"] = now.isoformat(timespec="seconds")
            send(webhook, notice("⚠️  Oneskorenie spúšťača",
                                 f"Bot sa naposledy spustil pred {gap} min. "
                                 f"Nové okná mohli prísť s oneskorením."))
    else:
        send(webhook, notice("✅  Bot beží", f"Pri prvom prechode posielam aktuálne okná ({len(to_send)}), "
                                            f"ďalej len nové."))
    state["last_run"] = now.isoformat(timespec="seconds")
    state["seen"] = sorted(seen)
    state["sent_matches"] = sorted(sent_matches)
    save_state(state)
    for key, mkey, emb in to_send:
        send(webhook, emb)
        seen.add(key)
        if mkey:
            sent_matches.add(mkey)
        state["seen"] = sorted(seen)
        state["sent_matches"] = sorted(sent_matches)
        save_state(state)
    if radar:
        send(webhook, radar)
        state["radar_date"] = now.astimezone(LOCAL).date().isoformat()
        save_state(state)


if __name__ == "__main__":
    try:
        main()
    finally:
        close_browser()
