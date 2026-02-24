# WEB_crawler

1) Установить Python-библиотеки:
py -m pip install requests beautifulsoup4 asyncio aiohttp

2) Добавить в KEYWORDS внутри скрипта свои слова для поиска

3) Запустить скрипт:
py fast_crawler.py

Если быстрый скрипт блокируется сервером, то можно попробовать понизить значение одновременных запросов - MAX_TASKS = 30, либо запустить медленный скрипт:
py crawler.py

4) Ввести адрес сайта, например example.com (без https://)
