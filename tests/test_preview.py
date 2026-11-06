import json
import os
import shutil
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

import app
import preview


class PreviewTests(unittest.TestCase):
    def test_streaming_sample_does_not_download_or_launch_a_browser(self):
        metadata = json.dumps({'url': 'https://r1.googlevideo.com/videoplayback?token=secret',
                               'http_headers': {'User-Agent': 'test', 'Referer': 'https://www.youtube.com/'}})
        states = []
        with patch.object(preview, 'run_process', side_effect=[metadata, '']) as run:
            preview.play('https://www.youtube.com/watch?v=abcdefghijk', ['downloader'], 'ffplay',
                         threading.Event(), lambda p: None, states.append)
        extraction = run.call_args_list[0].args[0]
        playback = run.call_args_list[1].args[0]
        self.assertIn('--skip-download', extraction)
        self.assertIn('--no-playlist', extraction)
        self.assertNotIn('--extract-audio', extraction)
        self.assertEqual(playback[0], 'ffplay')
        self.assertIn('-nodisp', playback)
        self.assertIn('-vn', playback)
        self.assertEqual(playback[playback.index('-t') + 1], '30')
        self.assertEqual(states, ['playing'])

    def test_invalid_song_and_unexpected_stream_are_rejected(self):
        for url in ['https://example.com/song', 'file:///song.mp3', 'https://www.youtube.com/watch?v=bad']:
            with patch.object(preview, 'run_process') as run, self.assertRaises(ValueError):
                preview.play(url, ['downloader'], 'ffplay', threading.Event(), lambda p: None, lambda s: None)
            run.assert_not_called()
        for url in ['file:///song.mp3', 'http://r1.googlevideo.com/audio', 'https://evilgooglevideo.com/audio']:
            with self.assertRaises(ValueError):
                preview.stream_options(json.dumps({'url': url}))
        with self.assertRaises(ValueError):
            preview.stream_options(json.dumps({'url': 'https://r1.googlevideo.com/audio', 'http_headers': {'Referer': 'ok\r\nInjected: yes'}}))

    def test_cancel_during_resolution_never_starts_player(self):
        cancel = threading.Event()
        def resolve(*args, **kwargs):
            cancel.set()
            return json.dumps({'url': 'https://r1.googlevideo.com/audio'})
        with patch.object(preview, 'run_process', side_effect=resolve) as run:
            with self.assertRaises(preview.PreviewCancelled):
                preview.play('https://www.youtube.com/watch?v=abcdefghijk', ['downloader'], 'ffplay',
                             cancel, lambda p: None, lambda s: self.fail('Started cancelled preview'))
        self.assertEqual(run.call_count, 1)

    def test_cancelling_or_timing_out_reaps_real_process(self):
        for cancel_after in [True, False]:
            cancel = threading.Event()
            stopped = []
            def stop(process):
                stopped.append(process)
                app.stop_process_tree(process)
            timer = threading.Timer(.3, cancel.set)
            if cancel_after:
                timer.start()
            try:
                with self.assertRaises(preview.PreviewCancelled if cancel_after else TimeoutError):
                    preview.run_process([sys.executable, '-c', 'import time;time.sleep(60)'], cancel,
                                        stop, timeout=3 if cancel_after else .3)
            finally:
                timer.cancel()
            self.assertEqual(len(stopped), 1)
            self.assertIsNotNone(stopped[0].poll())

    def test_process_error_does_not_expose_signed_urls(self):
        with self.assertRaisesRegex(RuntimeError, '^Preview unavailable') as error:
            preview.run_process([sys.executable, '-c', 'import sys;sys.stderr.write("signed-secret-url");sys.exit(1)'],
                                threading.Event(), app.stop_process_tree, timeout=5)
        self.assertNotIn('signed-secret', str(error.exception))

    def test_player_error_is_reported_even_when_player_exits_zero(self):
        with self.assertRaisesRegex(RuntimeError, '^Preview unavailable'):
            preview.run_process([sys.executable, '-c', 'import sys;sys.stderr.write("Failed to open audio")'],
                                threading.Event(), app.stop_process_tree, timeout=5, reject_stderr=True)

    @unittest.skipUnless(shutil.which('ffplay'), 'ffplay is required for the offline player test')
    def test_real_player_opens_and_decodes_audio_without_a_sound_device(self):
        preview.smoke_test(shutil.which('ffplay'))
