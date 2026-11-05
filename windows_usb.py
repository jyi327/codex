"""Request normal Windows Plug and Play removal; never force a dismount."""
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
import sys


class NotUSBDrive(OSError):
    pass


@dataclass(frozen=True)
class USBDevice:
    root: str
    volume_id: str
    device_id: str


def volume_identity(destination):
    if sys.platform != 'win32':
        raise NotUSBDrive('Automatic USB removal is available on Windows only.')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    get_path = kernel.GetVolumePathNameW
    get_path.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    get_path.restype = wintypes.BOOL
    root = ctypes.create_unicode_buffer(1024)
    if not get_path(str(Path(destination).resolve()), root, len(root)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not re.fullmatch(r'[A-Za-z]:\\', root.value):
        raise NotUSBDrive('Automatic removal needs a USB drive with its own drive letter. Use Windows Eject for this folder.')
    get_name = kernel.GetVolumeNameForVolumeMountPointW
    get_name.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    get_name.restype = wintypes.BOOL
    name = ctypes.create_unicode_buffer(1024)
    if not get_name(root.value, name, len(name)):
        raise ctypes.WinError(ctypes.get_last_error())
    return root.value.upper(), name.value


def describe_usb(destination):
    root, volume_id = volume_identity(destination)
    # Only the validated drive letter is inserted into this fixed local query.
    script = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$disks = @(Get-Partition -DriveLetter 'DRIVE_LETTER' | Get-Disk)
if ($disks.Count -ne 1) { throw 'Cannot identify one physical disk for this folder.' }
$disk = $disks[0]
$device = Get-CimInstance Win32_DiskDrive -Filter ('Index=' + $disk.Number)
@{bus = [string]$disk.BusType; device_id = [string]$device.PNPDeviceID} | ConvertTo-Json -Compress
""".replace('DRIVE_LETTER', root[0])
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, text=True, encoding='utf-8', errors='replace',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=30)
    if result.returncode:
        raise OSError('Windows could not identify this USB drive. Use Windows Eject instead.')
    data = json.loads(result.stdout.lstrip('\ufeff'))
    if data.get('bus') != 'USB':
        raise NotUSBDrive('This folder is not on a USB drive. No device was ejected.')
    if not data.get('device_id'):
        raise OSError('Windows did not provide a USB device identity. Use Windows Eject instead.')
    return USBDevice(root, volume_id, data['device_id'])


def request_device_eject(device_id):
    """CM_Request_Device_Eject lets Windows flush and veto unsafe removal."""
    manager = ctypes.WinDLL('cfgmgr32')
    locate = manager.CM_Locate_DevNodeW
    locate.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.ULONG]
    locate.restype = wintypes.ULONG
    request = manager.CM_Request_Device_EjectW
    request.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR,
                        wintypes.ULONG, wintypes.ULONG]
    request.restype = wintypes.ULONG
    node = wintypes.DWORD()
    if locate(ctypes.byref(node), ctypes.create_unicode_buffer(device_id), 0) != 0:
        raise OSError('The original USB device is no longer available. No other device was ejected.')
    veto = wintypes.DWORD()
    name = ctypes.create_unicode_buffer(512)
    result = request(node, ctypes.byref(veto), name, len(name), 0)
    if result != 0:
        raise OSError(f'Windows could not safely eject the USB drive (code {result}, reason {veto.value}). Close files or other apps using the drive, then try again. You can also use Windows Eject.')


def eject_usb(expected):
    current = describe_usb(expected.root)
    if current != expected:
        raise OSError('The drive has changed since copying. No device was ejected. Use Windows Eject for the original drive.')
    request_device_eject(expected.device_id)
