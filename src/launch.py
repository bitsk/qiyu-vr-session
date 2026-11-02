#!/usr/bin/env python3
"""Start Frame's deferred Gamescope contract, or request native VR shutdown."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from common import current, environment, PREFIX

assert os.getuid() == 1000 and os.uname().machine == 'aarch64'
data = current()
env = environment(data)
state = Path(data['directory'])
action = sys.argv[1] if len(sys.argv) > 1 else 'start'
if action == 'shutdown':
    env.pop('ENABLE_GAMESCOPE_WSI', None)
    env['DISABLE_GAMESCOPE_WSI'] = '1'
    display = state / 'display.json'
    if display.exists():
        env.update(json.loads(display.read_text()))
    # Ask the native runtime to release its graphics objects before systemd
    # signals any members of the owning cgroup (SIGTERM alone leaked DMA).
    with (state / 'logs/native-shutdown.log').open('w') as log:
        result = subprocess.run(['/opt/steamvr/bin/linuxarm64/vrstartup', '-shutdown'],
                                env=env, timeout=12, stdout=log, stderr=subprocess.STDOUT)
    completed = False
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        running = [name for name in ('vrserver', 'vrcompositor')
                   if subprocess.run(['pgrep', '-u', '1000', '-x', name],
                                     stdout=subprocess.DEVNULL).returncode == 0]
        if not running:
            completed = True
            break
        time.sleep(0.2)
    (state / 'shutdown.json').write_text(json.dumps({'native_shutdown_exit': result.returncode,
                'native_shutdown_completed': completed, 'remaining': running}) + '\n')
    raise SystemExit(result.returncode or (0 if completed else 1))
if action != 'start':
    raise SystemExit('Expected start or shutdown')
if not os.environ.get('XDG_SESSION_ID'):
    raise RuntimeError('A PAM/logind login session is required')
for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'GAMESCOPE_WAYLAND_DISPLAY'):
    env.pop(name, None)
env.update({'LIBSEAT_BACKEND': 'logind', 'XDG_SESSION_TYPE': 'wayland',
            'XDG_CURRENT_DESKTOP': 'gamescope', 'QT_IM_MODULE': 'xim', 'GTK_IM_MODULE': 'xim'})
env['MIX_VR_SHELL_MAIN_PID'] = str(os.getpid())
env['MIX_VR_SHELL_MAIN_START'] = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19]
args = ['/usr/bin/gamescope', '--allow-deferred-backend', '--backend', 'openvr',
        '--force-composition', '--steam', '--vr-session-manager',
        '--virtual-connector-strategy', 'PerAppId', '--xwayland-count', '2',
        '-W', '1600', '-H', '1000', '-w', '1600', '-h', '1000', '-r', '72',
        '--vr-overlay-key', 'valve.steam.gamepadui.fallback',
        '--vr-app-overlay-key', 'valve.steam.desktopgame',
        '--vr-overlay-show-immediately', '--vr-overlay-physical-width', '1.8',
        '--', '/usr/bin/python3', str(PREFIX / 'ready.py')]
print(json.dumps({'phase': 'gamescope_start', 'argv': args, 'trial': data['trial']}), flush=True)
os.execvpe(args[0], args, env)
