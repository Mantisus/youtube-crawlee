from crawlee.crawlers import PlaywrightPreNavCrawlingContext


async def pre_hook(context: PlaywrightPreNavCrawlingContext) -> None:
    """Prepare context before navigation."""
    crawler_state = await context.use_state()
    # Check if there are previously collected cookies in the crawler state and set them for the session
    if 'cookies' in crawler_state and context.session:
        cookies = crawler_state['cookies']
        # Set cookies for the session
        context.session.cookies.set_cookies_from_playwright_format(cookies)
    # Block requests to resources that aren't needed for parsing
    # This is similar to the default value, but we don't block `css` as it is needed for Player loading
    await context.block_requests(
        url_patterns=['.webp', '.jpg', '.jpeg', '.png', '.svg', '.gif', '.woff', '.pdf', '.zip']
    )
