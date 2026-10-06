"""Learn strategies from YouTube videos: transcript -> Claude extracts exact rules -> StrategySpec JSON.

This is not "training" in the machine-learning sense. It turns what a video says into
testable rules, flags what the video left out, and ties every rule to a quote so a
person can check it. Nothing learned here trades until it passes a backtest.
"""
import json
import re
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from .config import ROOT, env
from .indicators import INDICATORS
from .strategy import StrategySpec, save_spec

TRANSCRIPTS = ROOT / "data" / "transcripts"
LEARNED = ROOT / "strategies" / "learned"
MODEL = "claude-opus-5-5"


class VideoExtraction(BaseModel):
    summary: str = Field(description="2-3 sentences on what the video teaches")
    has_testable_strategy: bool
    why_not_testable: list[str] = Field(default_factory=list)
    strategies: list[StrategySpec] = Field(default_factory=list)
    non_mechanical_ideas: list[str] = Field(
        default_factory=list,
        description="Advice that can't be expressed as rules (psychology, discretionary chart reading, news)",
    )
    options_notes: Optional[str] = Field(
        None, description="If the video trades options, the structure used (e.g. 30-45 DTE bull put spread, 0.30 delta)"
    )


SYSTEM = f"""You turn trading-video transcripts into precise, testable strategy rules.

Rule language (the only thing the backtester understands):
- Operand: {{"ind": <name>, "period": <int>, "std": <float>}} or {{"value": <number>}}
- Indicator names: {", ".join(INDICATORS)}.
  "highest"/"lowest" are the highest high / lowest low of the PREVIOUS N bars.
  "pct_change" is percent change over N bars. bb_* are Bollinger bands (period, std).
- Condition: {{"left": Operand, "op": one of > < >= <= crosses_above crosses_below, "right": Operand}}
- entry / exit: {{"mode": "all"|"any", "conditions": [...]}}
- Plus optional stop_loss_pct, take_profit_pct (fractions, 0.05 = 5%), max_hold_days.
- Daily bars only, long only. If the video uses intraday bars or shorting, say so in "missing".

Rules for you:
1. Only extract what the speaker actually says. Fix obvious caption errors
   (e.g. "bowling your bands" = Bollinger Bands, "our side" = RSI).
2. When you must fill a gap to make it runnable (e.g. no stop given), use a conservative
   default, list it in "assumptions", and set complete=false with the gap in "missing".
3. For every rule, add an evidence item with the transcript quote it came from.
4. If the video has no mechanical strategy (pure motivation, discretionary chart reading,
   a sales pitch), set has_testable_strategy=false and explain why. Don't invent one.
5. Suggest symbols the video mentions; otherwise use ["SPY"].
6. If the video trades options, express the underlying stock signal as the rules and
   describe the options structure in options_notes."""


def video_id(url_or_id: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([A-Za-z0-9_-]{11})", url_or_id)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url_or_id):
        return url_or_id
    raise ValueError(f"Not a YouTube URL or id: {url_or_id}")


def _transcript_via_ytdlp(vid: str) -> str:
    import tempfile

    import yt_dlp

    with tempfile.TemporaryDirectory() as d:
        opts = {"skip_download": True, "writesubtitles": True, "writeautomaticsub": True,
                "subtitleslangs": ["en.*"], "subtitlesformat": "vtt", "outtmpl": f"{d}/v", "quiet": True, "noprogress": True,
                "ignore_no_formats_error": True,
                # the android_vr client still gets captions from cloud IPs that YouTube bot-checks
                "extractor_args": {"youtube": {"player_client": ["android_vr", "default"]}}}
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={vid}"])
        vtts = sorted(Path(d).glob("*.vtt"))
        if not vtts:
            raise RuntimeError(f"No English captions found for {vid}")
        lines = []
        for line in vtts[0].read_text().splitlines():
            line = re.sub(r"<[^>]+>", "", line).strip()
            if not line or "-->" in line or line.startswith(("WEBVTT", "Kind:", "Language:")):
                continue
            if not lines or lines[-1] != line:  # auto-captions repeat each line
                lines.append(line)
        return " ".join(lines)


def get_transcript(source: str) -> tuple[str, str]:
    """Return (key, text). `source` is a YouTube URL/id or a path to a .txt transcript."""
    p = Path(source)
    if p.suffix == ".txt" and p.exists():
        return p.stem, p.read_text()
    vid = video_id(source)
    TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
    cached = TRANSCRIPTS / f"{vid}.txt"
    if cached.exists():
        return vid, cached.read_text()
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        fetched = YouTubeTranscriptApi().fetch(vid, languages=["en", "en-US", "en-GB"])
        text = " ".join(s.text for s in fetched.snippets)
    except Exception:  # YouTube blocks this library from cloud IPs; yt-dlp usually still works
        text = _transcript_via_ytdlp(vid)
    cached.write_text(text)
    return vid, text


def extract(transcript: str, source_url: str | None = None) -> VideoExtraction:
    """Ask Claude for a VideoExtraction as JSON and validate it.

    Structured outputs reject this schema as too complex, so the schema goes in the
    prompt and pydantic validates the reply (one retry with the validation error).
    """
    import anthropic

    client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))
    schema = json.dumps(VideoExtraction.model_json_schema())
    messages = [{
        "role": "user",
        "content": f"Source: {source_url or 'unknown'}\n\n<transcript>\n{transcript}\n</transcript>\n\n"
                   f"Reply with ONLY a JSON object matching this JSON schema, no prose:\n{schema}",
    }]
    for attempt in range(2):
        resp = client.messages.create(
            model=MODEL, max_tokens=16000, thinking={"type": "adaptive"},
            output_config={"effort": "high"}, system=SYSTEM, messages=messages,
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError("Extraction refused")
        text = "".join(b.text for b in resp.content if b.type == "text")
        m = re.search(r"\{.*\}", text, re.S)
        try:
            out = VideoExtraction.model_validate_json(m.group(0) if m else text)
            break
        except Exception as e:
            if attempt:
                raise RuntimeError(f"Extraction returned invalid JSON: {e}") from e
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": f"That JSON failed validation:\n{e}\nReply with the corrected JSON only."}]
    for s in out.strategies:
        s.source_url = s.source_url or source_url
    return out


def learn(source: str) -> tuple[VideoExtraction, list[Path]]:
    key, text = get_transcript(source)
    url = source if source.startswith("http") else None
    result = extract(text, url)
    LEARNED.mkdir(parents=True, exist_ok=True)
    (LEARNED / f"{key}.extraction.json").write_text(result.model_dump_json(indent=2, exclude_none=True))
    paths = []
    for i, s in enumerate(result.strategies):
        slug = re.sub(r"[^a-z0-9]+", "_", s.name.lower()).strip("_")[:40] or f"s{i}"
        path = LEARNED / f"{key}_{slug}.json"
        save_spec(s, path)
        paths.append(path)
    return result, paths


if __name__ == "__main__":
    import sys

    res, files = learn(sys.argv[1])
    print(json.dumps({"summary": res.summary, "testable": res.has_testable_strategy,
                      "saved": [str(f) for f in files]}, indent=2))
