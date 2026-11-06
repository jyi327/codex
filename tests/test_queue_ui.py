import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import app


@unittest.skipUnless(app.sys.platform == 'win32' or os.environ.get('DISPLAY'), 'GUI requires Windows or a display')
class QueueUITests(unittest.TestCase):
    def setUp(self):
        self.root = app.tk.Tk()
        self.root.withdraw()
        with patch.object(app, 'removable_drives', return_value=['E:\\']):
            self.ui = app.App(self.root)
        self.songs = [
            {'title': 'First song', 'channel': 'Artist', 'duration': 180, 'url': 'https://www.youtube.com/watch?v=abcdefghijk'},
            {'title': 'Second song', 'channel': 'Artist', 'duration': 210, 'url': 'https://www.youtube.com/watch?v=lmnopqrstuv'},
            {'title': 'Third song', 'channel': 'Artist', 'duration': 200, 'url': 'https://www.youtube.com/watch?v=0123456789_'},
        ]

    def tearDown(self):
        self.ui.destroy_window()

    def test_selections_stay_across_searches_and_wrong_song_can_be_removed(self):
        self.ui.finish_search({'search_results': self.songs[:1]})
        self.ui.select_song(0)
        self.ui.finish_search({'search_results': self.songs[1:]})
        self.ui.select_song(0)
        self.assertEqual(list(self.ui.queue.items), [self.songs[0]['url'], self.songs[1]['url']])
        self.ui.finish_search({'search_results': self.songs})
        self.assertEqual(self.ui.result_widgets[0][1].cget('text'), '✓')
        self.ui.select_song(0)
        self.assertNotIn(self.songs[0]['url'], self.ui.queue.items)
        self.ui.remove_song(self.songs[1]['url'])
        self.assertEqual(self.ui.queue.items, {})

    def test_cancel_clears_all_selections_without_ejecting(self):
        self.ui.finish_search({'search_results': self.songs})
        self.ui.select_song(0)
        self.ui.select_song(1)
        self.assertEqual(self.ui.cancel_button.cget('state'), 'normal')
        with patch.object(app.windows_usb, 'eject_usb') as eject:
            self.ui.cancel()
        self.assertEqual(self.ui.queue.items, {})
        self.assertEqual(len(self.ui.results), 3)
        self.assertEqual(self.ui.drive.get(), 'E:\\')
        self.assertFalse(self.ui.cancel_event.is_set())
        self.assertEqual(self.ui.cancel_button.cget('state'), 'disabled')
        eject.assert_not_called()

    def test_retry_starts_only_failed_songs(self):
        for song in self.songs:
            self.ui.queue.add(song)
        self.ui.queue.update(self.songs[0]['url'], 'Done')
        self.ui.queue.update(self.songs[1]['url'], 'Failed')
        with tempfile.TemporaryDirectory() as work:
            self.ui.drive.set(work)
            with patch.object(app, 'removable_drives', return_value=[]), patch.object(app.shutil, 'which', return_value='tool'), patch.object(app, 'downloader_command', return_value=['downloader']), patch.object(app.sys, 'frozen', True, create=True), patch.object(app.threading, 'Thread') as worker:
                self.ui.start(retry_failed=True)
            self.assertEqual(worker.call_args.kwargs['args'][0], [self.songs[1]['url']])
        self.assertEqual(self.ui.queue.items[self.songs[0]['url']]['status'], 'Done')

    def test_partial_failure_summary_and_sequential_copying(self):
        for song in self.songs:
            self.ui.queue.add(song)
        order = []
        copy = app.copy_verified

        def download(command, *args):
            video_id = command[-1].split('v=')[1]
            order.append('download:' + video_id)
            if video_id == 'lmnopqrstuv':
                return subprocess.CompletedProcess(command, 1, '', 'Test network failure')
            cache = Path(command[command.index('--paths') + 1])
            source = cache / (video_id + '.mp3')
            source.write_bytes(b'audio')
            Path(command[command.index('--print-to-file') + 2]).write_text(str(source), encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, '', '')

        def copying(source, *args, **kwargs):
            order.append('copy:' + source.stem)
            return copy(source, *args, **kwargs)

        with tempfile.TemporaryDirectory() as work, patch.dict(app.os.environ, {'LOCALAPPDATA': work}), patch.object(app.windows_usb, 'describe_usb', return_value=object()), patch.object(app, 'download_with_progress', side_effect=download), patch.object(app, 'copy_verified', side_effect=copying):
            destination = Path(work) / 'usb'
            destination.mkdir()
            self.ui.busy = True
            self.ui.run([song['url'] for song in self.songs], destination)
            self.ui.poll()
        self.assertEqual(self.ui.queue.counts(), (2, 1))
        self.assertIn('2 songs copied · 1 failed', self.ui.progress_label.cget('text'))
        self.assertEqual(self.ui.retry_button.winfo_manager(), 'grid')
        self.assertEqual(order, ['download:abcdefghijk', 'copy:abcdefghijk', 'download:lmnopqrstuv', 'download:0123456789_', 'copy:0123456789_'])

    def test_main_controls_fit_small_laptop_window_without_page_scroll(self):
        self.root.geometry('1000x560')
        self.root.deiconify()
        self.root.update()
        self.assertNotIsInstance(self.ui.page, app.ctk.CTkScrollableFrame)
        for widget in (self.ui.search_entry, self.ui.result_frame, self.ui.queue_frame, self.ui.drive, self.ui.progress_bar, self.ui.button, self.ui.cancel_button, self.ui.details_check):
            self.assertGreaterEqual(widget.winfo_rooty(), self.root.winfo_rooty())
            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), self.root.winfo_rooty() + self.root.winfo_height())
        self.ui.queue.add(self.songs[0])
        self.ui.queue.update(self.songs[0]['url'], 'Failed')
        self.ui.show_done(True)
        self.ui.update_start_state()
        self.root.update()
        for widget in (self.ui.next_button, self.ui.done_button, self.ui.retry_button):
            self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), self.root.winfo_rootx() + self.root.winfo_width())
            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), self.root.winfo_rooty() + self.root.winfo_height())

    def test_unavailable_thumbnail_is_not_retried_forever(self):
        self.ui.result_thumbnail_slots = {'abcdefghijk': []}
        self.ui.thumbnail_pending.add('abcdefghijk')
        with patch.object(self.ui.thumbnail_pool, 'submit') as submit:
            self.ui.apply_thumbnail({'thumbnail_id': 'abcdefghijk', 'picture': None})
        submit.assert_not_called()

    def test_preview_button_does_not_select_a_song_and_stop_keeps_usb(self):
        self.ui.finish_search({'search_results': self.songs})
        with patch.object(app.shutil, 'which', return_value=app.sys.executable), patch.object(app.threading, 'Thread'):
            self.ui.preview_widgets[self.songs[0]['url']].invoke()
        token = self.ui.preview_token
        cancelled = self.ui.preview_cancel
        self.assertEqual(self.ui.queue.items, {})
        self.assertEqual(self.ui.preview_url, self.songs[0]['url'])
        self.ui.apply_preview_event({'preview_token': token, 'preview_state': 'playing'})
        self.assertEqual(self.ui.preview_widgets[self.songs[0]['url']].cget('text'), '■ STOP')
        with patch.object(app.windows_usb, 'eject_usb') as eject:
            self.ui.preview_widgets[self.songs[0]['url']].invoke()
        self.assertTrue(cancelled.is_set())
        self.assertIsNone(self.ui.preview_url)
        self.assertEqual(self.ui.drive.get(), 'E:\\')
        eject.assert_not_called()

    def test_switching_previews_cancels_previous_and_ignores_stale_events(self):
        self.ui.finish_search({'search_results': self.songs})
        with patch.object(app.shutil, 'which', return_value=app.sys.executable), patch.object(app.threading, 'Thread'):
            self.ui.preview_song(0)
            previous = self.ui.preview_cancel
            token = self.ui.preview_token
            self.ui.preview_song(1)
        self.assertTrue(previous.is_set())
        self.ui.apply_preview_event({'preview_token': token, 'preview_state': 'error'})
        self.assertEqual(self.ui.preview_url, self.songs[1]['url'])
        self.assertEqual(self.ui.queue.items, {})
        self.ui.select_song(0)
        self.assertEqual(self.ui.preview_url, self.songs[1]['url'])
        self.assertIn(self.songs[0]['url'], self.ui.queue.items)

    def test_preview_waits_for_previous_player_before_starting(self):
        order = []
        previous = MagicMock()
        previous.join.side_effect = lambda: order.append('previous stopped')
        with patch.object(app.preview, 'play', side_effect=lambda *args: order.append('new playback')):
            self.ui.preview_worker(1, self.songs[0]['url'], ['downloader'], 'ffplay', app.threading.Event(), previous)
        self.assertEqual(order, ['previous stopped', 'new playback'])

    def test_search_and_download_more_stop_preview_without_removing_songs(self):
        self.ui.finish_search({'search_results': self.songs})
        self.ui.select_song(0)
        self.ui.search_entry.insert(0, 'Another song')
        with patch.object(app.threading, 'Thread'):
            cancelled = self.ui.preview_cancel
            self.ui.search_songs()
        self.assertTrue(cancelled.is_set())
        self.ui.finish_search({'search_results': self.songs})
        self.ui.preview_cancel = app.threading.Event()
        cancelled = self.ui.preview_cancel
        self.ui.next_song()
        self.assertTrue(cancelled.is_set())
        self.assertIn(self.songs[0]['url'], self.ui.queue.items)

    def test_preview_buttons_fit_small_window_and_disable_during_download(self):
        self.root.geometry('1000x560')
        self.ui.finish_search({'search_results': self.songs})
        self.root.deiconify()
        self.root.update()
        for row, check in self.ui.result_widgets:
            self.assertLessEqual(row.winfo_rootx() + row.winfo_width(), self.root.winfo_rootx() + self.root.winfo_width())
        for button in self.ui.preview_widgets.values():
            self.assertLessEqual(button.winfo_rootx() + button.winfo_width(), self.root.winfo_rootx() + self.root.winfo_width())
        self.ui.busy = True
        self.ui.set_controls(True)
        self.assertTrue(all(b.cget('state') == 'disabled' for b in self.ui.preview_widgets.values()))
        self.ui.preview_song(0)
        self.assertIsNone(self.ui.preview_url)

    def test_start_and_close_cancel_preview(self):
        self.ui.finish_search({'search_results': self.songs})
        self.ui.select_song(0)
        cancelled = self.ui.preview_cancel
        with tempfile.TemporaryDirectory() as work:
            self.ui.drive.set(work)
            with patch.object(app, 'removable_drives', return_value=[]), patch.object(app.shutil, 'which', return_value='tool'), patch.object(app, 'downloader_command', return_value=['downloader']), patch.object(app.sys, 'frozen', True, create=True), patch.object(app.threading, 'Thread'):
                self.ui.start()
        self.assertTrue(cancelled.is_set())
        self.ui.busy = False
        self.ui.preview_cancel = app.threading.Event()
        cancelled = self.ui.preview_cancel
        with patch.object(self.ui, 'prepare_exit') as closing:
            self.ui.close()
        self.assertTrue(cancelled.is_set())
        closing.assert_called_once()
