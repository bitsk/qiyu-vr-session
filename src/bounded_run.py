#!/usr/bin/env python3
"""Run one real process; retain complete first/previous/current text log segments."""
import argparse
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import time

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--log', type=Path, required=True)
p.add_argument('--limit-bytes', type=int, default=16 * 1024 * 1024)
p.add_argument('command', nargs=argparse.REMAINDER)
a = p.parse_args()
if a.command and a.command[0] == '--':
    a.command.pop(0)
assert a.command and a.log.is_absolute() and 1048576 <= a.limit_bytes <= 67108864
assert not a.log.exists() and not a.log.is_symlink()
first, previous = Path(str(a.log) + '.first'), Path(str(a.log) + '.previous')
assert not first.exists() and not previous.exists()
pass_fds = ()
if 'A081_QVR_CAMERA_STOP_FD' in os.environ:
    stop_fd = int(os.environ['A081_QVR_CAMERA_STOP_FD'])
    assert stop_fd >= 3 and stat.S_ISFIFO(os.fstat(stop_fd).st_mode)
    pass_fds = (stop_fd,)
process = subprocess.Popen(a.command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, pass_fds=pass_fds)


def forward(number, _frame):
    if process.poll() is None:
        try:
            process.send_signal(number)
        except ProcessLookupError:
            pass


signal.signal(signal.SIGTERM, forward)
signal.signal(signal.SIGINT, forward)
segments = total = size = 0
output = a.log.open('xb', buffering=0)
try:
    while True:
        line = process.stdout.readline()
        if not line:
            break
        if size and size + len(line) > a.limit_bytes:
            output.close()
            a.log.replace(first if segments == 0 else previous)
            segments += 1
            output = a.log.open('xb', buffering=0)
            size = 0
        output.write(line)
        size += len(line)
        total += len(line)
finally:
    output.close()
    process.stdout.close()
    code = process.wait()
    retention = Path(str(a.log) + '.retention.json')
    temporary = Path(str(retention) + '.tmp')
    temporary.write_text(json.dumps({
        'exit': code, 'total_original_bytes': total, 'segment_limit_bytes': a.limit_bytes,
        'completed_segments': segments, 'current_segment_bytes': size,
        'retention': 'complete first segment and most recent previous/current segments; middle segments replaced',
        'ended_monotonic_ns': time.monotonic_ns()}, indent=2) + '\n')
    temporary.replace(retention)
raise SystemExit(code if code >= 0 else 128 - code)
