#!/usr/bin/env python3
"""Build the selected official RHVoice revision and unmodified voice datasets."""
import json
from pathlib import Path
import shutil
import subprocess

project = Path(__file__).resolve().parents[1]
source = Path('/opt/flashos-rhvoice')
target = Path('/opt/flashos-rootfs')
revision = 'fa9dd196fd2dac3b0bf089a2d80fc8477c2380e3'  # Official tag 1.18.4
voices = ['elena', 'irina', 'anna', 'yuriy', 'aleksandr']
if not (source / '.git').exists():
    subprocess.run(['git', 'clone', '--depth', '1', '--branch', '1.18.4',
                    'https://github.com/RHVoice/RHVoice.git', str(source)], check=True)
if subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip() != revision:
    raise SystemExit('RHVoice source revision differs from the reviewed release')
paths = [line.split(None, 1)[1] for line in subprocess.check_output(
    ['git', 'config', '-f', '.gitmodules', '--get-regexp', 'path'], cwd=source, text=True).splitlines()]
paths = [p for p in paths if p.startswith('external/') or p in
         ['data/languages/Russian', 'data/languages/English', *['data/voices/' + voice for voice in voices]]]
subprocess.run(['git', 'submodule', 'update', '--init', '--depth', '1', '--jobs', '4', '--', *paths], cwd=source, check=True)
subprocess.run(['scons', '-j4', 'prefix=/usr', 'audio_libs=libao', f'DESTDIR={target}', 'install'], cwd=source, check=True)
subprocess.run(['chroot', str(target), 'ldconfig'], check=True)
docs = target / 'usr/share/doc/flashos/rhvoice'
docs.mkdir(parents=True, exist_ok=True)
shutil.copytree(source / 'licenses', docs / 'licenses', dirs_exist_ok=True)
shutil.copyfile(source / 'doc/en/License.md', docs / 'License.md')
for voice in voices:
    for file in (source / 'data/voices' / voice).glob('*'):
        if file.is_file() and (file.name.lower().startswith(('readme', 'license', 'copying'))):
            folder = docs / voice
            folder.mkdir(exist_ok=True)
            shutil.copyfile(file, folder / file.name)
record = {'repository': 'https://github.com/RHVoice/RHVoice', 'tag': '1.18.4', 'commit': revision,
          'submodules': subprocess.check_output(['git', 'submodule', 'status', '--', *paths], cwd=source, text=True).splitlines(),
          'voices': voices, 'yuriy_license': 'CC BY-NC-ND 4.0; dataset copied without modification'}
(docs / 'sources.json').write_text(json.dumps(record, indent=2) + '\n')
(project / 'dist/rhvoice-sources.json').write_text(json.dumps(record, indent=2) + '\n')
