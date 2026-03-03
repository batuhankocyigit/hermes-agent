import os, re, json, time, random, logging
from urllib.parse import urlparse
try:
    import requests
    from bs4 import BeautifulSoup
    SCRAPING_AVAILABLE = True
except ImportError:
    SCRAPING_AVAILABLE = False

logger = logging.getLogger(__name__)

TOOL_SCHEMA = {
    "name": "analyze_product_reviews",
    "description": (
        "Bir urun veya isletmenin URL'sini alir, dusuk puanli (1-3 yildiz) "
        "musteri yorumlarini otomatik olarak ceker ve yapay zeka ile analiz eder. "
        "Kategori bazli puanlama, kritik sorunlar ve oncelikli aksiyon plani uretir. "
        "Trendyol, Hepsiburada, Amazon.com.tr desteklenir."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Urun veya isletme sayfasinin tam URL'si."
            },
            "max_reviews": {
                "type": "integer",
                "description": "Cekilecek maksimum yorum sayisi (varsayilan: 30)",
                "default": 30
            },
            "language": {
                "type": "string",
                "description": "Analiz dili: 'tr' veya 'en'",
                "default": "tr",
                "enum": ["tr", "en"]
            }
        },
        "required": ["url"]
    }
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def _sleep():
    time.sleep(random.uniform(0.8, 1.5))

def _fetch_trendyol(url, max_reviews):
    m = re.search(r'-p-(\d+)', url)
    product_id = m.group(1) if m else None
    product_name = "Trendyol Urunu"
    reviews = []
    if not product_id:
        return {"error": "Trendyol urun ID bulunamadi", "reviews": []}
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        t = soup.select_one("h1.pr-new-br span") or soup.select_one("h1[class*='product-name']")
        if t:
            product_name = t.get_text(strip=True)
    except Exception:
        pass
    api = f"https://public.trendyol.com/discovery-web-productgw-service/api/productReviewDetail/{product_id}"
    try:
        _sleep()
        r = requests.get(api, params={"storefrontId": "1", "culture": "tr-TR", "channelId": "1",
                                      "page": "0", "pageSize": min(max_reviews, 50)},
                         headers={**HEADERS, "Accept": "application/json"}, timeout=15)
        for rev in r.json().get("result", {}).get("productReviews", {}).get("content", []):
            if rev.get("rate", 5) <= 3:
                reviews.append({"star": rev.get("rate", 0),
                                 "text": rev.get("comment", "").strip(),
                                 "title": rev.get("commentTitle", "")})
    except Exception as e:
        logger.warning(f"Trendyol API: {e}")
    return {"product_name": product_name, "reviews": reviews, "site": "trendyol"}

def _fetch_amazon_tr(url, max_reviews):
    reviews = []
    product_name = "Amazon Urunu"
    m = re.search(r'/dp/([A-Z0-9]{10})', url) or re.search(r'/product-reviews/([A-Z0-9]{10})', url)
    asin = m.group(1) if m else None
    if not asin:
        return {"error": "Amazon ASIN bulunamadi", "reviews": []}
    for sf in ["one_star", "two_star", "three_star"]:
        if len(reviews) >= max_reviews:
            break
        try:
            _sleep()
            rev_url = f"https://www.amazon.com.tr/product-reviews/{asin}?filterByStar={sf}&pageNumber=1"
            r = requests.get(rev_url, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")
            if product_name == "Amazon Urunu":
                t = soup.select_one("[data-hook='product-link']")
                if t:
                    product_name = t.get_text(strip=True)
            for div in soup.select("[data-hook='review']"):
                s = div.select_one("[data-hook='review-star-rating'] span")
                b = div.select_one("[data-hook='review-body'] span")
                ti = div.select_one("[data-hook='review-title'] span:last-child")
                if s and b:
                    st = s.get_text(strip=True)
                    sn = int(st[0]) if st and st[0].isdigit() else 0
                    reviews.append({"star": sn, "text": b.get_text(strip=True),
                                    "title": ti.get_text(strip=True) if ti else ""})
        except Exception as e:
            logger.warning(f"Amazon {sf}: {e}")
    return {"product_name": product_name, "reviews": reviews, "site": "amazon_tr"}

def _fetch_hepsiburada(url, max_reviews):
    reviews = []
    product_name = "Hepsiburada Urunu"
    try:
        _sleep()
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        t = soup.select_one("h1[id='product-name']") or soup.select_one("span[itemprop='name']")
        if t:
            product_name = t.get_text(strip=True)
        for card in soup.select("[class*='reviewCard'], [class*='review-item']")[:max_reviews]:
            se = card.select_one("[class*='rating'], [class*='star']")
            te = card.select_one("[class*='comment'], [class*='review-text'], p")
            if se and te:
                st = re.search(r'\d', se.get_text(strip=True))
                sn = int(st.group()) if st else 0
                if sn <= 3:
                    reviews.append({"star": sn, "text": te.get_text(strip=True), "title": ""})
    except Exception as e:
        logger.warning(f"Hepsiburada: {e}")
    return {"product_name": product_name, "reviews": reviews, "site": "hepsiburada"}

def _fetch_firecrawl(url):
    key = os.environ.get("FIRECRAWL_API_KEY")
    if not key:
        return {"error": "FIRECRAWL_API_KEY eksik", "reviews": []}
    try:
        r = requests.post("https://api.firecrawl.dev/v1/scrape",
                          headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                          json={"url": url, "formats": ["markdown"], "onlyMainContent": True}, timeout=30)
        content = r.json().get("data", {}).get("markdown", "")
        return {"product_name": "Urun", "raw_content": content, "reviews": [], "site": "firecrawl", "use_raw": True}
    except Exception as e:
        return {"error": f"Firecrawl: {e}", "reviews": []}

def _fetch_generic(url, max_reviews):
    try:
        _sleep()
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        product_name = "Urun"
        for sel in ["h1", "[itemprop='name']", ".product-title", "#productTitle"]:
            el = soup.select_one(sel)
            if el:
                product_name = el.get_text(strip=True)[:100]
                break
        reviews = []
        for sel in ["[class*='review']", "[class*='comment']", "[class*='yorum']"]:
            for el in soup.select(sel)[:max_reviews]:
                text = el.get_text(strip=True)
                if len(text) > 20:
                    reviews.append({"star": 0, "text": text[:500], "title": ""})
        if not reviews:
            page_text = soup.get_text(separator="\n", strip=True)
            return {"product_name": product_name, "raw_content": page_text[:8000],
                    "reviews": [], "site": "generic", "use_raw": True}
        return {"product_name": product_name, "reviews": reviews, "site": "generic"}
    except Exception as e:
        return {"error": f"Generic: {e}", "reviews": []}

def fetch_reviews(url, max_reviews=30):
    domain = urlparse(url).netloc.lower()
    if "trendyol.com" in domain:
        return _fetch_trendyol(url, max_reviews)
    elif "hepsiburada.com" in domain:
        return _fetch_hepsiburada(url, max_reviews)
    elif "amazon.com.tr" in domain:
        return _fetch_amazon_tr(url, max_reviews)
    elif os.environ.get("FIRECRAWL_API_KEY"):
        return _fetch_firecrawl(url)
    else:
        return _fetch_generic(url, max_reviews)

ANALYSIS_PROMPT = """Sen bir musteri deneyimi analisti ve isletme danismanisim.
Asagidaki urun icin DUSUK PUANLI (1-3 yildiz) musteri yorumlarini analiz et.

Urun: {product_name}
URL: {url}
Yorum sayisi: {review_count}

YORUMLAR:
{reviews_text}

SADECE su JSON formatinda yanit ver, baska hicbir sey ekleme:
{{
  "urun_adi": "string",
  "genel_skor": 0,
  "genel_ozet": "string (2-3 cumle)",
  "kategoriler": {{
    "urun_kalitesi": {{"skor": 0, "tespit": "string", "ornek": "string"}},
    "musteri_hizmetleri": {{"skor": 0, "tespit": "string", "ornek": "string"}},
    "fiyat_deger": {{"skor": 0, "tespit": "string", "ornek": "string"}},
    "kargo_teslimat": {{"skor": 0, "tespit": "string", "ornek": "string"}},
    "kullanici_deneyimi": {{"skor": 0, "tespit": "string", "ornek": "string"}}
  }},
  "en_tekrarlayan_sikayet": "string",
  "kritik_sorunlar": ["string", "string", "string"],
  "oncelikli_aksiyonlar": [
    {{"oncelik": "yuksek", "baslik": "string", "ne_yapilmali": "string", "tahmini_etki": "string"}}
  ],
  "gizli_firsatlar": ["string", "string"],
  "rakip_avantaj_riski": "string",
  "musteri_profili": "string",
  "pozitif_sinyaller": ["string", "string"]
}}
genel_skor 0-60 araliginda, kategori skorlari 0-100 araliginda olmali."""

def analyze_with_llm(scraped, url, language="tr"):
    api_key = (os.environ.get("OPENROUTER_API_KEY")
               or os.environ.get("ANTHROPIC_API_KEY")
               or os.environ.get("OPENAI_API_KEY"))
    if not api_key:
        return {"error": "LLM API anahtari bulunamadi (OPENROUTER_API_KEY gerekli)"}
    product_name = scraped.get("product_name", "Belirtilmemis")
    reviews = scraped.get("reviews", [])
    raw = scraped.get("raw_content", "")
    if scraped.get("use_raw") and raw:
        reviews_text = f"[Sayfa icerigi]\n{raw[:5000]}"
        review_count = "bilinmiyor"
    elif reviews:
        lines = []
        for i, r in enumerate(reviews[:50], 1):
            s = r.get("star", 0)
            lines.append(f"{i}. [{'*'*s}{'o'*(5-s)}] {r.get('title','')}\n   {r.get('text','')[:400]}")
        reviews_text = "\n\n".join(lines)
        review_count = len(reviews)
    else:
        return {"error": "Yorum bulunamadi", "url": url}
    prompt = ANALYSIS_PROMPT.format(product_name=product_name, url=url,
                                    review_count=review_count, reviews_text=reviews_text)
    use_openrouter = bool(os.environ.get("OPENROUTER_API_KEY"))
    base_url = "https://openrouter.ai/api/v1" if use_openrouter else "https://api.openai.com/v1"
    model = "anthropic/claude-3.5-haiku" if use_openrouter else "gpt-4o-mini"
    try:
        resp = requests.post(f"{base_url}/chat/completions",
                             headers={"Authorization": f"Bearer {api_key}",
                                      "Content-Type": "application/json",
                                      "HTTP-Referer": "https://github.com/NousResearch/hermes-agent"},
                             json={"model": model,
                                   "messages": [{"role": "user", "content": prompt}],
                                   "max_tokens": 2000, "temperature": 0.3},
                             timeout=60)
        raw_resp = resp.json()["choices"][0]["message"]["content"].strip()
        if "```json" in raw_resp:
            raw_resp = raw_resp.split("```json")[1].split("```")[0]
        elif "```" in raw_resp:
            raw_resp = raw_resp.split("```")[1].split("```")[0]
        result = json.loads(raw_resp.strip())
        result["source_url"] = url
        result["scraped_reviews_count"] = len(reviews)
        result["site"] = scraped.get("site", "unknown")
        return result
    except Exception as e:
        return {"error": f"Analiz basarisiz: {str(e)}"}

def analyze_product_reviews(url: str, max_reviews: int = 30, language: str = "tr") -> dict:
    """Urun URL'sinden dusuk puanli yorumlari ceker ve AI ile analiz eder."""
    if not url.startswith(("http://", "https://")):
        return {"error": "Gecerli bir URL gerekli"}
    if not SCRAPING_AVAILABLE:
        return {"error": "pip install requests beautifulsoup4"}
    logger.info(f"[review_analyzer] {url}")
    scraped = fetch_reviews(url, max_reviews)
    if "error" in scraped and not scraped.get("reviews") and not scraped.get("raw_content"):
        return {"error": scraped["error"], "url": url}
    return analyze_with_llm(scraped, url, language)

def get_tool_schemas():
    return [TOOL_SCHEMA]

def handle_tool_call(tool_name: str, tool_input: dict) -> dict:
    if tool_name == "analyze_product_reviews":
        return analyze_product_reviews(
            url=tool_input["url"],
            max_reviews=tool_input.get("max_reviews", 30),
            language=tool_input.get("language", "tr")
        )
    return {"error": f"Bilinmeyen tool: {tool_name}"}
