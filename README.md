# YouTube to USB MP3

Windows desktop app for finding a YouTube song, converting its audio to 192 kbps MP3, and copying it directly into a flash drive’s root folder. Type a song name or artist, click SEARCH, tick one or more results, review the Download list on the right, and click START. The paste-link option is removed. Local downloads are retained, USB filenames have no spaces or trailing video IDs, and copies are verified with SHA-256. Existing files are never overwritten.

## Video guide

[观看 97 秒操作示范（无配音，中文字幕）](docs/YouTubeToUSB-Malaysia-Chinese-Demo.mp4) · [下载字幕](docs/YouTubeToUSB-Malaysia-Chinese-Demo.srt)

从电脑桌面打开程序，搜索想下载的歌曲，示范 PLAY 试听及 STOP 停止试听，再点击加入下载列表，示范移除选错的歌曲及 CANCEL 清空选择；再搜索「选择」并加入同一个列表，下载完成后点击 DOWNLOAD MORE 继续添加并下载新歌曲，最后点击 DONE。仅显示中文字幕，无配音，不讲解安全移除 USB。画面采用当前程序界面，搜索结果和传输过程为模拟示范，等待时间已缩短。只连接一个 USB，无须选择磁盘。

旧版粘贴网址视频保留在 `docs/YouTubeToUSB-Mandarin-Guide.mp4`，不适用于当前搜索界面。

## Portable Windows app (no installation)

Download `YouTubeToUSB-Windows-x64.zip` from a successful GitHub Actions **Build portable Windows app** run. Extract the entire ZIP, open its `YouTubeToUSB` folder, and double-click `YouTubeToUSB.exe`. Python, FFmpeg, Node.js, and yt-dlp are bundled: no administrator access, installer, or separate runtime installation is required. Keep the `_internal` folder beside the EXE. Enter a song name or artist, click **SEARCH**, select one or more results, then click **START**. Up to five results show the title, channel, and duration. Click **▶ PLAY** to hear a 30-second audio sample inside the app, or **■ STOP** to stop it. Preview does not select a song or save it to USB. A single connected USB drive is selected automatically; with multiple drives, choose the destination first. START requires queued songs and a connected destination. Songs are downloaded, converted, and copied one at a time. MP3s go directly into the drive root; no Music subfolder is created.

This is a portable folder, not a single-file executable. It requires 64-bit Windows 10/11 and Internet access for YouTube. It stores downloaded audio in your local app-data folder. Windows may show a warning for the unsigned executable. Do not bypass organizational security policies. To update the downloader, download a newly built portable package.

The Windows workflow builds the portable ZIP and checks that the packaged application can start and that its bundled downloader/FFmpeg can convert a local test tone to a valid MP3 and complete a verified copy before uploading it.

## Build the portable app

The app shows live download percentages for the current track and byte-based USB-copy percentages. Each track and stage starts a new percentage; this is not a percentage for the entire playlist. Conversion shows a percentage when the audio duration is known, using FFmpeg’s actual converted-audio time. Verification and stages with unknown sizes show an animated progress bar. Copying reaches 100% only after verification succeeds.

Push these files to GitHub and run the **Build portable Windows app** workflow, then download its ZIP artifact. Alternatively, on a Windows build machine with Python 3.12, FFmpeg/ffprobe/ffplay, and Node.js on PATH, run `./build-windows.ps1` in PowerShell. Setup tools are needed only on the build machine. The script verifies the official unpackaged yt-dlp Windows ZIP against its published SHA-256 checksum before bundling it. It also compares three version-command startup samples against the verified self-extracting release; the median timings are recorded in StartupTiming.txt and the build summary. The build selects the latest yt-dlp release so rebuilding can incorporate extraction fixes; it is not a reproducible pinned build.

Before distributing publicly, include the licenses and comply with the redistribution/source obligations of the exact Python, Tcl/Tk, FFmpeg, Node.js, yt-dlp, and PyInstaller builds bundled. Code signing and a Windows end-to-end test are recommended before distribution.

## Run from source (developer option)

Requires Windows 10/11 with App Installer (`winget`). Run `Setup.cmd` once to install Python, FFmpeg, and Node.js using verified winget packages. Follow its instructions to install the Python dependency in a fresh terminal. Then double-click `Start.cmd`, search for a song, select it, and click **START**. A single detected removable drive is selected automatically; otherwise select the destination. MP3s go directly into the selected folder. Some USB disks appear as fixed disks: open the USB dropdown and choose a folder on those drives. Safely eject the drive after completion.

Downloads stay in `%LOCALAPPDATA%\YouTubeToUSB\Downloads`. If a drive disconnects, reconnect it and select the song again to retry. Errors appear in the app. Restricted, removed, or login-required videos can fail; this tool does not bypass access restrictions. Use only content you own or have permission to download.

## Development

Python 3.12+, Tkinter, FFmpeg/ffprobe/ffplay, and the dependency in `requirements.txt` are required. Install dependencies with `python -m pip install -r requirements.txt`, then run `python -m unittest discover -s tests -t . -v`. On Windows run `py -3 app.py`. Linux can validate conversion and file-copy behavior but cannot validate Windows drive discovery or physical USB transfers. YouTube extraction requires network access and may require a supported JavaScript runtime for some videos; see yt-dlp's current runtime documentation if extraction reports that requirement.

## Easier controls and cancellation

The interface uses large text, high-contrast Start and Cancel buttons, and a compact links-and-drive layout. Search and selection replace the link box; technical messages stay under Details. Only the song lists scroll; the main controls stay visible. Alt+S starts and Escape cancels. USB drives are checked automatically every 1.5 seconds; START is disabled when no drive is connected.

Cancel stops the downloader and its conversion process, or interrupts a USB copy/check between chunks. Keep the USB connected while stopping or preparing it for removal. Songs already copied remain; an unfinished copy created by this operation is removed. Local download files are retained. Cancel and the window close button stop an active transfer, wait for cleanup, then ask Windows to prepare the dedicated USB for removal. DOWNLOAD MORE and DONE appear together only after all songs are copied and verified. DOWNLOAD MORE clears the search field for another search while keeping the same USB connected and retaining the download list. DONE still hides the window immediately and completes safe removal in the background. After successful removal following Cancel, reconnect the USB to use it again.

USB transfers first write a uniquely named `.youtubetousb-*.partial` file directly on the selected drive. The app flushes and closes it, checks its SHA-256 against the source, and only then publishes the final MP3 name without replacing existing files. Cancellation closes and flushes the current write before removing only this operation's temporary file. The USB-copy worker is never forcibly killed. On Windows the final move requests write-through; this does not replace Windows' safe-removal mechanism.

If cleanup cannot finish (for example, the drive disconnects), the app shows a cleanup error and keeps the window open rather than reporting a normal stop. An abrupt crash may leave a uniquely named `.partial` file, rather than an apparently finished MP3. Do not delete unrelated files when cleaning up an old partial transfer.

Press **DONE**, or close the window: the app prepares the USB for safe removal and closes silently, without a confirmation message. The app requests normal Windows Plug and Play removal of the original USB device; it never forces a dismount or removes another device that reused its drive letter. Windows may refuse if another app (or this app running from the USB itself) is using the drive. If the drive is absent or Windows refuses removal (for example, it is in use), the app closes silently without reporting successful removal. Use **Windows Eject / Safely Remove Hardware** before unplugging in that case. Automatic removal requires a USB disk with a drive letter; local folders, network paths, and other media are not automatically removed. File flushing and verification cannot guarantee against power loss, faulty hardware, filesystem damage, or a device that ignores flush requests. Automated tests use temporary directories; physical USB cancellation and safe-removal behavior still require validation on Windows.

The rounded blue interface uses START, CANCEL, and DONE. The USB dropdown contains drive selection, Refresh, and Choose folder; secondary controls stay out of the main layout. Technical removal errors appear only under Show details. Removal tests mock Windows device APIs; no test ejects a real device. Validate real safe removal on a Windows machine before relying on it.

Preparation of the selected song uses a stable status. A completed stage does not revert because of metadata messages from another postprocessor. Real retry messages remain visible in Details and on the main status. Network operations use a 20-second socket timeout with limited download/extractor retries.

USB MP3 filenames use the YouTube title with spaces and the downloader’s trailing bracketed video ID removed, for example `SongTitle.mp3`. The internal local download cache retains IDs to keep different videos distinct. Words that are part of the original YouTube title, such as Live or Official Video, are preserved. Existing USB files are never renamed or deleted; if a different song already has the same cleaned filename, the app reports a conflict instead of overwriting it.

## Preparation and download optimizations

The portable app bundles the official unpackaged downloader, avoiding repeated one-file extraction when starting a link. USB identification runs concurrently with downloader startup, and successful device identities are reused while the native volume identity remains the same. Safe removal always refreshes the physical-device identity and invalidates the cache afterward. YouTube Music/short song links are normalized to a direct watch URL without mix/tracking parameters. Explicit playlists use lazy extraction so downloading can begin before the entire list is enumerated; totals may initially be unknown. Audio-only format selection is explicit, and supported segmented streams can fetch four fragments concurrently. MP3 quality, flushes, hashes, and overwrite protection are unchanged. Network speed still depends on YouTube and the local connection.

## In-app search

Search uses the bundled downloader’s public YouTube search, without a YouTube API key or an embedded browser. Searching reads metadata only. Preview streams a short sample on demand; full audio is downloaded and copied only when START is pressed. Live/upcoming streams and malformed results are excluded. Enter song names rather than links. Search has a time limit and can be cancelled; cancelling a read-only search does not remove the USB. Closing still requests safe removal. YouTube or local network restrictions can prevent search; errors appear under Details and the user can retry.

The Windows build also runs a live search check and includes its result in SearchCheck.txt. This network-dependent check is reported separately from the offline conversion and startup checks.

## Multi-song layout

The main window uses two permanently visible columns: YouTube-style search results with thumbnails and duration badges on the left, and the ordered Download list on the right. There are no tabs or whole-page scrolling. Only the lists scroll; Details opens separately. The main controls were checked at 1000×560. Thumbnails load in the background and fall back to placeholders without blocking search or downloads.

Tick songs across different searches to build one duplicate-free list. Click a checked result again or its × in the Download list to remove it. Before a job, CANCEL clears all selections and leaves results and the USB connected. During a job, CANCEL still stops safely and requests USB removal.

Each song shows Waiting, Preparing, Downloading, Converting, Copying, Verifying, Done, Failed, or Stopped. Successful copies remain protected. Completion shows copied/failed counts; RETRY FAILED submits only failed songs and leaves successful songs alone. Selected items are retained across searches during this app session; they are not saved to disk for restoration after reopening.

## Song preview

Each result has a separate PLAY button. It plays up to 30 seconds of audio through the bundled ffplay player without opening a browser or saving files. Click the same button to stop; choosing another preview stops the previous one first. Song selection and the download list are independent of playback. Preview works without a connected USB.

Searching again, DOWNLOAD MORE, START, CANCEL, DONE, and closing the app stop preview playback. Preview is disabled during a search, transfer, or USB removal. The player is bundled in the portable ZIP; source users need ffplay on PATH. Loading and playback run off the UI thread. Unavailable or restricted streams show a short error.

The Windows build verifies bundled ffplay offline with a test tone and a dummy audio device. This checks the player and dependencies, not physical speakers or live YouTube media access.
