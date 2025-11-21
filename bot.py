import time
import threading
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, redirect, render_template_string

# ==================== CONFIG ==================== #
# Add as many alerts as you like in this list.

ALERTS = [
    {
        "name": "ralph_under_5",
    	"search_url": "https://www.vinted.co.uk/catalog?search_text=jumper&currency=GBP&search_id=28721414278&order=newest_first&page=1&time=1763712799&search_by_image_uuid=&size_ids[]=207&size_ids[]=208&brand_ids[]=88",
        "webhook_url": "https://discord.com/api/webhooks/1441120567842570342/ySapzRut6DFaJ8R1bUPE7i1oJ49C9VyG3y2a17xACWi8pqUm_55loJNsAiZzVPYKDehx",
        "max_price": 10.0,                  # float or None
        "must_include": [],                 # list of keywords or []
        "must_not_include": ["replica", "fake", "inspired"],  # list of banned words
        "size_filter": ["S", "M", "L", "XL"],                  # list of sizes or [] for any
        "avg_resale_price": 10.0,           # for profit estimation
        "fees_estimate": 10.0,               # for profit estimation
        "min_profit": 5,                 # only alert if est profit >= this
        "enabled": True,
    },
    {
    "name": "trainers_under_£5",
    "search_url": "https://www.vinted.co.uk/catalog?search_text=trainers&price_to=5.0&currency=GBP&size_ids[]=60&size_ids[]=1200&size_ids[]=782&size_ids[]=783&size_ids[]=784&size_ids[]=785&size_ids[]=786&size_ids[]=787&size_ids[]=788&size_ids[]=789&size_ids[]=790&size_ids[]=791&brand_ids[]=53&brand_ids[]=14&brand_ids[]=139&brand_ids[]=1775&brand_ids[]=1195&brand_ids[]=331974&brand_ids[]=44&brand_ids[]=2703&brand_ids[]=77512&brand_ids[]=11445&search_id=25547044964&order=newest_first",
    "webhook_url": "https://discord.com/api/webhooks/1441391005667426435/4wCoH0aLtB7l8b03McbSiCsmt2G1-i05LyhnoIxRyAaVkobbIu0bhrg2W_iDVb4Xf1Db",
    "max_price": 5.0,
    "must_include": [],
    "must_not_include": [],
    "size_filter": [],   # sizes already encoded in URL
    "avg_resale_price": 0.0,
    "fees_estimate": 0.0,
    "min_profit": 0.0,
    "enabled": True,
},

]

CHECK_DELAY_SECONDS = 5  # time between full scan cycles

# ================== END CONFIG ================== #

seen_links = {}  # name -> set of urls

app = Flask(__name__)


def ensure_seen_structure():
    """Make sure each alert name has a set in seen_links."""
    for alert in ALERTS:
        if alert["name"] not in seen_links:
            seen_links[alert["name"]] = set()


def send_discord_embed(webhook_url, title, price_text, url, image_url=None,
                       size_text=None, est_profit=None):
    """Send a rich embed with optional image, size and profit."""
    fields = []

    if price_text:
        fields.append({"name": "Price", "value": price_text, "inline": True})

    if size_text:
        fields.append({"name": "Size", "value": size_text, "inline": True})

    if est_profit is not None:
        fields.append(
            {
                "name": "Est. Profit",
                "value": f"£{est_profit:.2f}",
                "inline": True,
            }
        )

    embed = {
        "title": title,
        "url": url,
        "fields": fields,
    }

    if image_url:
        embed["image"] = {"url": image_url}

    data = {"embeds": [embed]}

    try:
        requests.post(webhook_url, json=data, timeout=10)
    except Exception as e:
        print(f"Error sending to Discord: {e}")


def parse_price(price_text: str):
    if not price_text:
        return None
    text = price_text.replace("£", "").replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def extract_basic_fields(card):
    """Try to pull title, price, size and image URL from a listing card."""
    # Title
    title_tag = (
        card.find("h3")
        or card.find("span", class_="ItemBox_title__")
        or card.find("div", class_="ItemBox_title__")
        or card.find("span")
    )
    title = title_tag.get_text(strip=True) if title_tag else "No title"

    # Price
    price_tag = (
        card.find("span", class_="price")
        or card.find("div", class_="ItemBox_price__")
        or card.find("span", string=lambda s: s and "£" in s)
    )
    price_text = price_tag.get_text(strip=True) if price_tag else ""
    price = parse_price(price_text)

    # Size (best-effort – may need tweaking depending on Vinted’s HTML)
    size_text = None
    size_tag = None

    cand_spans = card.find_all("span")
    for span in cand_spans:
        txt = span.get_text(strip=True)
        if txt.upper() in ["XS", "S", "M", "L", "XL", "XXL"]:
            size_tag = span
            break
    if size_tag:
        size_text = size_tag.get_text(strip=True)

    # Image URL
    img_url = None
    img_tag = card.find("img")
    if img_tag and img_tag.get("src"):
        img_url = img_tag["src"]

    return title, price_text, price, size_text, img_url


def fetch_items(search_url: str):
    """Fetch listing cards from a Vinted search page."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; VintedAlertBot/2.0)"}
    resp = requests.get(search_url, headers=headers, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # These selectors may need tweaking if Vinted changes layout
    cards = soup.select(
        "div.feed-grid__item, div.new-item-box, div.item-box, div.ItemBox_root__"
    )

    items = []
    for card in cards:
        link_tag = card.find("a", href=True)
        if not link_tag:
            continue

        href = link_tag["href"]
        if href.startswith("http"):
            full_url = href
        else:
            full_url = "https://www.vinted.co.uk" + href

        title, price_text, price, size_text, img_url = extract_basic_fields(card)

        items.append(
            {
                "url": full_url,
                "title": title,
                "price_text": price_text,
                "price": price,
                "size_text": size_text,
                "image_url": img_url,
            }
        )

    return items


def passes_filters(item: dict, alert_cfg: dict):
    """Apply keyword, price, size and profit logic."""
    title_lower = item["title"].lower()

    # Enabled?
    if not alert_cfg.get("enabled", True):
        return False

    # must_include keywords
    must_include = alert_cfg.get("must_include") or []
    if must_include:
        if not any(kw.lower() in title_lower for kw in must_include):
            return False

    # must_not_include keywords
    must_not = alert_cfg.get("must_not_include") or []
    if must_not:
        if any(bad.lower() in title_lower for bad in must_not):
            return False

    # Max price
    max_price = alert_cfg.get("max_price")
    if max_price is not None and item["price"] is not None:
        if item["price"] > max_price:
            return False

    # Size filter
    size_filter = alert_cfg.get("size_filter") or []
    if size_filter and item["size_text"]:
        norm_size = item["size_text"].strip().upper()
        norm_filter = [s.upper() for s in size_filter]
        if norm_size not in norm_filter:
            return False

    # Profit estimation
    avg_resale = alert_cfg.get("avg_resale_price")
    min_profit = alert_cfg.get("min_profit")
    fees = alert_cfg.get("fees_estimate", 0.0)

    est_profit = None
    if avg_resale is not None and item["price"] is not None:
        est_profit = avg_resale - item["price"] - fees
        item["est_profit"] = est_profit
    else:
        item["est_profit"] = None

    if min_profit is not None and est_profit is not None:
        if est_profit < min_profit:
            return False

    return True


def run_alert(alert_cfg: dict):
    """Check one alert config and send Discord messages for new matches."""
    name = alert_cfg["name"]
    search_url = alert_cfg["search_url"]
    webhook_url = alert_cfg["webhook_url"]

    print(f"[{name}] Checking {search_url}")
    items = fetch_items(search_url)

    for item in items:
        link = item["url"]

        if link in seen_links[name]:
            continue

        if not passes_filters(item, alert_cfg):
            continue

        seen_links[name].add(link)

        print(f"[{name}] New match: {item['title']} – {item['price_text']}")
        send_discord_embed(
            webhook_url=webhook_url,
            title=item["title"],
            price_text=item["price_text"],
            url=item["url"],
            image_url=item["image_url"],
            size_text=item["size_text"],
            est_profit=item.get("est_profit"),
        )


def main_loop():
    """Main scanning loop."""
    ensure_seen_structure()
    print("🚀 Vinted → Discord advanced alert bot started.")

    # Notify that alerts are live
    for alert in ALERTS:
        if alert.get("enabled", True):
            try:
                send_discord_embed(
                    alert["webhook_url"],
                    title=f"Alert '{alert['name']}' started",
                    price_text="",
                    url=alert["search_url"],
                    image_url=None,
                )
            except Exception:
                pass

    while True:
        for alert in ALERTS:
            try:
                ensure_seen_structure()
                run_alert(alert)
            except Exception as e:
                print(f"[{alert['name']}] Error: {e}")
        time.sleep(CHECK_DELAY_SECONDS)


# ================= WEB DASHBOARD (Flask) ================= #

TEMPLATE = """
<!doctype html>
<html>
<head>
  <title>Vinted Alert Dashboard</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 20px; }
    h1 { margin-bottom: 10px; }
    table { border-collapse: collapse; width: 100%; margin-bottom: 20px; }
    th, td { border: 1px solid #ccc; padding: 8px; font-size: 14px; }
    th { background: #f5f5f5; }
    input[type=text], input[type=number] { width: 100%; box-sizing: border-box; }
    .enabled { text-align: center; }
    .submit-row { text-align: right; }
    .small { font-size: 12px; color: #666; }
  </style>
</head>
<body>
  <h1>Vinted Alert Dashboard</h1>
  <p class="small">
    Edit alerts here. Changes apply in real time (bot must be running).
  </p>
  <form method="post">
    <table>
      <tr>
        <th>Enabled</th>
        <th>Name</th>
        <th>Search URL</th>
        <th>Webhook URL</th>
        <th>Max Price</th>
        <th>Must Include</th>
        <th>Must NOT Include</th>
        <th>Size Filter</th>
        <th>Avg Resale</th>
        <th>Fees</th>
        <th>Min Profit</th>
      </tr>
      {% for item in items %}
      <tr>
        <td class="enabled">
          <input type="checkbox" name="enabled_{{i}}" value="1" {% if alert.get('enabled', True) %}checked{% endif %}>
        </td>
        <td>
          <input type="text" name="name_{{i}}" value="{{alert['name']}}">
        </td>
        <td>
          <input type="text" name="search_url_{{i}}" value="{{alert['search_url']}}">
        </td>
        <td>
          <input type="text" name="webhook_url_{{i}}" value="{{alert['webhook_url']}}">
        </td>
        <td>
          <input type="number" step="0.01" name="max_price_{{i}}" value="{{alert.get('max_price') or ''}}">
        </td>
        <td>
          <input type="text" name="must_include_{{i}}" value="{{ ','.join(alert.get('must_include') or []) }}">
        </td>
        <td>
          <input type="text" name="must_not_include_{{i}}" value="{{ ','.join(alert.get('must_not_include') or []) }}">
        </td>
        <td>
          <input type="text" name="size_filter_{{i}}" value="{{ ','.join(alert.get('size_filter') or []) }}">
        </td>
        <td>
          <input type="number" step="0.01" name="avg_resale_price_{{i}}" value="{{alert.get('avg_resale_price') or ''}}">
        </td>
        <td>
          <input type="number" step="0.01" name="fees_estimate_{{i}}" value="{{alert.get('fees_estimate') or ''}}">
        </td>
        <td>
          <input type="number" step="0.01" name="min_profit_{{i}}" value="{{alert.get('min_profit') or ''}}">
        </td>
      </tr>
      {% endfor %}
    </table>
    <div class="submit-row">
      <button type="submit">Save Changes</button>
    </div>
  </form>
</body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def index():
    global ALERTS
    if request.method == "POST":
        for i, alert in enumerate(ALERTS):
            alert["enabled"] = bool(request.form.get(f"enabled_{i}"))
            alert["name"] = request.form.get(f"name_{{i}}", alert["name"])
            alert["search_url"] = request.form.get(
                f"search_url_{{i}}", alert["search_url"]
            )
            alert["webhook_url"] = request.form.get(
                f"webhook_url_{{i}}", alert["webhook_url"]
            )

            def parse_float(field_name):
                val = request.form.get(field_name, "").strip()
                return float(val) if val else None

            alert["max_price"] = parse_float(f"max_price_{{i}}")
            alert["avg_resale_price"] = parse_float(f"avg_resale_price_{{i}}")
            alert["fees_estimate"] = parse_float(f"fees_estimate_{{i}}") or 0.0
            alert["min_profit"] = parse_float(f"min_profit_{{i}}")

            def parse_list(field_name):
                txt = request.form.get(field_name, "").strip()
                if not txt:
                    return []
                return [x.strip() for x in txt.split(",") if x.strip()]

            alert["must_include"] = parse_list(f"must_include_{{i}}")
            alert["must_not_include"] = parse_list(f"must_not_include_{{i}}")
            alert["size_filter"] = parse_list(f"size_filter_{{i}}")

        ensure_seen_structure()
        return redirect("/")

    return render_template_string(TEMPLATE, alerts=ALERTS)


def start_dashboard():
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)


if __name__ == "__main__":
    # Start the web dashboard in a background thread
    t = threading.Thread(target=start_dashboard, daemon=True)
    t.start()

    # Start the main Vinted scanner loop
    main_loop()




