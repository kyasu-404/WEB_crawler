import asyncio
import aiohttp
from aiohttp import ClientConnectorError
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

KEYWORDS = ["word1", "word2"] # Слова для поиска
MAX_TASKS = 30  # Количество параллельных запросов

visited = set()
found = []

semaphore = asyncio.Semaphore(MAX_TASKS)

def clean_url(url):
    parsed = urlparse(url)
    return parsed.scheme + "://" + parsed.netloc + parsed.path

def check_text(text, url):
    for kw in KEYWORDS:
        if kw.lower() in text.lower():
            found.append((url, kw))

async def fetch(session, url):
    async with semaphore:
        try:
            async with session.get(url, timeout=10) as resp:
                if "text/html" not in resp.headers.get("Content-Type", ""):
                    return None
                return await resp.text()
        except (aiohttp.ClientSSLError, aiohttp.ClientConnectorError):
            # Если HTTPS не проходит — пробуем HTTP
            if url.startswith("https://"):
                http_url = url.replace("https://", "http://")
                print(f"[SSL fail] Пробуем {http_url}")
                try:
                    async with session.get(http_url, timeout=10) as resp2:
                        if "text/html" not in resp2.headers.get("Content-Type", ""):
                            return None
                        return await resp2.text()
                except Exception as e:
                    print(f"[HTTP fail] {e} для {http_url}")
                    return None
        except Exception as e:
            print(f"[Ошибка запроса] {e} для {url}")
            return None

async def crawl(session, url, base):
    url = clean_url(url)
    if url in visited:
        return
    visited.add(url)

    html = await fetch(session, url)
    if not html:
        return

    print(f"[OK] {url}")
    check_text(html, url)

    soup = BeautifulSoup(html, "html.parser")

    tasks = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        href_full = urljoin(base, href)
        href_full = clean_url(href_full)

        check_text(a.get_text(), url)
        check_text(href, url)

        # Пропускаем файлы, почту и т.п.
        if any(x in href_full.lower() for x in [".pdf", ".jpg", ".png", "mailto:", "tel:"]):
            continue

        if urlparse(href_full).netloc == urlparse(base).netloc:
            tasks.append(crawl(session, href_full, base))

    if tasks:
        await asyncio.gather(*tasks)

async def main():
    domain = input("Введите домен (например: example.com): ").strip()
    if not domain.startswith("http"):
        domain = "https://" + domain
    base = clean_url(domain)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml",
    }

    async with aiohttp.ClientSession(headers=headers) as session:
        await crawl(session, base, base)

    RED = "\033[91m"
    RESET = "\033[0m"

    print("\nНайдено:")
    if not found:
        print("Совпадений не обнаружено.")
    else:
        for u, w in found:
            print(f"{RED}Слово '{w}' найдено на {u}{RESET}")

if __name__ == "__main__":
    asyncio.run(main())