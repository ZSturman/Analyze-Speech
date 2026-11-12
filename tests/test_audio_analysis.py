from array import array
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

import audio_analysis as analysis


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def wav(self, sections, channels=1):
        path = self.root / "fixture.wav"
        samples = array("h")
        for milliseconds, amplitude in sections:
            samples.extend([amplitude] * milliseconds * channels)
        if sys.byteorder != "little":
            samples.byteswap()
        with wave.open(str(path), "wb") as output:
            output.setnchannels(channels)
            output.setsampwidth(2)
            output.setframerate(1000)
            output.writeframes(samples.tobytes())
        return path

    def test_text_and_empty_text(self):
        result = analysis.analyze_text("Um, I'm ready! Uh, let's go.")
        self.assertEqual(result["word_count"], 6)
        self.assertEqual(result["sentence_count"], 2)
        self.assertEqual(result["filler_counts"]["um"], 1)
        self.assertEqual(analysis.analyze_text("")["average_words_per_sentence"], 0)

    def test_filename_metadata_and_bom(self):
        path = self.root / "Example_Speaker_2023-04-29_18-34-49.txt"
        path.write_text("\ufeffHello world.", encoding="utf-8")
        record = analysis.transcript_record(path)
        self.assertEqual(record["speaker"], "Example_Speaker")
        self.assertEqual(record["recorded_at_local"], "2023-04-29T18:34:49")
        self.assertEqual(record["word_count"], 2)

    def test_invalid_date_is_flagged(self):
        path = self.root / "Example_2023-99-29_18-34-49.txt"
        path.write_text("Hello.")
        self.assertEqual(analysis.transcript_record(path)["metadata_status"], "unrecognized_filename")

    def test_corpus_preserves_unknown_and_reports_decode_failure(self):
        (self.root / "nested").mkdir()
        (self.root / "nested/Example_2023-04-29_18-34-49.txt").write_text("Hello world.")
        (self.root / "unknown.txt").write_text("")
        (self.root / "invalid.txt").write_bytes(b"\xff")
        result = analysis.analyze_corpus(self.root)
        self.assertEqual(result["files_found"], 3)
        self.assertEqual(result["transcripts_analyzed"], 2)
        self.assertEqual(result["empty_transcripts"], 1)
        self.assertEqual(result["unrecognized_filenames"], 1)
        self.assertEqual(len(result["errors"]), 1)
        self.assertNotIn("text", result["records"][0])

    def test_empty_and_missing_corpus(self):
        for directory in (self.root, self.root / "missing"):
            with self.assertRaises(ValueError):
                analysis.analyze_corpus(directory)

    def test_leading_interior_trailing_and_short_pauses(self):
        path = self.wav([(500, 0), (100, 10000), (600, 0), (100, 10000), (200, 0), (100, 10000), (700, 0)])
        self.assertEqual(analysis.detect_pauses(path), [(0, 500), (600, 1200), (1600, 2300)])

    def test_silence_and_no_pauses_are_json_safe(self):
        result = analysis.analyze_audio(self.wav([(1000, 0)]))
        self.assertIsNone(result["rms_dbfs"])
        self.assertEqual(result["pause_count"], 1)
        json.dumps(result, allow_nan=False)
        result = analysis.analyze_audio(self.wav([(1000, 10000)]))
        self.assertEqual(result["pause_count"], 0)
        self.assertEqual(result["average_pause_seconds"], 0)

    def test_stereo_duration_and_transcript_rate(self):
        text = self.root / "speech.txt"
        text.write_text("Hello world.")
        result = analysis.analyze_audio(self.wav([(1000, 10000)], channels=2), text)
        self.assertEqual(result["duration_seconds"], 1)
        self.assertEqual(result["words_per_minute"], 120)

    def test_invalid_thresholds(self):
        path = self.wav([(100, 0)])
        for threshold, length in ((1, 0.5), (float("nan"), 0.5), (-40, 0), (-40, float("inf"))):
            with self.assertRaises(ValueError):
                analysis.pcm_stats(path, threshold, length)

    def test_missing_audio(self):
        with self.assertRaises(ValueError):
            analysis.analyze_audio(self.root / "missing.wav")

    def test_import_has_no_side_effects(self):
        script = "import sys; sys.path.insert(0, sys.argv[1]); import audio_analysis"
        result = subprocess.run([sys.executable, "-c", script, str(Path(analysis.__file__).parent)],
                                cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_cli_output_refuses_overwrite(self):
        transcript = self.root / "demo.txt"
        transcript.write_text("Hello.")
        output = self.root / "report.json"
        with redirect_stderr(io.StringIO()):
            self.assertEqual(analysis.main(["transcript", str(transcript), "--output", str(output)]), 0)
        original = output.read_bytes()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failure:
            analysis.main(["transcript", str(transcript), "--output", str(output)])
        self.assertEqual(failure.exception.code, 2)
        self.assertEqual(output.read_bytes(), original)

    def test_partial_corpus_exit_status(self):
        (self.root / "invalid.txt").write_bytes(b"\xff")
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(analysis.main(["corpus", str(self.root)]), 1)
        self.assertEqual(len(json.loads(output.getvalue())["errors"]), 1)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg unavailable")
    def test_compressed_audio_conversion_preserves_source(self):
        source = self.wav([(1000, 10000)])
        compressed = self.root / "fixture.flac"
        subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-i", str(source), str(compressed)], check=True)
        original = compressed.read_bytes()
        self.assertAlmostEqual(analysis.analyze_audio(compressed)["duration_seconds"], 1)
        self.assertEqual(compressed.read_bytes(), original)

    def test_format_error_without_ffmpeg(self):
        path = self.root / "invalid.mp4"
        path.write_bytes(b"invalid")
        with patch("audio_analysis.shutil.which", return_value=None), self.assertRaisesRegex(ValueError, "FFmpeg"):
            analysis.analyze_audio(path)


if __name__ == "__main__":
    unittest.main()
