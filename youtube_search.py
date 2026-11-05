"""Read-only YouTube search through the bundled downloader; no API key needed."""
import json
import math
import re
import subprocess
import sys
import time


class SearchCancelled(Exception):
    pass


def validate_query(query):
    query = ' '.join(query.split())
    if not query:
        raise ValueError('Enter a song name or artist.')
    if len(query) > 200:
        raise ValueError('Use a shorter song name.')
    if '://' in query or query.lower().startswith(('www.', 'youtu.be/')):
        raise ValueError('Enter a song name, not a link.')
    return query


def parse_results(output):
    data = json.loads(output.lstrip('\ufeff'))
    if not isinstance(data, dict) or not isinstance(data.get('entries'), list):
        raise ValueError('YouTube returned an unexpected search response. Please try again.')
    results, seen = [], set()
    for entry in data['entries']:
        if not isinstance(entry, dict):
            continue
        video_id = entry.get('id', '')
        if not isinstance(video_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id) or video_id in seen:
            continue
        if entry.get('is_live') or entry.get('live_status') in ('is_live', 'is_upcoming'):
            continue
        duration = entry.get('duration')
        if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0:
            duration = None
        seen.add(video_id)
        results.append({
            'id': video_id,
            'title': ' '.join(str(entry.get('title') or 'Untitled song').split())[:250],
            'channel': ' '.join(str(entry.get('channel') or entry.get('uploader') or '').split())[:120],
            'duration': duration,
            # Construct our own URL instead of trusting redirects in search output.
            'url': 'https://www.youtube.com/watch?v=' + video_id,
        })
        if len(results) == 5:
            break
    return results


def search(query, downloader, cancel, stop_process, timeout=45):
    query = validate_query(query)
    if cancel.is_set():
        raise SearchCancelled()
    command = downloader + [
        '--ignore-config', '--encoding', 'utf-8', '--flat-playlist', '--dump-single-json', '--skip-download',
        '--no-warnings', '--socket-timeout', '15', '--extractor-retries', '1',
        '--playlist-end', '5', '--', 'ytsearch5:' + query,
    ]
    deadline = time.monotonic() + timeout
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding='utf-8', errors='replace',
                          start_new_session=sys.platform != 'win32',
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
        try:
            while True:
                if cancel.is_set():
                    raise SearchCancelled()
                if time.monotonic() >= deadline:
                    raise TimeoutError('Search timed out. Check your connection and try again.')
                try:
                    output, error = process.communicate(timeout=.15)
                    break
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if process.poll() is None:
                stop_process(process)
                process.communicate(timeout=5)
        if cancel.is_set():
            raise SearchCancelled()
        if process.returncode:
            raise RuntimeError(error.strip()[-2000:] or 'YouTube search failed. Please try again.')
        return parse_results(output)
