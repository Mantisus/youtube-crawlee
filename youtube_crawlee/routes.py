from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from yarl import URL

from crawlee import Request, RequestOptions, RequestTransformAction
from crawlee.crawlers import PlaywrightCrawlingContext
from crawlee.router import Router

if TYPE_CHECKING:
    from playwright.async_api import Request as PlaywrightRequest
    from playwright.async_api import Route as PlaywrightRoute

router = Router[PlaywrightCrawlingContext]()


def request_domain_transform(request_param: RequestOptions) -> RequestOptions | RequestTransformAction:
    """Transform request before adding it to the queue."""
    if 'consent.youtube' in request_param['url']:
        request_param['url'] = request_param['url'].replace('consent.youtube', 'www.youtube')
        return request_param
    return 'unchanged'


async def extract_transcript_url(context: PlaywrightCrawlingContext) -> str | None:
    """Extract the transcript URL from request intercepted by Playwright."""
    transcript_url = None

    # Define a handler for the transcript request
    # This will be called when the page requests the transcript
    async def handle_transcript_request(route: PlaywrightRoute, request: PlaywrightRequest) -> None:
        nonlocal transcript_url

        transcript_url = request.url

        await route.fulfill(status=200)

    # Set up a route to intercept requests to the transcript API
    await context.page.route('**/api/timedtext**', handle_transcript_request)

    # Click the subtitles button to trigger the transcript request
    await context.page.click('.ytp-subtitles-button')

    # Wait for the transcript URL to be set
    # We use `asyncio.wait_for` to avoid waiting indefinitely
    while not transcript_url:
        await asyncio.sleep(1)
        context.log.info('Waiting for transcript URL...')

    return transcript_url


@router.default_handler
async def default_handler(context: PlaywrightCrawlingContext) -> None:
    """Handle requests that do not match any specific handler."""
    context.log.info(f'Processing {context.request.url} ...')

    # Get the limit from user_data, default to 10 if not set
    limit = context.request.user_data.get('limit', 10)
    if not isinstance(limit, int):
        raise TypeError('Limit must be an integer')

    # Wait for the page to load
    await context.page.locator('h1').first.wait_for(state='attached')

    # Check if there's a GDPR popup on the page requiring consent for cookie usage
    cookies_button = context.page.locator('button[aria-label*="Accept"]').first
    if await cookies_button.is_visible():
        await cookies_button.click()

        # Save cookies for later use with other sessions
        cookies_state = [cookie for cookie in await context.page.context.cookies() if cookie['name'] == 'SOCS']
        crawler_state = await context.use_state()
        crawler_state['cookies'] = cookies_state

    # Wait until at least one video loads
    await context.page.locator('a[href*="watch"]').first.wait_for()

    # Create a background task for infinite scrolling
    scroll_task: asyncio.Task[None] = asyncio.create_task(context.infinite_scroll())
    # Scroll the page to the end until we reach the limit or finish scrolling
    while not scroll_task.done():
        # Extract links to videos
        requests = await context.extract_links(
            selector='a[href*="watch"]',
            label='video',
            transform_request_function=request_domain_transform,
            strategy='same-domain',
        )
        # Create a dictionary to avoid duplicates
        requests_map = {request.id: request for request in requests}
        # If the limit is reached, cancel the scrolling task
        if len(requests_map) >= limit:
            scroll_task.cancel()
            break
        # Switch the asynchronous context to allow other tasks to execute
        await asyncio.sleep(0.2)
    else:
        # If the scroll task is done, we can safely assume that we have reached the end of the page
        requests = await context.extract_links(
            selector='a[href*="watch"]',
            label='video',
            transform_request_function=request_domain_transform,
            strategy='same-domain',
        )
        requests_map = {request.id: request for request in requests}

    requests = list(requests_map.values())
    requests = requests[:limit]

    # Add the requests to the queue
    await context.enqueue_links(requests=requests)


@router.handler('video')
async def video_handler(context: PlaywrightCrawlingContext) -> None:
    """Handle video requests."""
    context.log.info(f'Processing video {context.request.url} ...')
    # extract video data from the page
    video_data = await context.page.evaluate('window.ytInitialPlayerResponse')
    main_data = {
        'url': context.request.url,
        'title': video_data['videoDetails']['title'],
        'description': video_data['videoDetails']['shortDescription'],
        'channel': video_data['videoDetails']['author'],
        'channel_id': video_data['videoDetails']['channelId'],
        'video_id': video_data['videoDetails']['videoId'],
        'duration': video_data['videoDetails']['lengthSeconds'],
        'keywords': video_data['videoDetails']['keywords'],
        'view_count': video_data['videoDetails']['viewCount'],
        'like_count': video_data['microformat']['playerMicroformatRenderer']['likeCount'],
        'is_shorts': video_data['microformat']['playerMicroformatRenderer']['isShortsEligible'],
        'publish_date': video_data['microformat']['playerMicroformatRenderer']['publishDate'],
    }

    # Try to extract the transcript URL
    try:
        transcript_url = await asyncio.wait_for(extract_transcript_url(context), timeout=20)
    except asyncio.TimeoutError:
        transcript_url = None

    if transcript_url:
        transcript_url = str(URL(transcript_url).without_query_params('fmt'))
        context.log.info(f'Found transcript URL: {transcript_url}')
        await context.add_requests(
            [Request.from_url(transcript_url, label='transcript', user_data={'video_data': main_data})]
        )
    else:
        await context.push_data(main_data)


@router.handler('transcript')
async def transcript_handler(context: PlaywrightCrawlingContext) -> None:
    """Handle transcript requests."""
    context.log.info(f'Processing transcript {context.request.url} ...')

    # Get the main video data extracted in `video_handler`
    video_data = context.request.user_data.get('video_data', {})

    try:
        # Get XML data from the response
        root = ET.fromstring(await context.response.text())

        # Extract text elements from XML
        transcript_data = [text_element.text.strip() for text_element in root.findall('.//text') if text_element.text]

        # Enrich video data by adding the transcript
        video_data['transcript'] = '\n'.join(transcript_data)

        # Save the data to Dataset
        await context.push_data(video_data)
    except ET.ParseError:
        context.log.warning('Incorect XML Response')
        # Save the video data without the transcript
        await context.push_data(video_data)
