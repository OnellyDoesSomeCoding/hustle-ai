# HustleAI

A local AI clipper: paste a long video link (or upload a file) and get vertical 9:16 shorts with animated captions and an AI hook title. Everything runs on your own computer, so there's no account, login, credits or upload limit.

## How it works

```
link / file ──► yt-dlp ──► ffmpeg (audio) ──► faster-whisper (GPU) ──► local LLM via Ollama ──► YuNet faces ──► ffmpeg + NVENC
                download    16 kHz wav         words + timestamps        picks & scores clips      speaker crop     jump cuts + 9:16 + captions
```

| Part | Tech |
|---|---|
| UI | React 19 + Vite + Tailwind 4 (`frontend/`) |
| API + job queue | Python FastAPI, one background worker (`backend/app/`) |
| Download | yt-dlp (YouTube, Twitch, Vimeo, Kick, …) |
| Transcription | faster-whisper `large-v3-turbo`, CUDA float16 (falls back to CPU) |
| Clip picking | Ollama + `qwen2.5:7b` (falls back to an offline heuristic) |
| Post copy | Ollama writes a title, caption, description and hashtags per clip, formatted for YouTube Shorts, TikTok and Instagram Reels with one-click copy (falls back to copy built from the transcript) |
| Captions | Generated ASS subtitles (Karaoke, Bold Pop, Clean Box), burned in by libass |
| Jump cuts | Pauses and filler sounds (um, uh…) are cut using the word timestamps; captions are re-timed to match. *Natural* cuts pauses over ~0.6 s, *Fast* over ~0.3 s |
| Framing | OpenCV YuNet face detection (CPU, model downloads on first use to `data/models/`). Faces become tracks, mouth movement picks the speaker, and the crop holds still in steady shots that switch on the source's own camera cuts. Groups that fit in the frame are shown together |
| Learning | Log each posted clip's views/likes/comments/shares/% viewed. Once 4 clips have results, curation shows the LLM your best and worst clips as examples. The **Insights** page ranks clips and shows how well the AI score predicted real results |
| Render | ffmpeg, `h264_nvenc` (falls back to libx264), 1080×1920 |

Projects are stored in `data/projects/<id>/`: the source video, `transcript.json`, `curation.json`, `posts.json`, `stats.json` (logged results) and `clips/<clip>/clip.mp4`. Each step saves its result, so **Retry** and server restarts pick up where they stopped.

## Requirements

- Windows 10/11 (also works on macOS/Linux, CPU only without NVIDIA)
- Node 18+
- Python **3.12** (newer versions may not have CUDA/ML wheels yet)
- ffmpeg: `winget install Gyan.FFmpeg`
- Ollama (optional but recommended): `winget install Ollama.Ollama`, then `ollama pull qwen2.5:7b`
- NVIDIA GPU recommended (tested target: RTX 4060 8 GB)

## Setup and run

```bash
npm run setup     # once: Python venv, pip packages (~1.5 GB of CUDA libs), npm packages
npm run dev       # backend on :8000 + UI on http://127.0.0.1:5173
```

Or as a single server: `npm start` builds the UI and serves everything at http://127.0.0.1:8000.

The first transcription downloads the Whisper model (~1.6 GB) into the Hugging Face cache.

## Settings (environment variables)

| Variable | Default |
|---|---|
| `HUSTLE_WHISPER_MODEL` | `large-v3-turbo` (try `medium` or `small` on weak GPUs) |
| `HUSTLE_OLLAMA_MODEL` | `qwen2.5:7b` |
| `HUSTLE_OLLAMA_URL` | `http://127.0.0.1:11434` |
| `HUSTLE_DATA_DIR` | `./data` |
| `HUSTLE_FFMPEG` / `HUSTLE_FFPROBE` | auto-detected |

## Roadmap

- **Phase 1 (done):** link/upload → transcribe → AI picks → centre crop → captions → MP4/SRT export
- **Phase 2:** ~~face tracking~~ (done: speaker-following crop), ~~silence/filler cuts~~ (done), ~~learning from results~~ (done), fit/blur/square layouts, more caption templates, CTA, pull stats from the YouTube API
- **Phase 3:** editor (trim, transcript editing, per-clip re-render)
- **Phase 4:** music + ducking, gameplay split-screen, translation, hashtags
- **Phase 5:** channel watcher (YouTube RSS), batch queue

Only clip videos you have the rights to repurpose.
