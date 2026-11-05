# YouTube to USB MP3

Windows desktop app for converting permitted YouTube audio to 192 kbps MP3 and copying it directly into the flash drive's root folder. Supports individual links and playlists. Local downloads are retained; existing identical files are skipped, different files with the same name produce an error. Transfers are checked with SHA-256. The app never formats drives or deletes source audio.

## Portable Windows app (no installation)

Download `YouTubeToUSB-Windows-x64.zip` from a successful GitHub Actions **Build portable Windows app** run. Extract the entire ZIP, open its `YouTubeToUSB` folder, and double-click `YouTubeToUSB.exe`. Python, FFmpeg, Node.js, and yt-dlp are bundled: no administrator access, installer, or separate runtime installation is required. Keep the `_internal` folder beside the EXE. Paste links and click **Start**. When exactly one removable drive is detected, it is selected automatically. With multiple drives, choose the destination first. MP3s go directly into the drive root; no Music subfolder is created.

This is a portable folder, not a single-file executable. It requires 64-bit Windows 10/11 and Internet access for YouTube. It stores downloaded audio in your local app-data folder. Windows may show a warning for the unsigned executable. Do not bypass organizational security policies. To update the downloader, download a newly built portable package.

The build workflow is provided but has not been run in this Linux environment; no downloadable EXE has been produced here yet.

## Build the portable app

The app shows live download percentages for the current track and byte-based USB-copy percentages. Each track and stage starts a new percentage; this is not a percentage for the entire playlist. Conversion, verification, and downloads with unknown sizes show an animated progress bar. Copying reaches 100% only after verification succeeds.

Push these files to GitHub and run the **Build portable Windows app** workflow, then download its ZIP artifact. Alternatively, on a Windows build machine with Python 3.12, FFmpeg/ffprobe, and Node.js on PATH, run `./build-windows.ps1` in PowerShell. Setup tools are needed only on the build machine. The script checks the upstream yt-dlp executable against its published SHA-256 checksum before bundling it. The build selects the latest yt-dlp release so rebuilding can incorporate extraction fixes; it is not a reproducible pinned build.

Before distributing publicly, include the licenses and comply with the redistribution/source obligations of the exact Python, Tcl/Tk, FFmpeg, Node.js, yt-dlp, and PyInstaller builds bundled. Code signing and a Windows end-to-end test are recommended before distribution.

## Run from source (developer option)

Requires Windows 10/11 with App Installer (`winget`). Run `Setup.cmd` once to install Python, FFmpeg, and Node.js using verified winget packages. Follow its instructions to install the Python dependency in a fresh terminal. Then double-click `Start.cmd`, paste links and click **Start**. A single detected removable drive is selected automatically; otherwise select the destination. MP3s go directly into the selected folder. Some USB disks appear as fixed disks: use Browse to select those explicitly. Safely eject the drive after completion.

Downloads stay in `%LOCALAPPDATA%\YouTubeToUSB\Downloads`. If a drive disconnects, reconnect it and rerun the links to retry. Errors appear in the app. Restricted, removed, or login-required videos can fail; this tool does not bypass access restrictions. Use only content you own or have permission to download.

## Development

Python 3.12+, Tkinter, FFmpeg/ffprobe, and the dependency in `requirements.txt` are required. Run `python -m unittest discover -s tests -v`. On Windows run `py -3 app.py`. Linux can validate conversion and file-copy behavior but cannot validate Windows drive discovery or physical USB transfers. YouTube extraction requires network access and may require a supported JavaScript runtime for some videos; see yt-dlp's current runtime documentation if extraction reports that requirement.

## Easier controls and cancellation

The interface uses large text, high-contrast Start and Cancel buttons, and three numbered steps. Paste links with the Paste links button; technical messages stay under Show details. The page scrolls on smaller screens. Tab moves between controls, Alt+S starts, and Escape cancels.

Cancel stops the downloader and its conversion process, or interrupts a USB copy/check between chunks. Keep the USB connected until the app says Stopped. Songs already copied remain; an unfinished copy created by this operation is removed. Local download files are retained. Closing while busy offers to stop and waits for cleanup before exiting. Start becomes available again after cancellation.
