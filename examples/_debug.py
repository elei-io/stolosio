import json
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from websockets.asyncio.client import connect


def with_reference(url: str, reference: str) -> str:
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("stolosio.session.reference", reference))
    return urlunsplit((*parsed[:3], urlencode(query), parsed.fragment))


async def observe(debug_url: str, reference: str) -> list[dict]:
    events = []
    async with connect(with_reference(debug_url, reference)) as websocket:
        async for raw_event in websocket:
            events.append(json.loads(raw_event))
    return events
