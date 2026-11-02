#!/usr/bin/env python3
"""Summarize exported real session evidence; no device access or packet replay."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import struct


def packets(path, size):
    data = path.read_bytes()
    if len(data) % size:
        raise ValueError(f'Truncated {size}-byte capture: {path}')
    for offset in range(0, len(data), size):
        yield data[offset:offset + size]


def head(path):
    counts, states, warnings, read_status, qualities = Counter(), Counter(), Counter(), Counter(), Counter()
    first = last = None
    for b in packets(path, 168):
        magic, version, size, flags = struct.unpack_from('<4I', b)
        if (magic, version, size) != (0x54523841, 1, 168):
            raise ValueError('Unexpected HMD packet header')
        gyro, accel, sample, sent, pose, camera = struct.unpack_from('<6Q', b, 32)
        values = struct.unpack_from('<14f', b, 80)
        state, warning, index, bracket, copied = struct.unpack_from('<5I', b, 136)
        q, quality = values[6:10], values[13]
        finite = all(math.isfinite(v) for v in values)
        clock = bool(flags & 2) and sample > 0 and sample <= sent and sent - sample < 100000000 and bracket <= 50000
        counts['records'] += 1
        counts['quality_one'] += bool(flags & 4) and quality == 1
        counts['quality_zero'] += bool(flags & 4) and quality == 0
        counts['clock_invalid'] += not clock
        counts['nonfinite'] += not finite
        counts['timestamp_mismatch'] += bool(flags & 4) and pose != gyro
        counts['camera_submitted'] += bool(flags & 8)
        counts['eligible_at_send_not_delivery'] += (flags == 15 and copied == 1 and quality == 1 and state == 4 and
            index < 80 and pose == gyro and 0 < camera <= gyro and gyro - camera < 100000000 and clock and finite and
            abs(sum(v * v for v in q) - 1) <= .01)
        states[str(state)] += 1
        warnings[str(warning)] += 1
        read_status[str(copied)] += 1
        qualities[str(quality)] += 1
        if first is None:
            first = gyro
        last = gyro
    return dict(counts=counts, tracking_states=states, warning_flags=warnings, pose_read_status=read_status,
                qualities=qualities, first_gyro_ns=first, last_gyro_ns=last)


def controllers(path):
    wire = struct.Struct('<4I4Q2IfI7fI')
    hands = [dict(counts=Counter(), flags=Counter(), profiles=Counter(), confidence=Counter()) for _ in range(2)]
    for b in packets(path, wire.size):
        v = wire.unpack(b)
        if v[:3] != (0x43503841, 1, 96) or v[8] > 1 or v[11] or v[-1]:
            raise ValueError('Unexpected controller packet header')
        flags, sample, sent, profile, confidence = v[3], v[6], v[7], v[9], v[10]
        h = hands[v[8]]
        h['counts']['records'] += 1
        h['counts']['connected'] += bool(flags & 1)
        h['counts']['optical_valid'] += bool(flags & 32)
        h['counts']['eligible_at_send_not_delivery'] += (flags == 63 and profile == 1 and confidence >= .1 and
            all(math.isfinite(x) for x in v[12:19]) and 0 < sample <= sent and sent - sample < 100000000 and
            abs(sum(x * x for x in v[15:19]) - 1) < .01)
        h['flags'][str(flags)] += 1
        h['profiles'][str(profile)] += 1
        h['confidence'][str(confidence)] += 1
    return {'left': hands[0], 'right': hands[1]}


def qcft(path):
    """Current QCFT v2 only; no guessing a record layout from file length."""
    counts, phases, statuses = Counter(), Counter(), Counter()
    streams = set()
    first = last = None
    for b in packets(path, 216):
        version, length, flags, sequence, index, cookie, csid, rdi = struct.unpack_from('<8I', b)
        if (version, length) != (2, 216) or any(struct.unpack_from('<4I', b, 144)):
            raise ValueError('Expected QCFT v2 with zero prefix reserved: ' + str(path))
        stream, sof, eof = struct.unpack_from('<3Q', b, 32)
        sof_boot = struct.unpack_from('<Q', b, 96)[0]
        sensor_sequence, phase, lines, gain, fll, status = struct.unpack_from('<5Ii', b, 160)
        exposure, apply_sof, applied_boot, anchor_sof = struct.unpack_from('<4Q', b, 184)
        counts['records'] += 1
        counts['base_timing_valid'] += (flags & 7) == 7
        active, valid = bool(flags & 8), bool(flags & 16)
        counts['pattern_active'] += active
        counts['exposure_valid'] += valid
        statuses[str(status)] += 1
        if active:
            phases[str(phase)] += 1
        if valid:
            consistent = (active and status == 0 and phase < 3 and 0 < lines <= fll - 20 and
                          16 <= gain <= 1023 and exposure > 0 and
                          0 < anchor_sof <= apply_sof < sof < eof and 0 < applied_boot < sof_boot)
            counts['exposure_association_invalid'] += not consistent
        streams.add((csid, rdi, stream))
        first = sof if first is None else first
        last = sof
    return dict(counts=counts, exposure_phases=phases, exposure_statuses=statuses,
                streams=sorted(streams), first_sof_qtimer=first, last_sof_qtimer=last,
                physical_optical_phase_verified=False)


def json_rows(path):
    for line in path.read_text(errors='replace').splitlines():
        if line.startswith('{'):
            yield json.loads(line)


def observer(path):
    hands, summaries = [Counter(), Counter()], []
    for row in json_rows(path):
        if row.get('kind') == 'controller_observation_end':
            summaries.append(row)
        if row.get('kind') != 'controller_pose':
            continue
        h = hands[row['hand']]
        h['records'] += 1
        for name in ('present', 'connected', 'valid', 'correct_role', 'finite'):
            h[name] += bool(row.get(name))
    return {'left': hands[0], 'right': hands[1], 'summaries': summaries}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory', type=Path, help='Export directory containing raw/ and optional camera/, state/, session.json')
    p.add_argument('--qcft-directory', type=Path, help='Directory containing current 216-byte QCFT v2 archives')
    a = p.parse_args()
    root = a.directory.resolve()
    raw = root / 'raw' if (root / 'raw').is_dir() else root
    result = {'source': str(root), 'physical_accuracy_verified': False}
    for key, name, parse in [('head', 'packets.bin', head), ('controllers', 'controller-packets.bin', controllers),
                              ('openvr', 'controller-openvr-observe.jsonl', observer)]:
        path = raw / name
        result[key] = parse(path) if path.exists() else {'missing': str(path)}
    sdk = raw / 'controller-sdk.log'
    if sdk.exists():
        rows = list(json_rows(sdk))
        result['sdk'] = {'kinds': Counter(row.get('kind', 'unknown') for row in rows),
            'states': Counter(str(row['hand']) + ':' + str(row['ctrl_state']) for row in rows if row.get('kind') == 'controller_status'),
            'transitions': [row for row in rows if row.get('kind') == 'controller_state'],
            'summaries': [row for row in rows if row.get('kind') == 'controller_sdk_summary']}
    for key, path in [('runtime', raw / 'tracking-result.json'), ('controller_runtime', raw / 'controller-session-result.json'),
                      ('camera', root / 'camera/result.json'), ('session', root / 'session.json')]:
        if path.exists():
            value = json.loads(path.read_text())
            result[key] = value if key != 'session' else {k: value.get(k) for k in
                ('trial', 'cleanup_complete', 'cleanup_pass', 'cleanup_errors', 'runtime_pass', 'runtime_errors',
                 'validation_pass', 'validation_errors', 'pose_watch_result')}
    camera_root = a.qcft_directory.resolve() if a.qcft_directory else root / 'camera'
    if camera_root.is_dir():
        archives = sorted(p for p in camera_root.rglob('camera-slot*-timing.bin*') if p.is_file())
        result['qcft'] = {str(p.relative_to(camera_root)): qcft(p) for p in archives}
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
