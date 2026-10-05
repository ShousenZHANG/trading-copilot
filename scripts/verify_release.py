"""Check a distribution in a clean temporary install without personal state."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def verify(archive):
    from copilot.service import KEY_NAMES
    from package_release import _audit_zip
    # Use the same archive leak/required-artifact audit before extracting.
    problems=_audit_zip(archive)
    if problems:
        raise ValueError('; '.join(problems))
    with tempfile.TemporaryDirectory(prefix='copilot-clean-release-') as directory:
        root=Path(directory).resolve()
        with zipfile.ZipFile(archive) as bundle:
            for entry in bundle.namelist():
                if not (root/entry).resolve().is_relative_to(root):
                    raise ValueError('archive path escapes clean install')
            bundle.extractall(root)
        install=root/'trading-copilot'
        private=root/'isolated'
        private.mkdir()
        environment={key:value for key,value in os.environ.items()
                     if key not in {*KEY_NAMES,'COPILOT_DB_PATH','COPILOT_CONFIG_PATH','PYTHONPATH'}}
        prefix=[sys.executable,str(install/'scripts/copilot_cli.py'),'--db',str(private/'journal.sqlite'),
                '--config-path',str(private/'missing.toml')]
        for command in ('capabilities','doctor','config'):
            completed=subprocess.run([*prefix,command],cwd=install,env=environment,
                capture_output=True,text=True,encoding='utf-8',timeout=45,check=True)
            payload=json.loads(completed.stdout)
            if command=='capabilities' and (payload['broker']['enabled'] or payload['broker']['order_submission']):
                raise ValueError('clean release unexpectedly enables broker actions')
            if command=='doctor' and payload.get('order_authorization') is not False:
                raise ValueError('diagnosis must not authorize orders')
        if (private/'journal.sqlite').exists():
            raise ValueError('read-only clean-install probes created a database')
        for relative in ('.env','config/user.toml','data/state/copilot.sqlite'):
            if (install/relative).exists():
                raise ValueError('private state exists in clean install')
    return dict(status='pass',clean_commands=['capabilities','doctor','config'],
                sha256=hashlib.sha256(Path(archive).read_bytes()).hexdigest(),private_state_created=False)


def main():
    from package_release import ROOT, plugin_version
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive',nargs='?',type=Path,default=ROOT/'dist'/f'trading-copilot-{plugin_version()}.zip')
    args=parser.parse_args()
    print(json.dumps(verify(args.archive),ensure_ascii=False))
    return 0


if __name__=='__main__': raise SystemExit(main())
