#!/usr/bin/env python3
"""Install validated session timing, preserving a complete file rollback set."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--expected-boot-id', required=True)
p.add_argument('--expected-manifest-sha256', required=True)
p.add_argument('--backup', type=Path, required=True)
a = p.parse_args()
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
assert os.geteuid() == 0 and os.uname().machine == 'aarch64'
assert Path('/etc/a081-rootfs-id').read_text().strip() == 'a081-steamos-arm64'
assert Path('/proc/sys/kernel/random/boot_id').read_text().strip() == a.expected_boot_id
prefix = Path('/opt/mix-vr-shell-controller')
assert sha(prefix / 'manifest.json') == a.expected_manifest_sha256
assert not a.backup.exists() and a.backup.is_relative_to('/osstore/updates')
subprocess.run(['sha256sum', '--quiet', '-c', 'SHA256SUMS'], cwd=prefix, check=True)
for unit in ('a081-mix-vr-shell-controller.service', 'a081-mix-vr-shell-controller-tracking.service'):
    assert subprocess.run(['systemctl', 'is-active', '--quiet', unit]).returncode != 0, unit
launch = (prefix / 'launch.py').read_text()
assert launch.count("'--force-composition', ") == 1
launch = launch.replace("'--force-composition', ", '')
ready = (prefix / 'ready.py').read_text()
anchor = "print(json.dumps({'phase': 'native_ui_started', 'trial': data['trial'],"
assert ready.count(anchor) == 1 and 'present-offset.py' not in ready
ready = ready.replace(anchor, "start('present-offset', ['python3', str(PREFIX / 'present-offset.py')])\n" + anchor)
helper = Path(__file__).with_name('present-offset.py').read_text()
updates = {'launch.py': launch, 'ready.py': ready, 'present-offset.py': helper}
for name, content in updates.items():
    compile(content, name, 'exec')
manifest = json.loads((prefix / 'manifest.json').read_text())
manifest['runtime_present_timing'] = {
    'offset_ms': -11, 'force_composition': False,
    'reapply_on_compositor_restart': True, 'reassert_seconds': 10,
    'prediction_property_changed': False,
    'evidence': 'reports/device/steamvr-bottleneck-r1/',
}
for name, content in updates.items():
    manifest['files'][name] = hashlib.sha256(content.encode()).hexdigest()
updates['manifest.json'] = json.dumps(manifest, indent=2) + '\n'
sums = {}
for line in (prefix / 'SHA256SUMS').read_text().splitlines():
    digest, name = line.split(None, 1)
    sums[name.lstrip('*')] = digest
for name, content in updates.items():
    sums[name] = hashlib.sha256(content.encode()).hexdigest()
updates['SHA256SUMS'] = ''.join(f'{digest}  {name}\n' for name, digest in sorted(sums.items()))
a.backup.mkdir(parents=True)
before = {}
for name in updates:
    path = prefix / name
    before[name] = sha(path) if path.exists() else None
    if path.exists():
        shutil.copy2(path, a.backup / name)
(a.backup / 'before.json').write_text(json.dumps(before, indent=2) + '\n')
try:
    for name, content in updates.items():
        path = prefix / name
        temporary = path.with_name(path.name + '.timing-new')
        temporary.write_text(content)
        temporary.chmod(path.stat().st_mode & 0o777 if path.exists() else 0o644)
        temporary.replace(path)
    subprocess.run(['sha256sum', '--quiet', '-c', 'SHA256SUMS'], cwd=prefix, check=True)
except BaseException:
    for name, digest in before.items():
        if digest is None:
            (prefix / name).unlink(missing_ok=True)
        else:
            shutil.copy2(a.backup / name, prefix / name)
    raise
os.sync()
result = {'backup': str(a.backup), 'before': before,
          'after': {name: sha(prefix / name) for name in updates},
          'partition_written': False, 'driver_changed': False}
(a.backup / 'applied.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
