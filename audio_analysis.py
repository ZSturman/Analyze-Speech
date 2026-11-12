"""Local transcript and recording analysis. Imports have no external side effects."""

import argparse
from array import array
from collections import Counter
from datetime import datetime
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import wave

WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)
RECORDING_NAME = re.compile(
    r"(?P<speaker>.+)_(?P<date>\d{4}-\d{2}-\d{2})_(?P<time>\d{2}-\d{2}-\d{2})(?:_transcript)?$"
)


def analyze_text(text):
    """Heuristic counts, without downloads or inferred health/emotion labels."""
    words = WORD.findall(text)
    sentences = sum(bool(WORD.search(part)) for part in re.split(r"[.!?]+", text))
    counts = Counter(word.casefold() for word in words)
    return {
        "word_count": len(words),
        "sentence_count": sentences,
        "average_words_per_sentence": len(words) / sentences if sentences else 0.0,
        "filler_counts": {word: counts[word] for word in ("um", "uh", "erm", "hmm")},
    }


def transcript_record(path, base=None):
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    match = RECORDING_NAME.fullmatch(path.stem)
    recorded_at = None
    if match:
        try:
            recorded_at = datetime.strptime(
                f"{match['date']}_{match['time']}", "%Y-%m-%d_%H-%M-%S"
            ).isoformat()
        except ValueError:
            pass
    return {
        "path": path.relative_to(base).as_posix() if base else path.name,
        "speaker": match["speaker"] if match else None,
        "recorded_at_local": recorded_at,
        "metadata_status": "parsed" if recorded_at else "unrecognized_filename",
        **analyze_text(text),
    }


def analyze_corpus(directory):
    directory = Path(directory)
    if not directory.is_dir():
        raise ValueError(f"Transcript directory does not exist: {directory}")
    paths = sorted(p for p in directory.rglob("*") if p.is_file() and p.suffix.lower() == ".txt")
    if not paths:
        raise ValueError("No .txt transcripts found in the supplied directory")
    records, errors = [], []
    for path in paths:
        try:
            records.append(transcript_record(path, directory))
        except (OSError, UnicodeError) as exc:
            errors.append({"path": path.relative_to(directory).as_posix(), "error": str(exc)})
    speakers = {}
    for record in records:
        summary = speakers.setdefault(record["speaker"] or "(unknown)", {"transcript_count": 0, "word_count": 0})
        summary["transcript_count"] += 1
        summary["word_count"] += record["word_count"]
    return {
        "schema_version": 1,
        "files_found": len(paths),
        "transcripts_analyzed": len(records),
        "empty_transcripts": sum(record["word_count"] == 0 for record in records),
        "unrecognized_filenames": sum(record["metadata_status"] != "parsed" for record in records),
        "word_count": sum(record["word_count"] for record in records),
        "speakers": speakers,
        "errors": errors,
        "records": records,
    }


def read_pcm(path):
    """Read uncompressed 16-bit WAV samples, preserving channels for RMS."""
    with wave.open(str(path), "rb") as audio:
        if audio.getsampwidth() != 2 or audio.getcomptype() != "NONE":
            raise ValueError("Expected uncompressed 16-bit PCM WAV")
        channels, rate = audio.getnchannels(), audio.getframerate()
        samples = array("h", audio.readframes(audio.getnframes()))
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        raise ValueError("Audio contains no samples")
    return samples, rate, channels


def pcm_stats(path, pause_threshold=-40.0, min_pause_length=0.5):
    """Measure RMS and quiet runs in 10 ms windows, including recording edges."""
    if not math.isfinite(pause_threshold) or pause_threshold > 0:
        raise ValueError("Pause threshold must be finite and at most 0 dBFS")
    if not math.isfinite(min_pause_length) or min_pause_length <= 0:
        raise ValueError("Minimum pause length must be finite and greater than zero")
    samples, rate, channels = read_pcm(path)
    frames = len(samples) // channels
    window_frames = max(1, round(rate * 0.01))
    duration = frames / rate
    start = None
    pauses = []
    total_power = 0
    for frame in range(0, frames, window_frames):
        end = min(frame + window_frames, frames)
        window = samples[frame * channels:end * channels]
        power = sum(sample * sample for sample in window)
        total_power += power
        rms = math.sqrt(power / len(window))
        dbfs = 20 * math.log10(rms / 32768) if rms else -math.inf
        if dbfs < pause_threshold:
            if start is None:
                start = frame / rate
        elif start is not None:
            if frame / rate - start + 1e-9 >= min_pause_length:
                pauses.append((start, frame / rate))
            start = None
    if start is not None and duration - start + 1e-9 >= min_pause_length:
        pauses.append((start, duration))
    rms = math.sqrt(total_power / len(samples))
    return {
        "duration_seconds": duration,
        "rms_dbfs": 20 * math.log10(rms / 32768) if rms else None,
        "pauses": [{"start_seconds": start, "end_seconds": end} for start, end in pauses],
        "pause_count": len(pauses),
        "average_pause_seconds": sum(end - start for start, end in pauses) / len(pauses) if pauses else 0.0,
        "pause_threshold_dbfs": pause_threshold,
        "minimum_pause_seconds": min_pause_length,
        "window_seconds": window_frames / rate,
    }


def detect_pauses(audio_file, pause_threshold=-40.0, min_pause_length=0.5):
    """Compatibility API: (start_ms, end_ms) for 16-bit PCM WAV pauses."""
    return [
        (round(pause["start_seconds"] * 1000), round(pause["end_seconds"] * 1000))
        for pause in pcm_stats(audio_file, pause_threshold, min_pause_length)["pauses"]
    ]


def analyze_audio(path, transcript=None, pitch=False, pause_threshold=-40.0, min_pause_length=0.5):
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Recording does not exist: {path}")
    with tempfile.TemporaryDirectory(prefix="speech-analysis-") as temporary:
        wav = path
        try:
            read_pcm(path)
        except (wave.Error, EOFError, ValueError):
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise ValueError("This format needs FFmpeg; supply a 16-bit PCM WAV or install FFmpeg")
            wav = Path(temporary) / "audio.wav"
            result = subprocess.run(
                [ffmpeg, "-nostdin", "-v", "error", "-i", str(path.resolve()),
                 "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
                capture_output=True, text=True, check=False,
            )
            if result.returncode:
                raise ValueError("FFmpeg could not decode the recording; check that it contains an audio stream")
        metrics = pcm_stats(wav, pause_threshold, min_pause_length)
        if pitch:
            try:
                import librosa
                import numpy as np
            except ImportError as exc:
                raise ValueError('Pitch needs optional dependencies: python3 -m pip install -e ".[pitch]"') from exc
            y, rate = librosa.load(wav, sr=16000)
            f0, voiced, _ = librosa.pyin(y, sr=rate, fmin=65, fmax=600, frame_length=1024)
            valid = f0[voiced & np.isfinite(f0)]
            metrics["mean_voiced_pitch_hz"] = float(valid.mean()) if valid.size else None
        if transcript is not None:
            metrics["transcript"] = transcript_record(transcript)
            metrics["words_per_minute"] = metrics["transcript"]["word_count"] * 60 / metrics["duration_seconds"]
        return {"schema_version": 1, "recording": path.name, **metrics}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Analyze local transcripts and recordings without uploading data.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, description in (
        ("transcript", "Count words, sentences, and simple fillers in one UTF-8 transcript"),
        ("corpus", "Analyze a directory of transcripts recursively"),
        ("audio", "Measure recording duration, loudness, and pauses"),
    ):
        command = commands.add_parser(name, help=description)
        command.add_argument("input", type=Path)
        command.add_argument("--output", type=Path, help="Write JSON to a new file; existing files are protected")
        if name == "audio":
            command.add_argument("--transcript", type=Path, help="Matching UTF-8 text for speaking rate")
            command.add_argument("--pitch", action="store_true", help="Estimate voiced pitch using optional librosa dependencies")
            command.add_argument("--pause-threshold", type=float, default=-40.0, metavar="DBFS")
            command.add_argument("--min-pause-length", type=float, default=0.5, metavar="SECONDS")
    args = parser.parse_args(argv)
    try:
        if args.command == "transcript":
            report = {"schema_version": 1, **transcript_record(args.input)}
        elif args.command == "corpus":
            report = analyze_corpus(args.input)
        else:
            report = analyze_audio(args.input, args.transcript, args.pitch, args.pause_threshold, args.min_pause_length)
        serialized = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as output:
                output.write(serialized)
            print(f"Report saved to {args.output}", file=sys.stderr)
        else:
            print(serialized, end="")
        return 1 if report.get("errors") else 0
    except (OSError, ValueError, UnicodeError, wave.Error, EOFError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    sys.exit(main())
