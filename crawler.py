import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

KEYWORDS = ["word1", "word2"] # Слова для поиска
visited = set()
found = []

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) " +
                  "AppleWebKit/537.36 (KHTML, like Gecko) " +
                  "Chrome/120.0.0.0 Safari/537.36"
})

def try_get(url):
    try:
        r = session.get(url, timeout=8)
        return r
    except requests.exceptions.SSLError:
        # Если HTTPS не работает — пробуем HTTP
        if url.startswith("https://"):
            http_url = url.replace("https://", "http://")
            print(f"[SSL fail] Пробуем {http_url}")
            try:
                r = session.get(http_url, timeout=8)
                return r
            except Exception as e:
                print(f"[Ошибка HTTP] {e}")
                return None
    except Exception as e:
        print(f"[Ошибка] {e}")
        return None

def clean_url(url):
    parsed = urlparse(url)
    return parsed.scheme + "://" + parsed.netloc + parsed.path

def check_text(text, url):
    for kw in KEYWORDS:
        if kw.lower() in text.lower():
            found.append((url, kw))

def crawl(url, base):
    url = clean_url(url)
    if url in visited:
        return
    visited.add(url)

    r = try_get(url)
    if not r:
        return

    print(f"{r.status_code} — {url}")

    if "text/html" not in r.headers.get("Content-Type", ""):
        return

    soup = BeautifulSoup(r.text, "html.parser")
    check_text(r.text, url)

    for a in soup.find_all("a", href=True):
        href = a["href"]
        href_full = urljoin(base, href)
        href_full = clean_url(href_full)

        check_text(a.get_text(), url)
        check_text(href, url)

        if any(x in href_full.lower() for x in [".pdf", ".jpg", ".png", "mailto:", "tel:"]):
            continue

        if urlparse(href_full).netloc == urlparse(base).netloc:
            crawl(href_full, base)

def main():
    domain = input("Введите домен (например: example.com): ").strip()
    if not domain.startswith("http"):
        domain = "https://" + domain
    base = clean_url(domain)
    crawl(base, base)

    RED = "\033[91m"
    RESET = "\033[0m"

    print("\nНайдено:")
    if not found:
        print("Совпадений не обнаружено.")
    else:
        for u, w in found:
            print(f"{RED}Слово '{w}' найдено на {u}{RESET}")

if __name__ == "__main__":
    main()