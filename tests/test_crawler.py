import asyncio
import io
import sys
import unittest
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path
from urllib.parse import quote

from aiohttp import web

from crawler import crawl


SCRIPT = Path(__file__).resolve().parents[1] / "crawler.py"


class CrawlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = Counter()
        self.routes = {}
        self.handler = self.serve_routes
        app = web.Application()
        app.router.add_get("/{path:.*}", self.serve)
        runner = web.AppRunner(app)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        self.port = runner.addresses[0][1]
        self.base = f"http://127.0.0.1:{self.port}"

    async def serve(self, request):
        self.requests[request.raw_path] += 1
        return await self.handler(request)

    async def serve_routes(self, request):
        response = self.routes.get(request.raw_path)
        if response is None:
            return self.html("Not found", status=404)
        return response()

    @staticmethod
    def html(text, status=200):
        return web.Response(text=text, content_type="text/html", status=status)

    async def scan(self, keywords, concurrency=3, search_link_urls=False):
        with redirect_stdout(io.StringIO()):
            return await crawl(f"{self.base}/", keywords, concurrency, search_link_urls)

    async def test_resolves_links_after_redirect_and_keeps_query(self):
        self.routes = {
            "/": lambda: self.html('<a href="/old">News</a>'),
            "/old": lambda: web.Response(status=301, headers={"Location": "/news/"}),
            "/news/": lambda: self.html(
                '<a href="item.html?id=1#intro">First</a>'
                '<a href="item.html?id=1#other">Same page</a>'
                '<a href="item.html?id=2&amp;token=A%2FB">Second</a>'
                '<a href="mailto:mail@example.com">Mail</a>'
                f'<a href="http://localhost:{self.port}/external">External</a>'
            ),
            "/news/item.html?id=1": lambda: self.html("Расписание"),
            "/news/item.html?id=2&token=A%2FB": lambda: self.html("Расписание"),
        }

        report = await self.scan(["расписание"])

        self.assertEqual(report.matches, {
            f"{self.base}/news/item.html?id=1": ["расписание"],
            f"{self.base}/news/item.html?id=2&token=A%2FB": ["расписание"],
        })
        self.assertEqual(report.redirects, {f"{self.base}/old": f"{self.base}/news/"})
        self.assertEqual(self.requests, dict.fromkeys(self.routes, 1))
        self.assertFalse(report.errors)

    async def test_reports_failed_requests_and_skips_non_html(self):
        self.routes = {
            "/": lambda: self.html(
                '<a href="/missing">Missing</a><a href="/busy">Busy</a>'
                '<a href="/file">File</a><a href="/offsite">Offsite</a>'
                '<a href="/loop">Loop</a>'
            ),
            "/missing": lambda: self.html("совпадение", status=404),
            "/busy": lambda: self.html("совпадение", status=429),
            "/file": lambda: web.Response(text="совпадение"),
            "/offsite": lambda: web.Response(
                status=302, headers={"Location": f"http://localhost:{self.port}/external"}
            ),
            "/loop": lambda: web.Response(status=302, headers={"Location": "/loop"}),
        }

        report = await self.scan(["совпадение"])

        self.assertFalse(report.matches)
        self.assertEqual(report.pages, 1)
        self.assertEqual(report.skipped, 1)
        self.assertEqual(report.errors[f"{self.base}/missing"], "HTTP 404")
        self.assertEqual(report.errors[f"{self.base}/busy"], "HTTP 429")
        self.assertIn("за пределы области обхода", report.errors[f"{self.base}/offsite"])
        self.assertEqual(report.errors[f"{self.base}/loop"], "Циклический редирект")
        self.assertEqual(self.requests, dict.fromkeys(self.routes, 1))

    async def test_reports_visible_matches_once_and_can_search_link_urls(self):
        word = "расписание"
        self.routes = {
            "/": lambda: self.html(
                f'<a href="/{word}?mode=token">{word.upper()}</a>'
                '<p>Расписание</p><script>hidden-script</script><style>hidden-style</style>'
                '<p hidden>hidden-text</p>'
            ),
            f"/{quote(word)}?mode=token": lambda: web.Response(status=204),
        }
        keywords = ["расписание", "РАСПИСАНИЕ", "token", "hidden-script", "hidden-style", "hidden-text"]

        text_only = await self.scan(keywords)
        with_links = await self.scan(keywords, search_link_urls=True)

        self.assertEqual(text_only.matches, {f"{self.base}/": ["расписание"]})
        self.assertEqual(with_links.matches, {f"{self.base}/": ["расписание", "token"]})
        self.assertFalse(text_only.errors)
        self.assertFalse(with_links.errors)

    async def test_limits_simultaneous_requests(self):
        active = 0
        peak = 0

        async def handler(request):
            nonlocal active, peak
            if request.path == "/":
                return self.html("".join(f'<a href="/{i}">Page</a>' for i in range(8)))
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.03)
                return self.html("content")
            finally:
                active -= 1

        self.handler = handler
        for limit in (1, 3):
            with self.subTest(limit=limit):
                peak = 0
                report = await self.scan(["content"], concurrency=limit)
                self.assertEqual(peak, limit)
                self.assertEqual(report.pages, 9)
                self.assertEqual(len(report.matches), 8)

    async def test_handles_a_chain_longer_than_the_recursion_limit(self):
        depth = sys.getrecursionlimit() + 5

        async def handler(request):
            index = int(request.path[1:] or 0)
            if index == depth:
                return self.html("target")
            return self.html(f'<a href="/{index + 1}">Next</a>')

        self.handler = handler
        report = await self.scan(["target"], concurrency=1)

        self.assertEqual(report.pages, depth + 1)
        self.assertEqual(report.matches, {f"{self.base}/{depth}": ["target"]})

    async def test_interactive_cli_reprompts_for_invalid_concurrency(self):
        self.routes = {"/": lambda: self.html("target")}
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(SCRIPT),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate(f"{self.base}\ntarget\n0\n1\n".encode())
        output = stdout.decode()
        self.assertEqual(process.returncode, 0, stderr.decode())
        self.assertEqual(output.count("Сколько запросов"), 2)
        self.assertIn(f"Слово «target» найдено на {self.base}/", output)
        self.assertIn("Ошибок обхода — 0", output)

    async def test_cli_arguments_return_nonzero_on_http_errors(self):
        self.routes = {"/": lambda: self.html("target", status=429)}
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(SCRIPT),
            self.base,
            "-k", "target",
            "--concurrency", "1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        output = stdout.decode()
        self.assertEqual(process.returncode, 1, stderr.decode())
        self.assertIn("HTTP 429", output)
        self.assertIn("Ошибок обхода — 1", output)
        self.assertNotIn("Слово «target»", output)
