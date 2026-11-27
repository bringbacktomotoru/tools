#!/usr/bin/env python3
"""Extract all readable strings from SakanaGL .sk.bin and .sk files."""
import os
import sys
import json
from pathlib import Path


def extract_strings(data, min_len=3):
    """Extract null-terminated ASCII strings from binary data."""
    strings = []
    i = 0
    while i < len(data):
        if 0x20 <= data[i] < 0x7f:
            s = b''
            j = i
            while j < len(data) and data[j] != 0 and 0x20 <= data[j] < 0x7f:
                s += bytes([data[j]])
                j += 1
            if len(s) >= min_len:
                strings.append((i, s.decode('ascii')))
            i = j
        else:
            i += 1
    return strings


def extract_all(sxr_output_dir, output_file=None):
    """Extract strings from all .sk and .sk.bin files."""
    all_strings = {}
    file_count = 0

    for root, dirs, files in os.walk(sxr_output_dir):
        for fn in sorted(files):
            if not (fn.endswith('.sk') or fn.endswith('.sk.bin')):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, sxr_output_dir)
            try:
                with open(path, 'rb') as f:
                    data = f.read()
                if data[:2] != b'\xfa\xfa':
                    continue
                strings = extract_strings(data)
                if strings:
                    all_strings[rel] = [(pos, s) for pos, s in strings]
                    file_count += 1
            except Exception as e:
                print(f"Error reading {rel}: {e}", file=sys.stderr)

    if output_file:
        with open(output_file, 'w') as f:
            json.dump(all_strings, f, indent=2)
        print(f"Extracted strings from {file_count} files -> {output_file}")
    else:
        for fn in sorted(all_strings.keys()):
            print(f"\n=== {fn} ===")
            for pos, s in all_strings[fn]:
                print(f"  {s}")

    return all_strings


def search_network_strings(all_strings):
    """Find all network-related strings."""
    keywords = [
        'http', 'url', 'server', 'socket', 'connect', 'session', 'token',
        'entrance', '/main', '/app_kitty', '/report', '/setting', '/conf/',
        'download', 'blowfish', 'encrypt', 'json', 'post', 'get',
        'Content-Type', 'GET ', 'POST ', 'cmd', 'CMD', 'response',
        'error', 'maintenance', 'version', 'setting', 'conf',
        'chara', 'furniture', 'room', 'costume', 'gacha', 'mission',
        'send', 'recv', 'request', 'api', 'succeed', 'fail',
    ]

    results = {}
    for fn, strings in all_strings.items():
        matches = []
        for pos, s in strings:
            sl = s.lower()
            for kw in keywords:
                if kw.lower() in sl:
                    matches.append((pos, s))
                    break
        if matches:
            results[fn] = matches

    return results


if __name__ == '__main__':
    project_root = Path(__file__).resolve().parent.parent.parent
    sxr_dir = project_root / 'extracted'
    output = project_root / 'extracted_strings.json'

    all_strings = extract_all(sxr_dir, output)

    print("\n\n=== NETWORK-RELATED STRINGS ===\n")
    net = search_network_strings(all_strings)
    for fn in sorted(net.keys()):
        print(f"\n--- {fn} ---")
        for pos, s in net[fn]:
            print(f"  {s}")
