#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
import argparse,hashlib,json,shutil,py_compile
from pathlib import Path
r=Path(__file__).resolve().parents[1];p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
if a.output.exists():p.error('Choose a new output directory')
a.output.mkdir(parents=True)
for name in ['src','integration','plans','systemd']:
 if (r/name).exists():shutil.copytree(r/name,a.output/name)
for q in (a.output/'src').glob('*.py'):py_compile.compile(str(q),cfile=str(a.output/('check-'+q.stem+'.pyc')),doraise=True)
for q in a.output.glob('check-*.pyc'):q.unlink()
files={str(q.relative_to(a.output)):hashlib.sha256(q.read_bytes()).hexdigest() for q in a.output.rglob('*') if q.is_file()}
(a.output/'package-manifest.json').write_text(json.dumps({'files_sha256':files,'scope':'Session source package; requires separately built native runtime/tracking and imported vendor assets','device_tested':False},indent=2)+'\n')
