import argparse
import asyncio
from dataclasses import dataclass, field
from urllib.parse import quote, urljoin, urlsplit

import aiohttp
from bs4 import BeautifulSoup
from yarl import URL


DEFAULT_CONCURRENCY = 5
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
HTML_TYPES = {"text/html", "application/xhtml+xml"}


@dataclass
class CrawlReport:
    pages: int = 0
    skipped: int = 0
    redirects: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    matches: dict[str, list[str]] = field(default_factory=dict)


def normalize_url(url, base=None):
    try:
        parsed = urlsplit(urljoin(base, url) if base else url)
        hostname = parsed.hostname
        if parsed.scheme not in {"http", "https"} or not hostname or parsed.username is not None:
            return None
        if any(char.isspace() for char in hostname):
            return None

        port = parsed.port
        default_port = 443 if parsed.scheme == "https" else 80
        origin = URL.build(scheme=parsed.scheme, host=hostname, port=None if port == default_port else port)
        path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=")
        query = quote(parsed.query, safe="/%?:@!$&'()*+,;=[]")
        return parsed._replace(netloc=origin.raw_authority, path=path, query=query, fragment="").geturl()
    except ValueError:
        return None


def normalize_keywords(keywords):
    unique = {}
    for keyword in keywords:
        keyword = " ".join(keyword.split())
        if keyword:
            unique.setdefault(keyword.casefold(), keyword)
    return unique


async def crawl(start_url, keywords, concurrency, search_link_urls=False):
    start = urlsplit(start_url)
    report = CrawlReport()
    pending = asyncio.Queue()
    scheduled = set()
    keywords = normalize_keywords(keywords)

    def in_scope(url):
        target = urlsplit(url)
        return (
            target.hostname == start.hostname
            and target.port == start.port
            and (start.scheme == "http" or target.scheme == "https")
        )

    def enqueue(url):
        if url not in scheduled:
            scheduled.add(url)
            pending.put_nowait(url)

    def follow_redirect(url, response_url, location):
        target = normalize_url(location, response_url) if location else None
        if target is None:
            report.errors[url] = "Некорректный или отсутствующий заголовок Location"
            return
        if not in_scope(target):
            report.errors[url] = f"Редирект за пределы области обхода на {target}"
            return

        destination = target
        while destination != url and destination in report.redirects:
            destination = report.redirects[destination]
        if destination == url:
            report.errors[url] = "Циклический редирект"
            return

        report.redirects[url] = target
        enqueue(target)

    async def process(session, url):
        async with session.get(URL(url, encoded=True), allow_redirects=False) as response:
            response_url = str(response.url)
            if response.status in REDIRECT_STATUSES:
                follow_redirect(url, response_url, response.headers.get("Location"))
                return
            if not 200 <= response.status < 300:
                report.errors[url] = f"HTTP {response.status}"
                return
            if response.content_type not in HTML_TYPES:
                report.skipped += 1
                return

            soup = BeautifulSoup(await response.read(), "html.parser", from_encoding=response.charset)
            report.pages += 1
            print(f"{response.status} — {response_url}")

        for element in soup.select("script, style, template, noscript, [hidden]"):
            element.extract()

        text = " ".join(soup.get_text(" ", strip=True).split()).casefold()
        base_tag = soup.find("base", href=True)
        base_url = normalize_url(base_tag["href"], response_url) if base_tag else None
        hrefs = [link["href"] for link in soup.find_all("a", href=True)]
        link_urls = [href.casefold() for href in hrefs] if search_link_urls else []
        matches = [
            original
            for keyword, original in keywords.items()
            if keyword in text or any(keyword in href for href in link_urls)
        ]
        if matches:
            report.matches[response_url] = matches

        for href in hrefs:
            target = normalize_url(href, base_url or response_url)
            if target and in_scope(target):
                enqueue(target)

    async def worker(session):
        while True:
            url = await pending.get()
            try:
                await process(session, url)
            except (aiohttp.ClientError, TimeoutError) as error:
                report.errors[url] = str(error) or type(error).__name__
            finally:
                pending.task_done()

    headers = {"User-Agent": "WEB_crawler", "Accept": "text/html,application/xhtml+xml"}
    timeout = aiohttp.ClientTimeout(total=10)
    enqueue(start_url)
    connector = aiohttp.TCPConnector(limit=concurrency)
    async with aiohttp.ClientSession(headers=headers, timeout=timeout, connector=connector) as session:
        async with asyncio.TaskGroup() as group:
            workers = [group.create_task(worker(session)) for _ in range(concurrency)]
            await pending.join()
            for task in workers:
                task.cancel()

    return report


def positive_int(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Введите целое число больше нуля") from None
    if number < 1:
        raise argparse.ArgumentTypeError("Введите целое число больше нуля")
    return number


def print_report(report):
    print("\nСовпадения")
    for url, keywords in sorted(report.matches.items()):
        for keyword in keywords:
            print(f"Слово «{keyword}» найдено на {url}")
    if report.pages == 0:
        print("HTML-страниц для поиска нет.")
    elif not report.matches:
        print("На обработанных страницах совпадений нет.")

    print(f"\nHTML-страниц обработано — {report.pages}")
    print(f"Не-HTML ответов пропущено — {report.skipped}")
    print(f"Редиректов обработано — {len(report.redirects)}")
    print(f"Ошибок обхода — {len(report.errors)}")
    for url, error in sorted(report.errors.items()):
        print(f"[Ошибка] {url} — {error}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Поиск слов на страницах сайта")
    parser.add_argument("url", nargs="?", help="Стартовый URL или домен")
    parser.add_argument("-k", "--keyword", action="append", help="Слово или фраза. Можно указать несколько раз")
    parser.add_argument("-c", "--concurrency", type=positive_int, help="Число одновременных запросов")
    parser.add_argument("--search-link-urls", action="store_true", help="Также искать в адресах ссылок")
    args = parser.parse_args(argv)

    url = (args.url or input("Введите адрес сайта (например, example.com) — ")).strip()
    if "://" not in url:
        url = "https://" + url
    start_url = normalize_url(url)
    if not start_url:
        parser.error("Укажите корректный адрес HTTP или HTTPS без данных для входа")

    keywords = args.keyword
    if keywords is None:
        keywords = input("Введите слова или фразы через запятую — ").split(",")
    if not normalize_keywords(keywords):
        parser.error("Укажите хотя бы одно слово или фразу")

    concurrency = args.concurrency
    while concurrency is None:
        value = input(f"Сколько запросов выполнять одновременно? [{DEFAULT_CONCURRENCY}] ").strip()
        try:
            concurrency = positive_int(value) if value else DEFAULT_CONCURRENCY
        except argparse.ArgumentTypeError as error:
            print(error)

    report = asyncio.run(crawl(start_url, keywords, concurrency, args.search_link_urls))
    print_report(report)
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
