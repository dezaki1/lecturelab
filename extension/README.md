# LectureLab Video Grabber (browser extension)

Like Video DownloadHelper, with two modes (the switch at the top of the popup):

- **Add to LectureLab**: LectureLab downloads the video with yt-dlp, extracts the audio, finds
  slides and runs the usual pipeline (transcription, study material, search).
- **Just download**: LectureLab saves the file to your Downloads folder (video as .mp4, or audio
  only as .m4a). No lecture is created. A notification tells you when it's done; the popup's
  **Downloads folder** link opens the folder. Streams are saved under the page's title.
  Another folder: set `save_dir` under `[downloads]` in `C:\Users\<you>\LectureLab\config.toml`.

- **This page**: sends the page link. LectureLab works out where the video is (YouTube, Panopto,
  Vimeo, Zoom cloud recordings, plain `<video>` pages and 1000+ other sites).
- **Found while playing**: videos, audio and HLS/DASH streams (`.m3u8`/`.mpd`) the page loaded
  while it played. Use these when "This page" says *No video found*, e.g. a Moodle page whose
  player only starts after you click Play.
- **Send my login**: passes your cookies for the page and the video along, so lectures behind a
  Moodle or Panopto login download too. They go only to LectureLab on this PC, are stored in
  `LectureLab\tmp` while downloading and are deleted afterwards.
- Right-click a page, link or video → **Send to LectureLab (study it)** sends it straight to the
  subject you used last; **Download video with LectureLab** just saves it.

It talks to `http://127.0.0.1:8765` (change it under the extension's Settings if you changed the
port). LectureLab has to be running. DRM-protected videos (Netflix and similar) can't be downloaded.

## Install (Chrome or Edge, Windows)

1. Update LectureLab first: close it, then in the repo folder run `uv sync`, then
   `cd frontend; npm run build`, then start it again (`.\start.ps1`).
2. Open `chrome://extensions` (Edge: `edge://extensions`) and switch on **Developer mode**.
3. Click **Load unpacked** and pick this `extension` folder.
4. Pin the extension (puzzle icon → pin). Its icon shows a number when it found videos on a tab.

After `git pull`, click the reload arrow on the extension card.

## YouTube

YouTube needs a JavaScript runtime for yt-dlp. Install Deno once: `winget install DenoLand.Deno`,
then restart LectureLab. University platforms (Moodle, Panopto, Zoom) don't need it.

## When a site stops working

Sites change and yt-dlp follows quickly. Update it: close LectureLab, then
`uv lock --upgrade-package yt-dlp; uv sync`. The download log of a failed lecture is in its
folder as `yt-dlp.log`.
