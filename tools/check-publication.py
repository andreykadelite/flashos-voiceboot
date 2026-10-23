#!/usr/bin/env python3
"""Check public source boundaries, including pending additions, without writes."""
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote
import ast
from public_payload import public_inventory, safe_path

ROOT = Path(__file__).resolve().parents[1]


def main():
    tracked = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
                                      cwd=ROOT).decode('utf-8').split('\0')
    tracked = sorted({name for name in tracked if name})
    if not tracked:
        raise RuntimeError('No public source files found in the checkout')
    errors = []
    inventoried = public_inventory(ROOT)
    def string_constant(filename, constant):
        tree = ast.parse((ROOT / filename).read_text(encoding='utf-8-sig'))
        for statement in tree.body:
            if isinstance(statement, ast.Assign) and any(isinstance(target, ast.Name) and target.id == constant for target in statement.targets):
                return ast.literal_eval(statement.value)
        raise ValueError('Missing version constant: ' + constant)
    if string_constant('src/help_content.py', 'VERSION') != string_constant('installer/model.py', 'SYSTEM_VERSION'):
        errors.append('Installer and boot-system version constants disagree')
    for name in tracked:
        path = safe_path(ROOT, ROOT / name)
        if name.startswith(('boot/', 'docs/', 'metadata/', 'licenses/')) and name not in inventoried:
            errors.append('File absent from reviewed public inventory: ' + name)
        if path.suffix.lower() in ('.exe', '.iso', '.zip', '.squashfs', '.img', '.vhdx', '.log'):
            errors.append('Generated/private file tracked: ' + name)
        if path.stat().st_size > 5 * 1024**2:
            errors.append('Large file in Git: ' + name)
        if name.startswith(('licenses/', 'metadata/')):
            continue
        if path.suffix.lower() not in ('.py', '.ps1', '.md', '.sh', '.json', '.yml', '.cfg', '.txt'):
            continue
        value = path.read_text(encoding='utf-8-sig')
        if str(ROOT).replace('\\', '/') in value or str(ROOT) in value:
            errors.append('Absolute checkout path: ' + name)
        if re.search(r'gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----', value):
            errors.append('Credential-like material: ' + name)
        if path.suffix.lower() == '.md':
            for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', value):
                if target.startswith(('https://', 'http://', 'mailto:', '#')):
                    continue
                relative = unquote(target.split('#', 1)[0])
                if relative and not (path.parent / relative).exists():
                    errors.append('Broken local link: ' + name + ' -> ' + target)
    if errors:
        raise SystemExit('\n'.join(errors))
    print(f'Checked {len(tracked)} source files: source boundaries, credentials and local documentation links passed.')


if __name__ == '__main__':
    main()
