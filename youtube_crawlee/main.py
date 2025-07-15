from datetime import timedelta

from apify import Actor

from crawlee import ConcurrencySettings, Request
from crawlee.crawlers import PlaywrightCrawler

from .hooks import pre_hook
from .routes import router


async def main() -> None:
    """The crawler entry point."""
    async with Actor:

        actor_input = await Actor.get_input()

        max_items = actor_input.get('maxItems', 1)
        channels = actor_input.get('channelNames', ['Apify'])
        proxy = await Actor.create_proxy_configuration(actor_proxy_input=actor_input.get('proxySettings'))

        # Create a crawler instance with the router
        crawler = PlaywrightCrawler(
            # Limit scraping intensity by setting a limit on requests per minute
            concurrency_settings=ConcurrencySettings(max_tasks_per_minute=50),
            # We'll configure the `router` in the next step
            request_handler=router,
            # Increase the timeout for the request handling pipeline
            request_handler_timeout=timedelta(seconds=120),
            # You can use `False` during development. But for production, it's always `True`
            headless=True,
            # Limit requests per crawl for testing purposes
            max_requests_per_crawl=100,
            proxy_configuration=proxy,
        )
        # Set hook for prepare context before navigation on each request
        crawler.pre_navigation_hook(pre_hook)

        await crawler.run(
            [
                Request.from_url(f'https://www.youtube.com/@{channel}/videos', user_data={'limit': max_items})
                for channel in channels
            ]
        )
