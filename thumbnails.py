"""Bounded thumbnail reads from a fixed YouTube image host."""
from io import BytesIO
import re
from urllib.request import build_opener, HTTPRedirectHandler, Request
from PIL import Image, ImageOps


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(video_id):
    if not isinstance(video_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        raise ValueError('Invalid video ID')
    request = Request(f'https://i.ytimg.com/vi/{video_id}/mqdefault.jpg', headers={'User-Agent': 'YouTubeToUSB/1.0'})
    with build_opener(NoRedirect()).open(request, timeout=3) as response:
        data = response.read(1_000_001)
    if len(data) > 1_000_000:
        raise ValueError('Thumbnail too large')
    with Image.open(BytesIO(data)) as image:
        if image.width > 1920 or image.height > 1080:
            raise ValueError('Thumbnail dimensions too large')
        return ImageOps.fit(image.convert('RGB'), (160, 90), method=Image.Resampling.LANCZOS)
