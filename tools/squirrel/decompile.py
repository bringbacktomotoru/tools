#!/usr/bin/env python3
"""Batch decompile all .sk.bin files from extracted bundles to .sq files."""
import sys
import os
import glob
import time

from tools.squirrel import map_resources
from tools.squirrel.sk_decompiler import decompile_sk

BUNDLES = [
    ('0-0111', 'extracted/0-0111'),
    ('7-010d', 'extracted/7-010d'),
    ('1-011b', 'extracted/1-011b'),
    ('main', 'extracted/main'),
]

def main():
    force = '--force' in sys.argv or '-f' in sys.argv
    project_root = map_resources.REPO_ROOT

    total = 0
    success = 0
    failed = 0
    skipped = 0

    for bundle_name, rel_dir in BUNDLES:
        bundle_dir = os.path.join(project_root, rel_dir)
        out_dir = os.path.join(project_root, 'decompiled', 'squirrel', bundle_name)
        os.makedirs(out_dir, exist_ok=True)

        files = sorted(
            glob.glob(os.path.join(bundle_dir, '*.sk.bin')) +
            glob.glob(os.path.join(bundle_dir, '*.sk')),
            key=lambda f: (
                int(os.path.splitext(os.path.basename(f))[0].split('.')[0])
                if os.path.splitext(os.path.basename(f))[0].split('.')[0].isdigit()
                else 0
            )
        )

        print(f"\n{'='*60}")
        print(f"  Bundle: {bundle_name} ({len(files)} files)")
        print(f"  Output: {out_dir}")
        print(f"{'='*60}")

        for filepath in files:
            basename = os.path.basename(filepath)
            name_base = os.path.splitext(basename)[0]
            out_path = os.path.join(out_dir, f"{name_base}.sq")

            # Skip if already decompiled and non-empty (unless --force)
            if not force and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                skipped += 1
                continue

            total += 1
            try:
                result = decompile_sk(filepath)
                for attempt in range(3):
                    try:
                        with open(out_path, 'w', encoding='utf-8') as f:
                            f.write(result)
                        break
                    except OSError:
                        if attempt == 2:
                            raise
                        time.sleep(0.05)
                success += 1
            except Exception as e:
                failed += 1
                print(f"  FAIL: {basename} - {e}")

    print(f"\n{'='*60}")
    print(f"  DONE: {success} decompiled, {failed} failed, {skipped} skipped")
    print(f"  Total: {total + skipped}")
    print(f"{'='*60}")

    if '--no-index' not in sys.argv:
        build_tree = '--no-tree' not in sys.argv
        index, index_path, copied = map_resources.generate_all(project_root, build_tree=build_tree)
        print(f"  Resource index: {index_path} ({index['total_paths']} paths, {index['total_decompiled']} decompiled)")
        if build_tree:
            print(f"  Tree mirror:    {copied} scripts -> decompiled/tree/")

if __name__ == '__main__':
    main()
