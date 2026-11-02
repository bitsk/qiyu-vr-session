#!/usr/bin/env python3
"""Stop only the exact managed tty7 KWin profile, including its PAM orphan."""
import json
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import time

UNIT = 'a081-steamos-desktop.service'
ARGV = ['/opt/mix-steamos-graphics/bin/kwin_wayland', '--drm', '--xwayland', '--no-lockscreen',
        '--exit-with-session', '/usr/local/bin/mix-steam desktop -nobigpicture']


def identity(pid):
    path = Path('/proc') / str(pid)
    try:
        if path.stat().st_uid != 1000:
            return None
        argv = [s.decode() for s in (path / 'cmdline').read_bytes().rstrip(b'\0').split(b'\0')]
        if argv != ARGV or str((path / 'exe').readlink()) != ARGV[0]:
            return None
        env = dict(item.split(b'=', 1) for item in (path / 'environ').read_bytes().split(b'\0') if b'=' in item)
        session = env.get(b'XDG_SESSION_ID', b'').decode()
        if (env.get(b'HOME') != b'/home/steamos' or env.get(b'XDG_VTNR') != b'7' or
                env.get(b'MIX_STEAMOS_DESKTOP_MODE') != b'steam' or not re.fullmatch('[a-zA-Z0-9]+', session)):
            return None
        scope = '/user.slice/user-1000.slice/session-' + session + '.scope'
        if scope not in (path / 'cgroup').read_text().splitlines()[0]:
            return None
        fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z':
            return None
        return {'pid': int(pid), 'start_ticks': fields[19], 'session': session, 'argv': argv}
    except (FileNotFoundError, ProcessLookupError):
        return None


def owners():
    found = []
    for path in Path('/proc').glob('[0-9]*'):
        try:
            if path.stat().st_uid != 1000 or (path / 'comm').read_text().strip() != 'kwin_wayland':
                continue
            record = identity(path.name)
            if record is None:
                # Refuse an unrelated compositor; never broaden the PID match.
                fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
                if fields[0] != 'Z':
                    raise RuntimeError('Unrecognized KWin owner: ' + path.name)
            else:
                found.append(record)
        except (FileNotFoundError, ProcessLookupError):
            continue
    if len(found) > 1:
        raise RuntimeError('Multiple managed KWin sessions need explicit inspection')
    return found


def stop_desktop():
    result = {'desktop_owners_before_stop': owners(), 'signals': []}
    for record in result['desktop_owners_before_stop']:
        pid = record['pid']
        descriptor = os.pidfd_open(pid)
        try:
            waiter = select.poll()
            waiter.register(descriptor, select.POLLIN)
            for number, wait_ms in ((signal.SIGTERM, 6000), (signal.SIGKILL, 3000)):
                if waiter.poll(0):
                    break
                if identity(pid) != record:
                    raise RuntimeError('Pinned desktop process identity changed')
                # Let KWin release KMS before stopping its PAM/tty service.
                # Forced fallback is limited to this exact pinned process.
                signal.pidfd_send_signal(descriptor, number)
                result['signals'].append({'pid': pid, 'signal': number.name})
                if waiter.poll(wait_ms):
                    break
            else:
                raise RuntimeError('Owned KWin did not exit after bounded stop')
        finally:
            os.close(descriptor)
    subprocess.run(['systemctl', 'stop', UNIT], check=True, timeout=25)
    if owners():
        raise RuntimeError('Managed desktop still exists after stop')
    result['desktop_processes_gone'] = True
    print(json.dumps(result), flush=True)
    return result


def restore_desktop():
    subprocess.run(['systemctl', 'start', UNIT], check=True, timeout=20)
    deadline, stable_since = time.monotonic() + 10, None
    pending_owner = None
    while time.monotonic() < deadline:
        state = subprocess.check_output(['systemctl', 'is-active', UNIT], text=True).strip()
        try:
            current_owners = owners() if state == 'active' else []
        except RuntimeError as error:
            if not str(error).startswith('Unrecognized KWin owner: '):
                raise
            # A newly spawned KWin can change identity during the /proc reads.
            # Only this restore window retries; stop_desktop remains strict.
            pending_owner = str(error)
            current_owners = []
        if state == 'active' and current_owners:
            stable_since = stable_since or time.monotonic()
            if time.monotonic() - stable_since >= 2:
                return {'desktop_restore_active_seconds': 2}
        else:
            stable_since = None
        time.sleep(0.2)
    raise RuntimeError('Desktop did not remain active with its real KWin process' +
                       (': ' + pending_owner if pending_owner else ''))


if __name__ == '__main__':
    assert os.geteuid() == 0 and os.uname().machine == 'aarch64'
    assert Path('/etc/a081-rootfs-id').read_text().strip() == 'a081-steamos-arm64'
    assert b'A081' in Path('/proc/device-tree/model').read_bytes()
    stop_desktop()
