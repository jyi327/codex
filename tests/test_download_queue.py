import unittest
from download_queue import DownloadQueue


class QueueTests(unittest.TestCase):
    def test_duplicates_preserve_order_and_original_status(self):
        queue = DownloadQueue()
        first = {'title': 'First', 'url': 'https://www.youtube.com/watch?v=abcdefghijk'}
        second = {'title': 'Second', 'url': 'https://www.youtube.com/watch?v=lmnopqrstuv'}
        self.assertTrue(queue.add(first))
        queue.update(first['url'], 'Done')
        self.assertTrue(queue.add(second))
        self.assertFalse(queue.add(first))
        self.assertEqual(list(queue.items), [first['url'], second['url']])
        self.assertEqual(queue.items[first['url']]['status'], 'Done')

    def test_retry_contains_failed_songs_only(self):
        queue = DownloadQueue()
        for url in ('one', 'two', 'three'):
            queue.add({'title': url, 'url': url})
        queue.update('one', 'Done')
        queue.update('two', 'Failed', error='network')
        queue.update('three', 'Waiting')
        self.assertEqual(queue.failed(), ['two'])
        self.assertEqual(queue.pending(), ['three'])
        self.assertEqual(queue.counts(), (1, 1))
        queue.update('two', 'Done')
        self.assertEqual(queue.counts(), (2, 0))

    def test_removing_one_and_clearing_do_not_touch_other_entries(self):
        queue = DownloadQueue()
        for url in ('one', 'two'):
            queue.add({'title': url, 'url': url})
        queue.remove('one')
        self.assertEqual(queue.pending(), ['two'])
        queue.update('two', 'Stopped')
        self.assertEqual(queue.pending(), ['two'])
        queue.clear()
        self.assertEqual(queue.items, {})
