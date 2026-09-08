from __future__ import annotations

import pathlib
import tempfile
from urllib.parse import quote

import pandas as pd
import pyreadr
import requests

OUT = pathlib.Path("outputs/adams_westbrook_dunks")
OUT.mkdir(parents=True, exist_ok=True)
SEASONS = {2014:"2013-14", 2015:"2014-15", 2016:"2015-16", 2017:"2016-17", 2018:"2017-18", 2019:"2018-19"}


def read_rds(url: str) -> pd.DataFrame:
    r = requests.get(url, timeout=180)
    r.raise_for_status()
    with tempfile.NamedTemporaryFile(suffix=".rds") as f:
        f.write(r.content)
        f.flush()
        obj = pyreadr.read_r(f.name)
    if not obj:
        raise RuntimeError(f"No dataframe in {url}")
    return next(iter(obj.values()))


def norm_name(s: pd.Series) -> pd.Series:
    return (s.fillna("").astype(str).str.lower().str.replace(r"[^a-z ]", "", regex=True).str.strip())


def game_id_str(v) -> str:
    s = str(v).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s.zfill(10)

rows = []
qa = []
for end_year, season in SEASONS.items():
    sources = [
        ("Regular Season", f"pbp-final-{end_year}"),
        ("Playoffs", f"pbp-final-playoffs{end_year}"),
    ]
    for season_type, folder in sources:
        url = f"https://raw.githubusercontent.com/ramirobentes/nba_pbp_data/main/{folder}/data.rds"
        df = read_rds(url)
        required = {"game_date","game_id","event_num","msg_type","period","clock","description","player1_name","player2_name","team_home","team_away"}
        missing = sorted(required - set(df.columns))
        if missing:
            raise RuntimeError(f"{folder}: missing {missing}; cols={list(df.columns)}")

        p1 = norm_name(df["player1_name"])
        p2 = norm_name(df["player2_name"])
        desc = df["description"].fillna("").astype(str)
        msg = pd.to_numeric(df["msg_type"], errors="coerce")

        # Exact made FGs: player1=scorer, player2=assister. Require official PBP text to say DUNK.
        mask = (
            msg.eq(1)
            & p1.str.contains(r"\bsteven adams\b|\badams\b", regex=True)
            & p2.str.contains(r"\brussell westbrook\b|\bwestbrook\b", regex=True)
            & desc.str.contains("DUNK", case=False, na=False)
        )
        hit = df.loc[mask].copy()
        qa.append({"season":season,"season_type":season_type,"source_rows":len(df),"matches":len(hit),"source_url":url})

        for _, r in hit.iterrows():
            gid = game_id_str(r["game_id"])
            ev = int(round(float(r["event_num"])))
            description = str(r["description"])
            base = f"https://www.nba.com/stats/events?GameEventID={ev}&GameID={gid}&Season={season}&flag=1"
            rows.append({
                "season": season,
                "season_type": season_type,
                "game_date": pd.to_datetime(r["game_date"]).date().isoformat(),
                "game_id": gid,
                "event_num": ev,
                "period": int(round(float(r["period"]))),
                "clock": str(r["clock"]),
                "team_away": str(r["team_away"]),
                "team_home": str(r["team_home"]),
                "player1_name": str(r["player1_name"]),
                "player2_name": str(r["player2_name"]),
                "description": description,
                "event_video_link": base,
                "event_video_link_with_title": base + "&title=" + quote(description, safe=""),
            })

out = pd.DataFrame(rows)
if out.empty:
    raise RuntimeError("No matching events")
out = out.sort_values(["game_date","game_id","event_num"], kind="stable").reset_index(drop=True)

# QA: one row per exact NBA game/event key, and every retained row carries the requested identities/type.
if out.duplicated(["game_id","event_num"]).any():
    raise RuntimeError("Duplicate game/event keys")
assert out["player1_name"].str.contains("Adams", case=False, na=False).all()
assert out["player2_name"].str.contains("Westbrook", case=False, na=False).all()
assert out["description"].str.contains("DUNK", case=False, na=False).all()

out.to_csv(OUT / "adams_dunks_assisted_by_westbrook.csv", index=False)
pd.DataFrame(qa).to_csv(OUT / "qa_source_coverage.csv", index=False)

counts = out.groupby(["season","season_type"], sort=False).size().rename("count").reset_index()
counts.to_csv(OUT / "counts.csv", index=False)

lines = [
    "# Steven Adams dunks assisted by Russell Westbrook",
    "",
    f"Total exact events: **{len(out)}**",
    "",
    "Coverage: 2013-14 through 2018-19, regular season + playoffs.",
    "Filter: NBA PBP made FG (`msg_type=1`), scorer Steven Adams, assister Russell Westbrook, description contains `DUNK`.",
    "Event-video pages are constructed deterministically from exact `game_id` + `event_num`.",
    "",
    "## Counts",
    "",
]
for _, r in counts.iterrows():
    lines.append(f"- {r['season']} — {r['season_type']}: {int(r['count'])}")
lines += ["", "## Events", ""]
for _, r in out.iterrows():
    lines.append(f"- {r['game_date']} | {r['season']} {r['season_type']} | {r['team_away']} @ {r['team_home']} | Q{r['period']} {r['clock']} | event {r['event_num']} | [{r['description']}]({r['event_video_link']})")
(OUT / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

print(out.to_csv(index=False))
print("\nCOUNTS\n", counts.to_string(index=False))
print(f"\nTOTAL={len(out)}")
