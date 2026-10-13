"""Collect the live numbers the profile art shows and write data/stats.json.

Sources: GitHub GraphQL (contribution calendar, PRs, repos, languages, releases),
the npm registry (versions, downloads), repo files (package versions) and a
plain HTTP check of every live product URL.

Each source is fetched independently; if one fails, its previous value in
stats.json is kept, so a flaky API never blanks the profile.

Token: PROFILE_TOKEN or GITHUB_TOKEN env var, else `gh auth token` for local runs.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATS = ROOT / "data" / "stats.json"
CONTENT = tomllib.loads((ROOT / "content.toml").read_text(encoding="utf-8"))
LOGIN = CONTENT["person"]["github"]
UA = "abhay-yemekar-profile-art (+https://github.com/abhay-yemekar/abhay-yemekar)"


def token() -> str:
    for key in ("PROFILE_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(key):
            return os.environ[key]
    try:
        return subprocess.run(
            ["gh", "auth", "token", "--user", LOGIN], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sys.exit("No GitHub token: set GITHUB_TOKEN or log in with `gh auth login`.")


TOKEN = token()


def http(url: str, *, data: bytes | None = None, headers: dict | None = None, timeout: int = 30):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def gql(query: str, **variables) -> dict:
    body = json.dumps({"query": query, "variables": variables}).encode()
    _, raw = http(
        "https://api.github.com/graphql",
        data=body,
        headers={"Authorization": f"bearer {TOKEN}", "Content-Type": "application/json"},
    )
    out = json.loads(raw)
    if out.get("errors"):
        raise RuntimeError(out["errors"])
    return out["data"]


def rest_raw(path: str) -> bytes:
    _, raw = http(
        f"https://api.github.com/{path}",
        headers={"Authorization": f"bearer {TOKEN}", "Accept": "application/vnd.github.raw"},
    )
    return raw


# --------------------------------------------------------------------------- sources


def fetch_activity() -> dict:
    today = dt.date.today()
    year_ago = today - dt.timedelta(days=365)
    data = gql(
        """
        query($login: String!, $merged: String!) {
          user(login: $login) {
            contributionsCollection {
              contributionCalendar { totalContributions weeks { contributionDays { date contributionCount } } }
            }
            pullRequests(states: MERGED) { totalCount }
          }
          mergedYear: search(query: $merged, type: ISSUE) { issueCount }
        }
        """,
        login=LOGIN,
        merged=f"author:{LOGIN} is:pr is:merged merged:>={year_ago.isoformat()}",
    )
    user = data["user"]
    cal = user["contributionsCollection"]["contributionCalendar"]
    days = [
        {"date": d["date"], "count": d["contributionCount"]}
        for w in cal["weeks"]
        for d in w["contributionDays"]
    ]
    counts = [d["count"] for d in days]

    longest = run = 0
    for c in counts:
        run = run + 1 if c else 0
        longest = max(longest, run)
    # Today's UTC day may still be empty; don't let that break a live streak.
    tail = counts[:-1] if counts and counts[-1] == 0 else counts
    current = 0
    for c in reversed(tail):
        if not c:
            break
        current += 1

    return {
        "calendar": days,
        "contributions": cal["totalContributions"],
        "active_days": sum(1 for c in counts if c),
        "longest_streak": longest,
        "current_streak": current,
        "merged_prs_year": data["mergedYear"]["issueCount"],
        "merged_prs_all": user["pullRequests"]["totalCount"],
    }


def fetch_repos() -> dict:
    data = gql(
        """
        query($login: String!) {
          user(login: $login) {
            repositories(first: 100, ownerAffiliations: OWNER, privacy: PUBLIC, isFork: false) {
              nodes {
                name stargazerCount
                latestRelease { tagName publishedAt }
                releases { totalCount }
                languages(first: 12, orderBy: {field: SIZE, direction: DESC}) { edges { size node { name } } }
              }
            }
          }
        }
        """,
        login=LOGIN,
    )
    repos = data["user"]["repositories"]["nodes"]
    langs: dict[str, int] = {}
    for r in repos:
        for e in r["languages"]["edges"]:
            langs[e["node"]["name"]] = langs.get(e["node"]["name"], 0) + e["size"]
    return {
        "public_repos": len(repos),
        "stars": sum(r["stargazerCount"] for r in repos),
        "releases": sum(r["releases"]["totalCount"] for r in repos),
        "languages": sorted(
            ({"name": k, "bytes": v} for k, v in langs.items()), key=lambda x: -x["bytes"]
        ),
        "repos": {
            r["name"]: {
                "release": (r["latestRelease"] or {}).get("tagName"),
                "stars": r["stargazerCount"],
            }
            for r in repos
        },
    }


def fetch_npm() -> dict:
    out = {}
    for project in CONTENT["project"]:
        for fact in project.get("facts", []):
            start = 0
            while (i := fact.find("{npm:", start)) != -1:
                pkg = fact[i + 5 : fact.index("}", i)]
                _, raw = http(f"https://api.npmjs.org/downloads/point/last-month/{pkg}")
                out[pkg] = json.loads(raw)["downloads"]
                start = i + 1
    return out


def check_live() -> dict:
    out = {}
    for project in CONTENT["project"]:
        url = project.get("live")
        if not url:
            continue
        # Free-tier hosts (Render) sleep when idle and take ~40 s to wake, so allow a
        # long first attempt and retry once before calling a product "not verified".
        for attempt in range(2):
            t0 = time.monotonic()
            try:
                status, _ = http(url, timeout=75)
            except urllib.error.HTTPError as e:
                status = e.code
            except Exception:  # noqa: BLE001 - any network failure counts as "not verified"
                status = 0
            if 200 <= status < 400:
                break
        out[project["slug"]] = {
            "ok": 200 <= status < 400,
            "status": status,
            "ms": round((time.monotonic() - t0) * 1000),
        }
    return out


# --------------------------------------------------------------------------- main


def main() -> None:
    prev = json.loads(STATS.read_text(encoding="utf-8")) if STATS.exists() else {}
    stats = dict(prev)
    failures = []
    for key, fn in (
        ("activity", fetch_activity),
        ("repos", fetch_repos),
        ("npm", fetch_npm),
        ("live", check_live),
    ):
        try:
            stats[key] = fn()
        except Exception as exc:  # noqa: BLE001 - keep the last good value
            failures.append(f"{key}: {exc}")
            print(f"warn: {key} failed, keeping previous value ({exc})", file=sys.stderr)

    stats["generated_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    STATS.parent.mkdir(exist_ok=True)
    STATS.write_text(json.dumps(stats, indent=1) + "\n", encoding="utf-8")
    a = stats.get("activity", {})
    print(
        f"contributions={a.get('contributions')} merged_prs_year={a.get('merged_prs_year')} "
        f"streak={a.get('current_streak')}/{a.get('longest_streak')} "
        f"live={ {k: v['ok'] for k, v in stats.get('live', {}).items()} } npm={stats.get('npm')}"
    )
    if len(failures) == 4:
        sys.exit("every source failed")


if __name__ == "__main__":
    main()
