# Validation and next steps

Snapshot prepared November 12, 2026.

## Current state

Research tools, not clinical speech assessment. Real recordings, transcripts and derived personal outputs are deliberately excluded. FFmpeg is needed for formats other than supported WAV input.

## Validation

PASS: 15 standard-library tests using synthetic temporary fixtures. No personal recordings or transcripts were read.

Publication checks: the exact Git tree and its new history are scanned locally with Gitleaks before the repository becomes public. Reviewed scanner exceptions are restricted to synthetic fixture secrets, an invalid test token, a browser preference name and the published NIST AES test vector. Private histories are not included.

## Next action

Follow the README quick start, run the documented focused tests, then use isolated inputs for a manual walkthrough. Real hardware, release signing, background behavior and live-server compatibility are outside this publication check.
