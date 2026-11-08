"""Animated caption files (ASS, burned in by ffmpeg/libass) and SRT export."""

from __future__ import annotations

from dataclasses import dataclass

SENTENCE_END = (".", "?", "!", "…", "。", "？", "！")


@dataclass(frozen=True)
class Template:
    id: str
    name: str
    font: str
    size: int
    max_words: int
    max_chars: int
    upper: bool
    margin_v: int
    outline: int
    box: bool = False
    highlight: str | None = None  # ASS colour for the active word / accent
    mode: str = "karaoke"  # karaoke | pop | line


# ASS colours are &HAABBGGRR (alpha first, then blue, green, red).
TEMPLATES: dict[str, Template] = {
    "karaoke": Template("karaoke", "Karaoke", "Arial Black", 78, 3, 18, True, 560, 6,
                        highlight="&H004DE1FF", mode="karaoke"),
    "bold": Template("bold", "Bold Pop", "Arial Black", 108, 2, 12, True, 620, 8,
                     highlight="&H0088FF4D", mode="pop"),
    "clean": Template("clean", "Clean Box", "Arial", 60, 7, 32, False, 420, 14,
                      box=True, mode="line"),
}


def _ts(t: float) -> str:
    cs = int(round(max(t, 0) * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _srt_ts(t: float) -> str:
    ms = int(round(max(t, 0) * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _clean(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")")


def clip_words(segments: list[dict], start: float, end: float) -> list[dict]:
    """Words inside [start, end], re-timed relative to the clip start."""
    dur = end - start
    out = []
    for seg in segments:
        if seg["end"] < start or seg["start"] > end:
            continue
        for w in seg["words"]:
            if w["end"] <= start or w["start"] >= end:
                continue
            out.append({
                "start": max(0.0, w["start"] - start),
                "end": min(dur, w["end"] - start),
                "word": w["word"],
            })
    return out


def group_words(words: list[dict], max_words: int, max_chars: int, max_gap: float = 0.5) -> list[list[dict]]:
    groups: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur:
            chars = sum(len(x["word"]) + 1 for x in cur) + len(w["word"])
            if (len(cur) >= max_words or chars > max_chars or w["start"] - cur[-1]["end"] > max_gap
                    or cur[-1]["word"].endswith(SENTENCE_END)):
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def _group_end(groups: list[list[dict]], i: int, clip_dur: float) -> float:
    last = groups[i][-1]["end"]
    if i + 1 < len(groups) and groups[i + 1][0]["start"] - last < 0.5:
        return groups[i + 1][0]["start"]
    return min(last + 0.2, clip_dur)


def _wrap(text: str, width: int) -> str:
    lines, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        lines.append(line)
    return r"\N".join(lines)


def build_ass(words: list[dict], clip_dur: float, template_id: str, hook: str | None) -> str:
    t = TEMPLATES.get(template_id, TEMPLATES["karaoke"])
    border_style = 3 if t.box else 1
    outline_colour = "&H99000000" if t.box else "&H00000000"
    styles = [
        f"Style: Caption,{t.font},{t.size},&H00FFFFFF,&H00FFFFFF,{outline_colour},&H80000000,"
        f"1,0,0,0,100,100,0,0,{border_style},{t.outline},0,2,60,60,{t.margin_v},1",
        "Style: Hook,Arial Black,62,&H00141414,&H00FFFFFF,&H00FFFFFF,&H00000000,"
        "1,0,0,0,100,100,0,0,3,20,0,8,80,80,300,1",
    ]
    events: list[str] = []

    def add(start: float, end: float, style: str, text: str, layer: int = 0) -> None:
        if end - start >= 0.02:
            events.append(f"Dialogue: {layer},{_ts(start)},{_ts(end)},{style},,0,0,0,,{text}")

    if hook:
        hook_text = _wrap(_clean(hook.upper()), 22)
        add(0, min(3.5, clip_dur), "Hook", r"{\fad(150,250)}" + hook_text, layer=1)

    groups = group_words(words, t.max_words, t.max_chars)
    for gi, group in enumerate(groups):
        g_start, g_end = group[0]["start"], _group_end(groups, gi, clip_dur)
        tokens = [_clean(w["word"].upper() if t.upper else w["word"]) for w in group]
        if t.mode == "karaoke":
            for wi, w in enumerate(group):
                w_start = g_start if wi == 0 else w["start"]
                w_end = group[wi + 1]["start"] if wi + 1 < len(group) else g_end
                parts = [
                    (r"{\c" + t.highlight + r"&\fscx108\fscy108}" + tok + r"{\r}") if i == wi else tok
                    for i, tok in enumerate(tokens)
                ]
                add(w_start, w_end, "Caption", " ".join(parts))
        elif t.mode == "pop":
            colour = (r"\c" + t.highlight + "&") if (t.highlight and gi % 3 == 2) else ""
            anim = r"{\fscx70\fscy70" + colour + r"\t(0,90,\fscx108\fscy108)\t(90,160,\fscx100\fscy100)}"
            add(g_start, g_end, "Caption", anim + " ".join(tokens))
        else:
            add(g_start, g_end, "Caption", _wrap(" ".join(tokens), t.max_chars // 2 + 6))

    return "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        *styles,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *events,
        "",
    ])


def build_srt(words: list[dict]) -> str:
    blocks = []
    for i, group in enumerate(group_words(words, 8, 42, max_gap=0.8), start=1):
        text = " ".join(w["word"] for w in group)
        blocks.append(f"{i}\n{_srt_ts(group[0]['start'])} --> {_srt_ts(group[-1]['end'])}\n{text}\n")
    return "\n".join(blocks)
