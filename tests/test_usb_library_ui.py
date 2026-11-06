import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import app
import usb_library


@unittest.skipUnless(app.sys.platform == 'win32' or os.environ.get('DISPLAY'), 'GUI requires Windows or a display')
class USBLibraryUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        (self.folder / 'Song.mp3').write_bytes(b'audio')
        (self.folder / 'Classics').mkdir()
        (self.folder / 'Classics' / 'Child.wav').write_bytes(b'audio')
        self.root = app.tk.Tk()
        self.root.withdraw()
        with patch.object(app, 'removable_drives', return_value=[]):
            self.ui = app.App(self.root)
        self.ui.drive.set(str(self.folder))
        self.refresh()

    def tearDown(self):
        self.ui.destroy_window()
        self.ui.usb_library_pool.shutdown(wait=True, cancel_futures=True)
        self.temp.cleanup()

    def refresh(self):
        self.ui.sync_usb_library(force=True)
        self.ui.usb_scan_future.result(timeout=5)
        self.ui.poll()

    def finish_delete(self):
        self.ui.usb_library_pool.submit(lambda: None).result(timeout=5)
        self.ui.poll()
        if not self.ui.ejecting:
            self.ui.usb_scan_future.result(timeout=5)
            self.ui.poll()

    def test_folder_expansion_and_automatic_external_refresh(self):
        self.assertEqual(self.ui.usb_heading.cget('text'), 'USB (2)')
        self.assertEqual(len(self.ui.usb_delete_buttons), 1)
        self.ui.toggle_usb_folder(('Classics',))
        self.assertEqual(len(self.ui.usb_delete_buttons), 2)
        (self.folder / 'New.flac').write_bytes(b'audio')
        with patch.object(app, 'removable_drives', return_value=[]):
            self.ui.monitor_drives()
        self.ui.usb_scan_future.result(timeout=5)
        self.ui.poll()
        self.assertEqual(self.ui.usb_heading.cget('text'), 'USB (3)')
        self.assertIn(('Classics',), self.ui.usb_expanded)
        self.assertEqual(len(self.ui.usb_delete_buttons), 3)
        labels = [label.cget('text') for row in self.ui.usb_frame.winfo_children()
                  for label in row.winfo_children() if isinstance(label, app.ctk.CTkLabel)]
        self.assertIn('Child', labels)
        self.assertFalse(any('.mp3' in label or '.wav' in label for label in labels))

    def test_unchanged_list_is_not_rebuilt_on_every_refresh(self):
        with patch.object(self.ui, 'render_usb_library') as render:
            self.refresh()
        render.assert_not_called()

    def test_stale_scan_cannot_replace_a_new_drive_list(self):
        old = self.ui.usb_snapshot
        generation = self.ui.usb_scan_generation
        new = self.folder / 'Other'
        new.mkdir()
        (new / 'Different.mp3').write_bytes(b'audio')
        self.ui.drive.set(str(new))
        self.refresh()
        self.ui.apply_usb_library({'usb_library_generation': generation, 'usb_library_path': str(self.folder), 'usb_library_snapshot': old})
        self.assertEqual(self.ui.usb_snapshot.root, new)
        self.assertEqual([e.name for e in self.ui.usb_snapshot.entries], ['Different'])

    def test_declining_confirmation_never_deletes(self):
        snapshot = self.ui.usb_snapshot
        song = usb_library.find_entry(snapshot.entries, ('Song.mp3',))
        with patch.object(app.messagebox, 'askyesno', return_value=False), patch.object(app.usb_library, 'delete_song') as delete:
            self.ui.delete_usb_song(snapshot, song)
        delete.assert_not_called()
        self.assertFalse(self.ui.busy)
        self.assertTrue((self.folder / 'Song.mp3').exists())

    def test_confirmed_delete_updates_count_and_keeps_other_songs(self):
        snapshot = self.ui.usb_snapshot
        song = usb_library.find_entry(snapshot.entries, ('Song.mp3',))
        with patch.object(app.messagebox, 'askyesno', return_value=True) as question:
            self.ui.delete_usb_song(snapshot, song)
        self.assertEqual(question.call_args.kwargs['default'], 'no')
        self.finish_delete()
        self.assertFalse((self.folder / 'Song.mp3').exists())
        self.assertTrue((self.folder / 'Classics' / 'Child.wav').exists())
        self.assertEqual(self.ui.usb_heading.cget('text'), 'USB (1)')
        self.assertFalse(self.ui.busy)

    def test_changed_song_is_preserved_after_confirmation(self):
        snapshot = self.ui.usb_snapshot
        song = usb_library.find_entry(snapshot.entries, ('Song.mp3',))
        (self.folder / 'Song.mp3').write_bytes(b'a different song')
        with patch.object(app.messagebox, 'askyesno', return_value=True):
            self.ui.delete_usb_song(snapshot, song)
        self.finish_delete()
        self.assertEqual((self.folder / 'Song.mp3').read_bytes(), b'a different song')
        self.assertIn('Could not delete', self.ui.progress_label.cget('text'))

    def test_drive_changed_during_confirmation_cannot_delete_old_song(self):
        snapshot = self.ui.usb_snapshot
        song = usb_library.find_entry(snapshot.entries, ('Song.mp3',))
        other = self.folder / 'Other'
        other.mkdir()
        def confirm(*args, **kwargs):
            self.ui.drive.set(str(other))
            self.ui.sync_usb_library(force=True)
            return True
        with patch.object(app.messagebox, 'askyesno', side_effect=confirm):
            self.ui.delete_usb_song(snapshot, song)
        self.assertFalse(self.ui.busy)
        self.assertTrue((self.folder / 'Song.mp3').exists())

    def test_deletion_disabled_while_busy_but_list_still_refreshes(self):
        self.ui.busy = True
        self.ui.set_controls(True)
        self.assertTrue(all(b.cget('state') == 'disabled' for b in self.ui.usb_delete_buttons))
        with patch.object(app.messagebox, 'askyesno') as ask:
            self.ui.delete_usb_song(self.ui.usb_snapshot, self.ui.usb_snapshot.entries[0])
        ask.assert_not_called()
        (self.folder / 'External.mp3').write_bytes(b'audio')
        with patch.object(app, 'removable_drives', return_value=['Z:\\']):
            self.ui.monitor_drives()
        self.ui.usb_scan_future.result(timeout=5)
        self.ui.poll()
        self.assertEqual(self.ui.drive.get(), str(self.folder))
        self.assertEqual(self.ui.usb_heading.cget('text'), 'USB (3)')

    def test_copied_song_triggers_immediate_refresh(self):
        (self.folder / 'Copied.mp3').write_bytes(b'audio')
        self.ui.apply_song_event({'song_url': 'a song', 'song_status': 'Done', 'percent': 100})
        self.ui.usb_scan_future.result(timeout=5)
        self.ui.poll()
        self.assertEqual(self.ui.usb_snapshot.count, 3)

    def test_scanning_finishes_before_native_removal(self):
        started = threading.Event()
        stopped = threading.Event()
        removed = threading.Event()
        def scan(root, cancel):
            started.set()
            if not cancel.wait(5):
                raise RuntimeError('Scan was not stopped')
            stopped.set()
            raise usb_library.Cancelled()
        def eject(device):
            self.assertTrue(stopped.is_set())
            removed.set()
        with patch.object(app.usb_library, 'scan', side_effect=scan), patch.object(app.windows_usb, 'describe_usb', return_value=object()), patch.object(app.windows_usb, 'eject_usb', side_effect=eject):
            self.ui.sync_usb_library(force=True)
            self.assertTrue(started.wait(5))
            self.ui.prepare_exit(close_app=False)
            self.assertTrue(removed.wait(5))
        self.assertTrue(stopped.is_set())

    def test_three_columns_and_controls_fit_small_window(self):
        self.root.geometry('1000x560')
        songs=[dict(title='A song with a longer title', channel='An artist', duration=180, url='https://www.youtube.com/watch?v=abcdefghijk')]
        self.ui.finish_search({'search_results': songs})
        self.ui.select_song(0)
        self.ui.toggle_usb_folder(('Classics',))
        self.root.deiconify()
        self.root.update()
        self.assertLessEqual(self.ui.search_button.winfo_rootx()+self.ui.search_button.winfo_width(), self.ui.usb_panel.winfo_rootx())
        self.assertLess(self.ui.usb_panel.winfo_rooty(), self.ui.result_frame.winfo_rooty())
        for widget in [self.ui.usb_panel, self.ui.drive, self.ui.button, self.ui.cancel_button, *self.ui.usb_delete_buttons, *self.ui.preview_widgets.values()]:
            self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(), self.root.winfo_rootx()+self.root.winfo_width())
            self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(), self.root.winfo_rooty()+self.root.winfo_height())
