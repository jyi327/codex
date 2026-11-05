import queue
import threading
import unittest
from unittest.mock import MagicMock, patch
import subprocess
import app
import windows_usb


class SafeRemovalTests(unittest.TestCase):
    def setUp(self):
        self.device = windows_usb.USBDevice('E:\\', 'original-volume', 'USBSTOR\\original')

    def test_only_original_device_can_be_removed(self):
        with patch.object(windows_usb, 'describe_usb', return_value=self.device), patch.object(windows_usb, 'request_device_eject') as request:
            windows_usb.eject_usb(self.device)
            request.assert_called_once_with(self.device.device_id)
        replacement = windows_usb.USBDevice('E:\\', 'new-volume', self.device.device_id)
        with patch.object(windows_usb, 'describe_usb', return_value=replacement), patch.object(windows_usb, 'request_device_eject') as request:
            with self.assertRaisesRegex(OSError, 'drive has changed'):
                windows_usb.eject_usb(self.device)
            request.assert_not_called()

    def test_non_usb_disk_is_rejected(self):
        result = subprocess.CompletedProcess([], 0, '{"bus":"SATA","device_id":"system-disk"}', '')
        with patch.object(windows_usb, 'volume_identity', return_value=('C:\\', 'volume')), patch.object(windows_usb.subprocess, 'run', return_value=result):
            with self.assertRaises(windows_usb.NotUSBDrive):
                windows_usb.describe_usb('C:\\Music')

    def test_windows_veto_is_an_error_and_never_forces_removal(self):
        manager = MagicMock()
        manager.CM_Locate_DevNodeW.return_value = 0
        manager.CM_Request_Device_EjectW.return_value = 23
        with patch.object(windows_usb.ctypes, 'WinDLL', return_value=manager, create=True):
            with self.assertRaisesRegex(OSError, 'could not safely eject'):
                windows_usb.request_device_eject(self.device.device_id)
        self.assertEqual(manager.CM_Request_Device_EjectW.call_count, 1)
        self.assertEqual(manager.CM_Request_Device_EjectW.call_args.args[-1], 0)

    def test_no_removal_when_device_cannot_be_located(self):
        manager = MagicMock()
        manager.CM_Locate_DevNodeW.return_value = 13
        with patch.object(windows_usb.ctypes, 'WinDLL', return_value=manager, create=True):
            with self.assertRaises(OSError):
                windows_usb.request_device_eject(self.device.device_id)
        manager.CM_Request_Device_EjectW.assert_not_called()

    def test_close_waits_for_copy_cleanup(self):
        instance = app.App.__new__(app.App)
        instance.ejecting = False
        instance.busy = True
        instance.close_when_done = False
        instance.cancel = MagicMock()
        instance.prepare_exit = MagicMock()
        with patch.object(app.messagebox, 'askyesno', return_value=True):
            instance.close()
        self.assertTrue(instance.close_when_done)
        instance.cancel.assert_called_once()
        instance.prepare_exit.assert_not_called()

    def test_failure_never_claims_ready_to_unplug(self):
        instance = app.App.__new__(app.App)
        instance.events = queue.Queue()
        instance.last_destination = 'E:\\'
        instance.usb_identity_error = None
        instance.usb_device = self.device
        with patch.object(windows_usb, 'eject_usb', side_effect=OSError('Drive is in use')):
            instance.prepare_usb('E:\\')
        self.assertEqual(instance.events.get_nowait(), {'usb_error': 'Drive is in use'})

    def test_success_is_shown_only_after_windows_approves(self):
        instance = app.App.__new__(app.App)
        instance.events = queue.Queue()
        instance.last_destination = 'E:\\'
        instance.usb_identity_error = None
        instance.usb_device = self.device
        with patch.object(windows_usb, 'eject_usb') as remove:
            instance.prepare_usb('E:\\')
        remove.assert_called_once_with(self.device)
        self.assertEqual(instance.events.get_nowait(), {'usb_ready': True})

    def test_cleanup_finishes_before_safe_removal_starts(self):
        instance = app.App.__new__(app.App)
        instance.events = queue.Queue()
        instance.events.put(None)
        instance.busy = True
        instance.transfer_cleanup_failed = False
        instance.download_complete = False
        instance.show_done = MagicMock()
        instance.close_when_done = True
        instance.cancel_event = threading.Event()
        instance.cancel_event.set()
        instance.progress_bar = MagicMock()
        instance.set_controls = MagicMock()
        instance.show_progress = MagicMock()
        instance.root = MagicMock()
        seen_busy = []
        instance.prepare_exit = lambda: seen_busy.append(instance.busy)
        instance.poll()
        self.assertEqual(seen_busy, [False])
        instance.root.destroy.assert_not_called()

    def test_cancel_requests_removal_after_cleanup_without_closing(self):
        instance = app.App.__new__(app.App)
        instance.events = queue.Queue()
        instance.events.put(None)
        instance.busy = True
        instance.transfer_cleanup_failed = False
        instance.download_complete = False
        instance.close_when_done = False
        instance.remove_when_stopped = True
        instance.cancel_event = threading.Event()
        instance.cancel_event.set()
        for name in ('progress_bar', 'set_controls', 'show_progress', 'show_done', 'root', 'prepare_exit'):
            setattr(instance, name, MagicMock())
        instance.poll()
        instance.prepare_exit.assert_called_once_with(close_app=False)
        self.assertFalse(instance.busy)

    def test_failed_or_missing_usb_closes_silently(self):
        instance = app.App.__new__(app.App)
        instance.events = queue.Queue()
        instance.events.put({'usb_error': 'Device in use'})
        instance.progress_bar = MagicMock()
        instance.root = MagicMock()
        with patch.object(app.messagebox, 'askyesno') as ask, patch.object(app.messagebox, 'showinfo') as info:
            instance.poll()
        ask.assert_not_called()
        info.assert_not_called()
        instance.root.destroy.assert_called_once()

    def test_successful_done_closes_without_a_message(self):
        instance = app.App.__new__(app.App)
        instance.events = queue.Queue()
        instance.events.put({'usb_ready': True})
        instance.progress_bar = MagicMock()
        instance.root = MagicMock()
        instance.exit_after_removal = True
        with patch.object(app.messagebox, 'showinfo') as info:
            instance.poll()
        info.assert_not_called()
        instance.root.destroy.assert_called_once()

    def test_done_hides_window_before_background_removal(self):
        instance = app.App.__new__(app.App)
        instance.busy = False
        instance.ejecting = False
        instance.last_destination = 'E:\\'
        instance.root = MagicMock()
        for name in ('set_controls', 'cancel_button', 'done_button', 'show_progress', 'progress_bar'):
            setattr(instance, name, MagicMock())
        with patch.object(app.threading, 'Thread') as worker:
            def start():
                instance.root.withdraw.assert_called_once()
                instance.root.destroy.assert_not_called()
            worker.return_value.start.side_effect = start
            instance.prepare_exit()
        self.assertTrue(instance.exit_after_removal)
        self.assertTrue(instance.ejecting)
        worker.assert_called_once_with(target=instance.prepare_usb, args=('E:\\',), daemon=False)


if __name__ == '__main__':
    unittest.main()
