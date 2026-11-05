import json
import sys
import threading
import unittest
from unittest.mock import patch
import app
import youtube_search


class SearchTests(unittest.TestCase):
    def test_query_accepts_names_and_rejects_links(self):
        self.assertEqual(youtube_search.validate_query('  周杰伦   夜曲 '), '周杰伦 夜曲')
        for query in ('', 'https://youtu.be/abcdefghijk', 'www.youtube.com/watch?v=abcdefghijk'):
            with self.assertRaises(ValueError):
                youtube_search.validate_query(query)

    def test_results_validate_ids_remove_duplicates_and_skip_live_streams(self):
        data = {'entries': [
            {'id': 'abcdefghijk', 'title': 'A song', 'channel': 'Artist', 'duration': 185, 'url': 'https://untrusted.example/redirect'},
            {'id': 'abcdefghijk', 'title': 'Duplicate'},
            {'id': 'invalid', 'title': 'Not a video'},
            {'id': 'lmnopqrstuv', 'title': 'Live stream', 'is_live': True},
            {'id': '0123456789_', 'title': '夜曲', 'uploader': 'Singer'},
            None,
        ]}
        results = youtube_search.parse_results(json.dumps(data))
        self.assertEqual([result['title'] for result in results], ['A song', '夜曲'])
        self.assertEqual(results[0]['url'], 'https://www.youtube.com/watch?v=abcdefghijk')
        self.assertEqual(results[0]['duration'], 185)
        self.assertIsNone(results[1]['duration'])

    def test_search_runs_read_only_and_parses_real_process_output(self):
        output = json.dumps({'entries': [{'id': 'abcdefghijk', 'title': 'Example'}]})
        code = f'import sys; assert "--skip-download" in sys.argv; assert "--flat-playlist" in sys.argv; print({output!r})'
        results = youtube_search.search('Example', [sys.executable, '-c', code], threading.Event(), app.stop_process_tree)
        self.assertEqual(results[0]['title'], 'Example')

    def test_search_can_be_cancelled_while_process_is_silent(self):
        cancel = threading.Event()
        timer = threading.Timer(.2, cancel.set)
        timer.start()
        try:
            with self.assertRaises(youtube_search.SearchCancelled):
                youtube_search.search('Example', [sys.executable, '-c', 'import time; time.sleep(10)'], cancel, app.stop_process_tree)
        finally:
            timer.cancel()

    def test_timeout_stops_search(self):
        with self.assertRaises(TimeoutError):
            youtube_search.search('Example', [sys.executable, '-c', 'import time; time.sleep(10)'], threading.Event(), app.stop_process_tree, timeout=.2)

    def test_search_failure_does_not_become_empty_success(self):
        with self.assertRaisesRegex(RuntimeError, 'Network unavailable'):
            youtube_search.search('Example', [sys.executable, '-c', "import sys; print('Network unavailable', file=sys.stderr); sys.exit(1)"], threading.Event(), app.stop_process_tree)


if __name__ == '__main__':
    unittest.main()
