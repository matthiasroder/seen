#!/usr/bin/env python3
"""Explicit, user-run registration. No service, listener, or browser restart."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import sys

parser = argparse.ArgumentParser(description='Connect Seen to local SQLite.')
parser.add_argument('extension_id', help='Seen ID from chrome://extensions')
args = parser.parse_args()
if not re.fullmatch('[a-p]{32}', args.extension_id):
    parser.error('Expected the 32-letter Chrome extension ID.')
root = Path(__file__).resolve().parent
launcher = root / 'launch-host'
destination = Path.home() / 'Library/Application Support/Google/Chrome/NativeMessagingHosts/com.seen.archive.json'
manifest = {'name': 'com.seen.archive', 'description': 'Seen local DOM archive', 'path': str(launcher),
            'type': 'stdio', 'allowed_origins': ['chrome-extension://' + args.extension_id + '/']}
if destination.exists() and json.loads(destination.read_text()) != manifest:
    parser.error('A different Seen helper registration exists. It was not overwritten.')
launcher.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' ' + shlex.quote(str(root / 'host.py')) + ' "$@"\n')
launcher.chmod(0o700)
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(json.dumps(manifest, indent=2) + '\n')
destination.chmod(0o600)
from host import Archive
archive = Archive()
print('Database: ' + str(archive.path))
archive.close()
print('Helper registered. Reload Seen in chrome://extensions and approve its new local-helper permission if prompted.')
