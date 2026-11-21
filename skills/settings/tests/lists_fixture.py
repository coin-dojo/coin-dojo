"""Fixture builder for the GAME LISTS tests and the VM proof (#281): a cabinet's worth of scraped gamelists, ROM files
and media, deterministic, no real game data (the titles are made-up words).

    build(root, games=1100)  -> dict of a lists_rebuild config pointing into ROOT (a throwaway tree)

ROOT/ES-DE/gamelists/<system>/gamelist.xml (scraped: genre, release date, rating, desc, image), ROOT/roms/<system>/<file>,
ROOT/media/<system>/covers/<stem>.png, ROOT/tools -> the real skills/collections tools, ROOT/data/coin-dojo for the output.
"""
import json
import os
import random
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
COLL = os.path.join(REPO, "skills", "collections")
SYSTEMS = ("cps1", "cps2", "cps3", "fbneo", "neogeo", "genesis", "segacd", "snes", "nes", "pcengine", "psx", "saturn")
GENRES = ("Fighting", "Shoot'em Up", "Platform", "Puzzle", "Racing", "Sports", "Beat'em Up", "Run and Gun", "Maze")
WORDS = ("Alpha", "Brave", "Cobalt", "Dragon", "Echo", "Falcon", "Giant", "Hyper", "Iron", "Jade", "Karma", "Lunar", "Mega",
         "Nova", "Omega", "Pixel", "Quest", "Rapid", "Solar", "Turbo", "Ultra", "Vector", "Wild", "Xeno", "Yonder", "Zen")
TOOLS = ("collgen.py", "nested.py", "validate_nested.py", "metadata.py", "folder_art.py", "mame_hidden_names.tsv",
         "folder_text.tsv", "series.tsv", "duplicates.tsv", "twin_stick.tsv", "system_names.tsv")


def game_name(rng, i):
    return "%s %s %d" % (rng.choice(WORDS), rng.choice(WORDS), i)


def write_gamelist(path, games, scraped=True):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out = ['<?xml version="1.0"?>', "<gameList>"]
    for g in games:
        out += ["\t<game>", "\t\t<path>./%s</path>" % g["file"], "\t\t<name>%s</name>" % g["name"]]
        if scraped:
            out += ["\t\t<desc>%s.</desc>" % g["name"], "\t\t<rating>%s</rating>" % g["rating"],
                    "\t\t<releasedate>%d0101T000000</releasedate>" % g["year"], "\t\t<genre>%s</genre>" % g["genre"],
                    "\t\t<image>./media/covers/%s.png</image>" % g["file"].rsplit(".", 1)[0]]
        out.append("\t</game>")
    out.append("</gameList>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def make_games(system, n, rng):
    games = []
    for i in range(n):
        games.append(dict(name=game_name(rng, i), file="%s%04d.zip" % (system, i), year=rng.randint(1986, 2004),
                          genre=rng.choice(GENRES), rating=rng.choice(("0.4", "0.6", "0.8", "0.9", "1", "0.7", "0.5"))))
    return games


def build(root, games=1100, systems=SYSTEMS, scraped=True, seed=281):
    rng = random.Random(seed)
    per = max(1, games // len(systems))
    gl = os.path.join(root, "ES-DE", "gamelists")
    for s in systems:
        gs = make_games(s, per, rng)
        write_gamelist(os.path.join(gl, s, "gamelist.xml"), gs, scraped)
        for g in gs:
            rp = os.path.join(root, "roms", s, g["file"])
            os.makedirs(os.path.dirname(rp), exist_ok=True)
            open(rp, "wb").close()
            mp = os.path.join(root, "media", s, "covers", g["file"].rsplit(".", 1)[0] + ".png")
            os.makedirs(os.path.dirname(mp), exist_ok=True)
            open(mp, "wb").close()
    tools = os.path.join(root, "data", "coin-dojo")
    os.makedirs(tools, exist_ok=True)
    for t in TOOLS:
        shutil.copy2(os.path.join(COLL, t), os.path.join(tools, t))
    with open(os.path.join(tools, "virtual_systems.json"), "w") as f:
        json.dump(["must-play", "must-play-genres", "genres", "series", "decades", "systems", "fightcade", "trophies", "settings", "quit"], f)
    os.makedirs(os.path.join(root, "ES-DE", "custom_systems"), exist_ok=True)
    return dict(edition="community", tools=tools, gamelists=gl, custom_systems=os.path.join(root, "ES-DE", "custom_systems"),
                roms=os.path.join(root, "roms"), media=os.path.join(root, "media"),
                nested_out=os.path.join(tools, "nested_out"), nested_root=os.path.join(root, "nested"),
                launcher="python3 launch.py", picks=os.path.join(tools, "none.html"),
                picks_example=os.path.join(REPO, "skills", "settings", "picks_example.html"), dat=os.path.join(tools, "none.dat"),
                folder_art=os.path.join(tools, "none"), state=os.path.join(root, "state", "settings-state.json"),
                backup_root=os.path.join(tools, "lists-backup"), run_dir="", game_procs=["retroarch", "flycast"],
                game_patterns=[], nested_args=["--min-decade", "1"])
