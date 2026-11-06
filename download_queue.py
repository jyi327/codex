"""Ordered, duplicate-free song selections and per-song outcomes."""
class DownloadQueue:
    def __init__(self):
        self.items = {}

    def add(self, song):
        url = song['url']
        if url in self.items:
            return False
        self.items[url] = {**song, 'status': 'Waiting', 'percent': None, 'error': ''}
        return True

    def remove(self, url):
        self.items.pop(url, None)

    def clear(self):
        self.items.clear()

    def pending(self):
        return [url for url, song in self.items.items() if song['status'] in ('Waiting', 'Stopped')]

    def failed(self):
        return [url for url, song in self.items.items() if song['status'] == 'Failed']

    def update(self, url, status, percent=None, error=''):
        if url in self.items:
            self.items[url].update(status=status, percent=percent, error=error)

    def counts(self):
        return (sum(song['status'] == 'Done' for song in self.items.values()), len(self.failed()))
