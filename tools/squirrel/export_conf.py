#!/usr/bin/env python3
"""Export structured game metadata from Tomotoru .conf scripts.

Locates key .conf scripts via decompiled/resource_index.json, parses their
.sk.bin token streams directly, and writes a structured summary.
"""
import argparse
import json
from pathlib import Path

from tools.squirrel.map_resources import REPO_ROOT, generate_all
from tools.squirrel.sk_parser import parse_func_data, parse_header
from tools.squirrel.token_table import TOKENS

CONF_LOOKUP = {
    "rooms": ["/project/conf/rooms.conf"],
    "room_slots": ["/project/conf/room_slots.conf"],
    "costumes": ["/project/conf/costumes.conf"],
    "sanrio_charas": ["/project/conf/sanrio_charas.conf"],
    "furnitures": ["/project/conf/furnitures.conf"],
    "shops": ["/project/conf/shop.conf", "/project/conf/shops.conf"],
    "gachas": ["/project/conf/gacha.conf", "/project/conf/gachas.conf"],
    "tutorials": ["/project/conf/tutorials.conf"],
    "panel_missions": ["/project/conf/panel_missions.conf"],
    "game_rule": ["/project/conf/game_rule.conf"],
}


class ConfTokenParser:
    """Minimal recursive-descent parser for Squirrel .conf data definitions."""

    def __init__(self, tokens):
        # Strip NL tokens; keep semantic tokens as (kind, val)
        self.toks = []
        for t in tokens:
            k = t[0]
            if k == "NL":
                continue
            if k in ("ID", "STR"):
                self.toks.append((k, t[2]))
            elif k in ("INT", "FLT"):
                self.toks.append((k, t[1]))
            elif k == "OP":
                self.toks.append(("OP", TOKENS.get(t[1], f"?{t[1]:02x}")))
        self.pos = 0
        self.n = len(self.toks)

    def peek(self, offset=0):
        idx = self.pos + offset
        if idx < self.n:
            return self.toks[idx]
        return (None, None)

    def next(self):
        tok = self.peek()
        self.pos += 1
        return tok

    def match_op(self, text):
        k, v = self.peek()
        if k == "OP" and v == text:
            self.pos += 1
            return True
        return False

    def parse_value(self):
        k, v = self.peek()
        if k is None:
            return None

        # Unary +/-
        if k == "OP" and v in ("-", "+"):
            self.pos += 1
            sub = self.parse_value()
            if v == "-" and isinstance(sub, (int, float)):
                return -sub
            return sub

        if k == "INT":
            self.pos += 1
            return int(v)
        if k == "FLT":
            self.pos += 1
            return round(float(v), 6)
        if k == "STR":
            self.pos += 1
            return v
        if k == "OP" and v == "true":
            self.pos += 1
            return True
        if k == "OP" and v == "false":
            self.pos += 1
            return False
        if k == "OP" and v == "null":
            self.pos += 1
            return None
        if k == "OP" and v == "[":
            return self.parse_array()
        if k == "OP" and v == "{":
            return self.parse_table()

        # Identifier / dotted constant / constructor call like ::TutorialSetting({...})
        if k == "ID" or (k == "OP" and v == "::"):
            parts = []
            while self.pos < self.n:
                ck, cv = self.peek()
                if ck == "OP" and cv in ("::", "."):
                    parts.append(cv)
                    self.pos += 1
                elif ck == "ID":
                    parts.append(cv)
                    self.pos += 1
                else:
                    break
            name = "".join(parts)
            # Constructor/function call: Foo({...})
            if self.match_op("("):
                args = []
                while self.pos < self.n and not self.match_op(")"):
                    args.append(self.parse_value())
                    self.match_op(",")
                if len(args) == 1:
                    return args[0]
                return args
            return name

        # Unknown token fallback: advance to avoid infinite loop
        self.pos += 1
        return v

    def parse_array(self):
        self.match_op("[")
        items = []
        while self.pos < self.n:
            if self.match_op("]"):
                break
            items.append(self.parse_value())
            self.match_op(",")
            self.match_op(";")
        return items

    def parse_table(self):
        self.match_op("{")
        obj = {}
        while self.pos < self.n:
            if self.match_op("}"):
                break
            if self.match_op(";") or self.match_op(","):
                continue
            k, v = self.peek()
            if k in ("ID", "STR"):
                key = v
                self.pos += 1
            elif k == "OP" and v == "[":
                self.pos += 1
                key = self.parse_value()
                self.match_op("]")
            else:
                self.pos += 1
                continue
            if self.match_op("=") or self.match_op(":") or self.match_op("<-"):
                val = self.parse_value()
                obj[str(key)] = val
            self.match_op(";")
            self.match_op(",")
        return obj

    def parse_assignments(self):
        """Parse top-level `table[id] <- Ctor({...})` and `name = Ctor({...})`."""
        indexed = {}
        scalars = {}
        while self.pos < self.n:
            k, v = self.peek()
            if k == "OP" and v == ";":
                self.pos += 1
                continue
            if k == "OP" and v == "::":
                self.pos += 1
                continue
            if k == "ID":
                lhs = v
                self.pos += 1
                if self.match_op("["):
                    key = self.parse_value()
                    self.match_op("]")
                    if self.match_op("<-") or self.match_op("="):
                        val = self.parse_value()
                        indexed.setdefault(lhs, {})[key] = val
                elif self.match_op("=") or self.match_op("<-"):
                    val = self.parse_value()
                    scalars[lhs] = val
                continue
            self.pos += 1
        return indexed, scalars


def _parse_conf_sk(sk_path):
    with open(sk_path, "rb") as f:
        data = f.read()
    _, func_data, strings = parse_header(data)
    root = parse_func_data(func_data, strings)
    return ConfTokenParser(root.body_tokens).parse_assignments()


def _resolve_conf_path(index, candidates, repo_root):
    by_path = index.get("by_path", {})
    for vpath in candidates:
        info = by_path.get(vpath)
        if info and info.get("extracted_path"):
            p = repo_root / info["extracted_path"]
            if p.exists():
                return vpath, p
    raise FileNotFoundError(f"Could not resolve any of {candidates} in resource_index.json")


def _pick(d, keys):
    if not isinstance(d, dict):
        return {}
    return {k: d[k] for k in keys if k in d}


def build_game_metadata(repo_root=REPO_ROOT):
    repo_root = Path(repo_root)
    index_path = repo_root / "decompiled" / "resource_index.json"
    if not index_path.exists():
        generate_all(repo_root, build_tree=False)

    with open(index_path, "r", encoding="utf-8") as f:
        index = json.load(f)

    sources = {}
    parsed = {}
    for key, candidates in CONF_LOOKUP.items():
        vpath, sk_path = _resolve_conf_path(index, candidates, repo_root)
        sources[key] = {
            "virtual_path": vpath,
            "extracted_path": sk_path.relative_to(repo_root).as_posix(),
        }
        parsed[key] = _parse_conf_sk(sk_path)

    # 1. game_rule
    _, gr_scalars = parsed["game_rule"]
    game_rule = gr_scalars.get("game_rule", {})

    # 2. rooms
    rooms_raw = parsed["rooms"][0].get("room_settings", {})
    rooms = {}
    for rid in sorted(rooms_raw):
        r = rooms_raw[rid]
        rooms[str(rid)] = _pick(r, [
            "name", "series", "bg_image_path", "chara_ids",
            "init_furnitures", "first_diary_id", "release_chara_conditions",
        ])

    # 3. room_slots
    slots_raw = parsed["room_slots"][0].get("room_slot_settings", {})
    room_slots = {}
    for sid in sorted(slots_raw):
        room_slots[str(sid)] = _pick(slots_raw[sid], ["condition_ids", "unlock_text"])

    # 4. sanrio_charas
    charas_raw = parsed["sanrio_charas"][0].get("sanrio_chara_settings", {})
    sanrio_charas = {}
    for cid in sorted(charas_raw):
        sanrio_charas[str(cid)] = _pick(charas_raw[cid], [
            "name", "nick_name", "name_key", "def_costume_id",
            "first_event_id", "voice_channel", "permission",
        ])

    # 5. costumes
    costumes_raw = parsed["costumes"][0].get("costume_settings", {})
    costumes = {}
    for cid in sorted(costumes_raw):
        costumes[str(cid)] = _pick(costumes_raw[cid], [
            "name", "chara_id", "grade", "permission", "actions", "priority",
        ])

    # 6. furnitures
    furn_raw = parsed["furnitures"][0].get("furniture_settings", {})
    furnitures = {}
    for fid in sorted(furn_raw):
        f_obj = furn_raw[fid]
        entry = _pick(f_obj, [
            "grade", "series", "type", "place", "sell_enable", "sell_price",
            "size_w", "size_h", "size_d", "base_tap_gm", "leveltable_id", "production_id",
        ])
        names = f_obj.get("name_list")
        if isinstance(names, list) and names:
            entry["name"] = names[0]
        furnitures[str(fid)] = entry

    # 7. shops
    shops_raw = parsed["shops"][0].get("shop_settings", {})
    shops = {}
    for sid in sorted(shops_raw):
        shops[str(sid)] = _pick(shops_raw[sid], [
            "type", "item_id", "price", "room_id", "priority", "mark", "mark_conditions",
        ])

    # 8. gachas
    gachas_raw = parsed["gachas"][0].get("gacha_settings", {})
    gachas = {}
    for gid in sorted(gachas_raw):
        gachas[str(gid)] = _pick(gachas_raw[gid], [
            "name", "gacha_version", "items_id", "type", "price",
            "step_price", "limit", "enable_visible", "priority",
        ])

    # 9. tutorials
    tuts_raw = parsed["tutorials"][0].get("tutorial_settings", {})
    tutorials = {}
    for tid in sorted(tuts_raw):
        tutorials[str(tid)] = _pick(tuts_raw[tid], [
            "type", "clazz", "title", "guide_func", "conditions", "time",
        ])

    # 10. panel_missions
    pm_raw = parsed["panel_missions"][0].get("panel_mission_settings", {})
    panel_missions = {}
    for pid in sorted(pm_raw):
        panel_missions[str(pid)] = _pick(pm_raw[pid], [
            "room_id", "diary_id", "priority", "mission_id_list", "conditions",
        ])

    # Useful cross-reference maps
    costume_to_chara = {
        str(cid): info["chara_id"]
        for cid, info in costumes.items()
        if "chara_id" in info
    }

    summary = {
        "counts": {
            "rooms": len(rooms),
            "room_slots": len(room_slots),
            "sanrio_charas": len(sanrio_charas),
            "costumes": len(costumes),
            "furnitures": len(furnitures),
            "shops": len(shops),
            "gachas": len(gachas),
            "tutorials": len(tutorials),
            "panel_missions": len(panel_missions),
        },
        "room_ids": sorted(int(k) for k in rooms),
        "room_slot_ids": sorted(int(k) for k in room_slots),
        "chara_ids": sorted(int(k) for k in sanrio_charas),
        "costume_ids": sorted(int(k) for k in costumes),
        "furniture_ids": sorted(int(k) for k in furnitures),
        "shop_ids": sorted(int(k) for k in shops),
        "gacha_ids": sorted(int(k) for k in gachas),
        "tutorial_ids": sorted(int(k) for k in tutorials),
        "panel_mission_ids": sorted(int(k) for k in panel_missions),
    }

    return {
        "pkg_version": index.get("version"),
        "sources": sources,
        "summary": summary,
        "game_rule": game_rule,
        "rooms": rooms,
        "room_slots": room_slots,
        "sanrio_charas": sanrio_charas,
        "costumes": costumes,
        "costume_to_chara": costume_to_chara,
        "furnitures": furnitures,
        "shops": shops,
        "gachas": gachas,
        "tutorials": tutorials,
        "panel_missions": panel_missions,
    }


def export_game_metadata(repo_root=REPO_ROOT, out_path=None):
    repo_root = Path(repo_root)
    if out_path is None:
        out_path = repo_root / "decompiled" / "game_metadata.json"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    metadata = build_game_metadata(repo_root)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    return metadata, out_path


def main():
    parser = argparse.ArgumentParser(description="Export structured game metadata from extracted assets")
    parser.add_argument("-o", "--output", type=Path, help="Output JSON (default: decompiled/game_metadata.json)")
    args = parser.parse_args()
    metadata, out_path = export_game_metadata(out_path=args.output)
    print(f"Exported {out_path}")
    for k, v in metadata["summary"]["counts"].items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
