# HustleAI

A local AI clipper: paste a long video link (or upload a file) and get vertical 9:16 shorts with animated captions and an AI hook title. Everything runs on your own computer, so there's no account, login, credits or upload limit.

## How it works

```
link / file ──► yt-dlp ──► ffmpeg (audio) ──► faster-whisper (GPU) ──► YAMNet ──────────► local LLM via Ollama ──► YuNet faces ──► ffmpeg + NVENC
                download    16 kHz wav         words + timestamps        laughs, loudness    picks & ranks clips       speaker crop     jump cuts + 9:16 + captions
```

| Part | Tech |
|---|---|
| UI | React 19 + Vite + Tailwind 4 (`frontend/`) |
| API + job queue | Python FastAPI, one background worker (`backend/app/`) |
| Download | yt-dlp (YouTube, Twitch, Vimeo, Kick, …) |
| Transcription | faster-whisper `large-v3-turbo`, CUDA float16 (falls back to CPU) |
| Clip picking | Ollama + `qwen2.5:7b` (falls back to an offline heuristic) |
| Post copy | Ollama writes a title, caption, description and hashtags per clip, formatted for YouTube Shorts, TikTok and Instagram Reels with one-click copy (falls back to copy built from the transcript) |
| Ranking | Every clip gets five sub-scores: **hook**, **payoff** and **standalone** (from the AI, or text checks offline) and measured **energy** (loudness and expression vs. the rest of the video, laughter/applause/shouting, on-screen motion and camera cuts) and **pacing** (words per second after jump cuts, dead air). Sound events come from Google's [YAMNet](https://huggingface.co/audiomagic/yamnet-onnx) (Apache 2.0, 16 MB ONNX, CPU, ~1 s per 4 min of audio). The AI also sees `(laughter)`/`(applause)` cues on transcript lines. Clips show a rank, the breakdown and tags like *Best hook* or *Laughs*, and can be sorted by any factor |
| Top list video | Pick the top 3/5/10 clips (or any order) and render one "Top N" video: the clips play back to back under a title, and a numbered list on the left fills in as each clip plays, with the ranks revealed in a shuffled order. You can set each clip's rank and label, pick the reveal order by hand or shuffle it, and use a slider to set each clip's length (10 s up to the full clip; quick buttons set every clip to full/30/15/10 s). It warns you past the 3-minute Shorts limit |
| Captions | Generated ASS subtitles (Karaoke, Bold Pop, Clean Box), burned in by libass |
| Jump cuts | Pauses and filler sounds (um, uh…) are cut using the word timestamps; captions are re-timed to match. *Natural* cuts pauses over ~0.6 s, *Fast* over ~0.3 s |
| Framing | OpenCV YuNet face detection (CPU, model downloads on first use to `data/models/`, with a Hugging Face mirror if GitHub is down). Faces become tracks, mouth movement picks the speaker, and the crop holds still in steady shots that switch on the source's own camera cuts. Groups that fit in the frame are shown together |
| Animation preset | Choose **Content: Animation** for cartoons and anime (English audio). Adds an anime-face detector ([lbpcascade_animeface](https://github.com/nagadomi/lbpcascade_animeface), MIT), keeps characters who hold still, follows the on-screen action where no face is found, turns jump cuts off, and tells the AI to skip theme songs, recaps and previews |
| Learning | Log each posted clip's views/likes/comments/shares/% viewed. Once 4 clips have results, curation shows the LLM your best and worst clips as examples. Once 8 clips have results, the ranking weights shift toward the sub-scores that predicted your views (correlations within chance level are ignored). The **Insights** page ranks clips, shows which factors drive your results, and how well the ranking score predicted them |
| Render | ffmpeg, `h264_nvenc` (falls back to libx264), 1080×1920 |

Projects are stored in `data/projects/<id>/`: the source video, `transcript.json`, `signals.json` (loudness and sound events), `curation.json` (picks and scores), `posts.json`, `stats.json` (logged results), `clips/<clip>/clip.mp4` and `countdown/countdown.mp4`. Each step saves its result, so **Retry** and server restarts pick up where they stopped.

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
