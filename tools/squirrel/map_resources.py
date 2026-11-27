#!/usr/bin/env python3
"""Map Tomotoru SXR virtual resource paths to extracted and decompiled files.

Parses main.sxr's internal pkg.json (entries tree + sxrlist) and main.sxr's own
hierarchical entity table to generate:
  - decompiled/resource_index.json  (by_path and by_entity lookups)
  - decompiled/tree/                (optional clean path-organized mirror of .sq files)
"""
import json
import shutil
import sys
from pathlib import Path

from tools.assets import sxr_extract as extract

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _decompress_entity_data(entity, sxr_data):
    """Extract, decrypt, and decompress a single entity's bytes from an SXR buffer."""
    raw = sxr_data[entity["data_offset"]:entity["data_offset"] + entity["data_size"]]
    if entity["encrypted"]:
        raw = extract.decrypt_entity(entity["name"], raw)
    comp = entity["compression"]
    if comp == 6:
        raw = extract.decompress_zlib(raw)
    elif comp == 10:
        import lz4.block
        raw = lz4.block.decompress(raw, uncompressed_size=entity["decompressed_size"])
    return raw


def _parse_main_sxr(main_sxr_path):
    """Return (pkg_dict, main_tree_entries) parsed from sxr/main.sxr."""
    with open(main_sxr_path, "rb") as f:
        sxr_data = f.read()

    if sxr_data[:4] != b"SXR ":
        raise ValueError(f"Invalid SXR header in {main_sxr_path}")

    trailer_offset = extract.read_u64_be(sxr_data, 10)
    trailer_length = extract.read_u32_be(sxr_data, 18)
    trailer_decoded = extract.decode_trailer(
        trailer_offset,
        trailer_length,
        sxr_data[trailer_offset:trailer_offset + trailer_length],
    )
    entities = extract.parse_entities(trailer_decoded, sxr_data)

    # Walk hierarchical child_count tree for main.sxr's own files
    idx = 0
    main_entries = []

    def visit(prefix):
        nonlocal idx
        ent = entities[idx]
        idx += 1
        name = ent["name"]
        vpath = f"{prefix}/{name}" if name else prefix
        if ent["data_size"] > 0:
            main_entries.append((vpath, ent))
        for _ in range(ent["child_count"]):
            if idx < len(entities):
                visit(vpath)

    while idx < len(entities):
        visit("")

    # Locate and decode internal pkg.json
    pkg = None
    for _, ent in main_entries:
        if ent["name"] == "pkg.json":
            raw = _decompress_entity_data(ent, sxr_data)
            pkg = json.loads(raw.decode("utf-8"))
            break

    if pkg is None:
        raise FileNotFoundError("pkg.json not found inside main.sxr")

    return pkg, main_entries


def _build_extracted_lookup(extracted_root):
    """Map bundle_name -> {stem_or_id: relative_posix_path} for fast lookup."""
    lookup = {}
    if not extracted_root.exists():
        return lookup
    for bundle_dir in sorted(extracted_root.iterdir()):
        if not bundle_dir.is_dir():
            continue
        bmap = {}
        for p in bundle_dir.iterdir():
            if not p.is_file():
                continue
            rel = p.relative_to(extracted_root.parent).as_posix()
            # Map exact filename, stem without .bin, and numeric prefix
            bmap[p.name] = rel
            if p.name.endswith(".sk.bin"):
                bmap[p.name[:-7]] = rel
            stem = p.stem
            if stem not in bmap:
                bmap[stem] = rel
        lookup[bundle_dir.name] = bmap
    return lookup


def _build_decompiled_lookup(decompiled_squirrel_root):
    """Map bundle_name -> {entity_key: relative_posix_path} for .sq files."""
    lookup = {}
    if not decompiled_squirrel_root.exists():
        return lookup
    for bundle_dir in sorted(decompiled_squirrel_root.iterdir()):
        if not bundle_dir.is_dir():
            continue
        bmap = {}
        for p in bundle_dir.iterdir():
            if not p.is_file() or p.suffix != ".sq":
                continue
            rel = p.relative_to(decompiled_squirrel_root.parent.parent).as_posix()
            bmap[p.name] = rel
            if p.name.endswith(".sk.sq"):
                bmap[p.name[:-6]] = rel
            bmap[p.stem] = rel
            bmap[f"{p.stem}.sk"] = rel
        lookup[bundle_dir.name] = bmap
    return lookup


def build_resource_index(repo_root=REPO_ROOT):
    """Build the resource index dictionary from the base main.sxr."""
    repo_root = Path(repo_root)
    main_sxr = repo_root / "sxr" / "main.sxr"
    if not main_sxr.is_file():
        main_sxr = repo_root / ".cache" / "sxr" / "main.sxr"
    pkg, main_entries = _parse_main_sxr(main_sxr)

    extracted_lookup = _build_extracted_lookup(repo_root / "extracted")
    decompiled_lookup = _build_decompiled_lookup(repo_root / "decompiled" / "squirrel")

    sxrlist = pkg.get("sxrlist", [])
    bundles = {}
    sxr_idx_to_bundle = {}
    for idx, meta in enumerate(sxrlist):
        bname = f"{meta['sxrname']}-0{meta['version']:x}"
        sxr_idx_to_bundle[idx] = bname
        bundles[bname] = {
            "sxr_index": idx,
            "sxrname": meta["sxrname"],
            "version": meta["version"],
            "size": meta.get("size"),
            "md5": meta.get("md5"),
            "install": meta.get("install"),
        }

    by_path = {}
    by_entity = {}

    def walk_entries(nodes, prefix=""):
        for node in nodes:
            node_type = node[0]
            if node_type == 1:
                walk_entries(node[2], f"{prefix}/{node[1]}")
            elif node_type == 0:
                vpath = f"{prefix}/{node[1]}"
                sxr_idx = node[2]
                entity_id = node[3]
                bundle = sxr_idx_to_bundle[sxr_idx]
                eid_str = str(entity_id)

                ext_path = extracted_lookup.get(bundle, {}).get(eid_str)
                dec_path = decompiled_lookup.get(bundle, {}).get(eid_str)

                entry = {
                    "bundle": bundle,
                    "entity_id": entity_id,
                    "extracted_path": ext_path,
                    "decompiled_path": dec_path,
                }
                by_path[vpath] = entry
                by_entity[f"{bundle}/{entity_id}"] = {
                    "path": vpath,
                    **entry,
                }

    walk_entries(pkg.get("entries", []))

    # Also include main.sxr's own entities (e.g. /system/consts.sk)
    for vpath, ent in main_entries:
        name = ent["name"]
        if vpath in by_path:
            continue
        ext_path = extracted_lookup.get("main", {}).get(name) or extracted_lookup.get("main", {}).get(Path(name).stem)
        dec_path = decompiled_lookup.get("main", {}).get(name) or decompiled_lookup.get("main", {}).get(Path(name).stem)
        entry = {
            "bundle": "main",
            "entity_id": name,
            "extracted_path": ext_path,
            "decompiled_path": dec_path,
        }
        by_path[vpath] = entry
        by_entity[f"main/{name}"] = {
            "path": vpath,
            **entry,
        }

    return {
        "version": pkg.get("version"),
        "total_paths": len(by_path),
        "total_decompiled": sum(1 for e in by_path.values() if e["decompiled_path"]),
        "bundles": bundles,
        "by_path": by_path,
        "by_entity": by_entity,
    }


def write_resource_index(index, out_path=None):
    """Write resource_index.json to disk."""
    if out_path is None:
        out_path = REPO_ROOT / "decompiled" / "resource_index.json"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    return out_path


def mirror_decompiled_tree(index, repo_root=REPO_ROOT, clean=True):
    """Create/refresh a path-organized mirror of all decompiled .sq files under decompiled/tree/."""
    repo_root = Path(repo_root)
    tree_root = repo_root / "decompiled" / "tree"
    if clean and tree_root.exists():
        shutil.rmtree(tree_root)
    tree_root.mkdir(parents=True, exist_ok=True)

    copied = 0
    for vpath, info in index["by_path"].items():
        dec_rel = info.get("decompiled_path")
        if not dec_rel:
            continue
        src = repo_root / dec_rel
        if not src.exists():
            continue
        rel_vpath = vpath.lstrip("/")
        dst = tree_root / f"{rel_vpath}.sq"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1

    return copied, tree_root


def generate_all(repo_root=REPO_ROOT, build_tree=True):
    """Build resource_index.json and optionally refresh decompiled/tree/."""
    repo_root = Path(repo_root)
    index = build_resource_index(repo_root)
    index_path = write_resource_index(index, repo_root / "decompiled" / "resource_index.json")
    copied = 0
    if build_tree:
        copied, _ = mirror_decompiled_tree(index, repo_root=repo_root, clean=True)
    return index, index_path, copied


def main():
    build_tree = "--no-tree" not in sys.argv
    index, index_path, copied = generate_all(REPO_ROOT, build_tree=build_tree)
    print(f"Wrote {index_path} ({index['total_paths']} paths, {index['total_decompiled']} decompiled)")
    if build_tree:
        print(f"Mirrored {copied} decompiled scripts into {REPO_ROOT / 'decompiled' / 'tree'}")


if __name__ == "__main__":
    main()
