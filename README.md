# Analyze Speech

Local Python transcript and audio analysis with dependency-free core commands and synthetic tests.

**Status:** Research tools, not clinical speech assessment. Real recordings, transcripts and derived personal outputs are deliberately excluded. FFmpeg is needed for formats other than supported WAV input.

[Project portfolio](https://zacharysturman.com/projects/analyze-speech)

## Quick start

```sh
python3 audio_analysis.py --help
python3 audio_analysis.py transcript examples/demo_transcript.txt
```

## Usage

```sh
python3 audio_analysis.py audio /path/to/recording.wav
```

## How it works

A standard-library CLI computes transcript statistics and WAV measurements. FFmpeg conversion and optional pitch analysis extend the core without uploading source recordings.

## Testing

```sh
python3 -m unittest discover -s tests -v
```

See [validation and next steps](docs/NEXT_STEPS.md) for the checks performed on this snapshot. Automated fixture tests do not establish physical-device compatibility.

## Configuration and privacy

This is a curated source snapshot, published November 12, 2026. Configure your own endpoints, credentials and signing identities. Private development history, local environment files, personal recordings, runtime databases, signing materials and deployment automation are excluded. Deployment and device installation are explicit operator actions.

## License and attribution

Existing licenses and third-party notices apply to the files they cover. Publication of this snapshot does not grant a new license to original material; no additional license is granted where none is supplied.
