#!/usr/bin/env python3
"""Inspect a completed QEMU recording and save a short audible PCM excerpt."""
import argparse
import array
import json
from pathlib import Path
import wave

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('recording', type=Path)
args = parser.parse_args()
with wave.open(str(args.recording), 'rb') as source:
    assert source.getsampwidth() == 2
    params = source.getparams()
    frames = source.readframes(source.getnframes())
samples = array.array('h', frames)
peak = max(map(abs, samples), default=0)
first = next((i for i, value in enumerate(samples) if abs(value) > 100), None)
report = {'file': args.recording.name, 'seconds': len(samples) / params.nchannels / params.framerate,
          'peak': peak, 'nonzero_samples': sum(value != 0 for value in samples)}
assert first is not None, 'No audible signal found in the completed recording'
start = max(0, first // params.nchannels - params.framerate // 4)
with wave.open(str(args.recording.with_suffix('.sample.wav')), 'wb') as output:
    output.setparams(params)
    output.writeframes(frames[start * params.nchannels * 2:(start + params.framerate * 12) * params.nchannels * 2])
args.recording.with_suffix('.evidence.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report))
