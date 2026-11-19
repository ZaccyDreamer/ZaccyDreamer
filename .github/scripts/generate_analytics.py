#!/usr/bin/env python3
"""Generate public GitHub analytics cards for ZaccyDreamer."""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape


USERNAME = "ZaccyDreamer"
API_URL = "https://api.github.com/graphql"
OUTPUT = Path("assets/github-analytics")
NAVY = "#0B1220"
PANEL = "#101B2C"
PANEL_ALT = "#132238"
BORDER = "#253B55"
TEXT = "#D8E7F5"
MUTED = "#91A6BD"
CYAN = "#00F5FF"
BLUE = "#38BDF8"
PURPLE = "#9B8CFF"
OTHER = "#53657A"
LANGUAGE_PALETTE = (CYAN, BLUE, PURPLE, "#5B8CFF", "#76DCEB", "#8575FF")


def graphql(token: str, query: str, variables: dict) -> dict:
    body = json.dumps({"query": query, "variables": variables}).encode()
    request = Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "ZaccyDreamer-GitHub-Analytics",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except (HTTPError, URLError, TimeoutError) as error:
        raise RuntimeError(f"GitHub GraphQL request failed: {error}") from error
    if payload.get("errors"):
        details = "; ".join(item.get("message", "GraphQL error") for item in payload["errors"])
        raise RuntimeError(f"GitHub GraphQL returned errors: {details}")
    return payload["data"]


SUMMARY_QUERY = """
query Summary($login: String!, $from: DateTime!, $to: DateTime!, $prQuery: String!, $issueQuery: String!) {
  user(login: $login) {
    createdAt
    followers { totalCount }
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
      }
    }
    repositoriesContributedTo(first: 1, privacy: PUBLIC, includeUserRepositories: true) { totalCount }
  }
  prs: search(query: $prQuery, type: ISSUE, first: 1) { issueCount }
  issues: search(query: $issueQuery, type: ISSUE, first: 1) { issueCount }
}
"""

REPOSITORIES_QUERY = """
query Repositories($login: String!, $after: String) {
  user(login: $login) {
    repositories(first: 100, after: $after, ownerAffiliations: OWNER, privacy: PUBLIC, isFork: false) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        stargazerCount
        languages(first: 100) { edges { size node { name color } } }
      }
    }
  }
}
"""

HISTORY_QUERY = """
query History($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalPullRequestReviewContributions
      contributionCalendar { weeks { contributionDays { date contributionCount } } }
    }
  }
}
"""


def iso_start(day: date) -> str:
    return datetime.combine(day, time.min, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def iso_end(day: date) -> str:
    return datetime.combine(day, time.max, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def calendar_days(calendar: dict, start: date, end: date) -> dict[date, int]:
    result = {}
    for week in calendar.get("weeks", []):
        for item in week.get("contributionDays", []):
            day = date.fromisoformat(item["date"])
            if start <= day <= end:
                result[day] = item["contributionCount"]
    return result


def fetch_history(token: str, start: date, end: date) -> tuple[dict[date, int], int]:
    days: dict[date, int] = {}
    reviews = 0
    cursor = start
    # GitHub limits a contributionsCollection date range to at most one year.
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=364), end)
        data = graphql(
            token,
            HISTORY_QUERY,
            {"login": USERNAME, "from": iso_start(cursor), "to": iso_end(chunk_end)},
        )["user"]["contributionsCollection"]
        days.update(calendar_days(data["contributionCalendar"], cursor, chunk_end))
        reviews += data["totalPullRequestReviewContributions"]
        cursor = chunk_end + timedelta(days=1)
    return days, reviews


def streaks(days: dict[date, int], today: date) -> tuple[int, date | None, date | None, int, date | None, date | None]:
    active = sorted(day for day, count in days.items() if count > 0)
    longest = 0
    run = 0
    previous = None
    longest_start = longest_end = None
    run_start = None
    for day in active:
        if previous and day == previous + timedelta(days=1):
            run += 1
        else:
            run = 1
            run_start = day
        if run > longest:
            longest = run
            longest_start, longest_end = run_start, day
        previous = day

    current_end = today if days.get(today, 0) > 0 else today - timedelta(days=1)
    current = 0
    day = current_end
    while days.get(day, 0) > 0:
        current += 1
        day -= timedelta(days=1)
    current_start = current_end - timedelta(days=current - 1) if current else None
    return current, current_start, current_end if current else None, longest, longest_start, longest_end


def display_date(day: date | None) -> str:
    return f"{day.strftime('%b')} {day.day}, {day.year}" if day else "No active streak"


def grade(stars: int, commits: int, prs: int, issues: int, reviews: int, followers: int) -> tuple[str, float, float]:
    def exponential_cdf(value: float) -> float:
        return 1 - 2 ** (-value)

    def log_normal_cdf(value: float) -> float:
        return value / (1 + value)

    weights = (
        (exponential_cdf(commits / 250), 2),
        (exponential_cdf(prs / 50), 3),
        (exponential_cdf(issues / 25), 1),
        (exponential_cdf(reviews / 2), 1),
        (log_normal_cdf(stars / 50), 4),
        (log_normal_cdf(followers / 10), 1),
    )
    percentile = 100 * (1 - sum(value * weight for value, weight in weights) / 12)
    thresholds = (1, 12.5, 25, 37.5, 50, 62.5, 75, 87.5, 100)
    levels = ("S", "A+", "A", "A-", "B+", "B", "B-", "C+", "C")
    level = next((name for threshold, name in zip(thresholds, levels) if percentile <= threshold), "C")
    return level, percentile, 100 - percentile


def svg_text(x: int, y: int, value: object, size: int, fill: str, weight: int = 400, anchor: str = "start") -> str:
    return (f'<text x="{x}" y="{y}" text-anchor="{anchor}" '
            f'font-family="Inter,Segoe UI,Arial,sans-serif" font-size="{size}px" '
            f'font-weight="{weight}" fill="{fill}">{escape(str(value))}</text>')


def card_shell(width: int, height: int, body: str, defs: str = "") -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img">{defs}'
            f'<rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="20" '
            f'fill="{NAVY}" stroke="{BORDER}" stroke-width="2"/>{body}</svg>')


def performance_card(stats: dict, updated: str) -> str:
    level, percentile, score = stats["grade"]
    body = [
        svg_text(34, 40, "GITHUB PERFORMANCE", 20, CYAN, 700),
        svg_text(34, 70, USERNAME, 15, MUTED, 600),
    ]
    metrics = [
        ("TOTAL STARS", stats["stars"]),
        ("COMMITS · LAST 365 DAYS", stats["commits"]),
        ("PULL REQUESTS · PUBLIC TOTAL", stats["prs"]),
        ("ISSUES · PUBLIC TOTAL", stats["issues"]),
        ("REPOSITORIES CONTRIBUTED TO", stats["repos_contributed"]),
        ("FOLLOWERS", stats["followers"]),
    ]
    x_positions = (34, 300, 566)
    y_positions = (115, 202)
    for index, (label, value) in enumerate(metrics):
        col, row = index % 3, index // 3
        x, y = x_positions[col], y_positions[row]
        body.extend([
            svg_text(x, y, label, 13, MUTED, 600),
            svg_text(x, y + 39, f"{value:,}", 29, TEXT, 700),
        ])

    cx, cy, radius = 891, 151, 54
    circumference = 2 * math.pi * radius
    body.extend([
        f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" stroke="{PANEL_ALT}" stroke-width="9"/>',
        f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" stroke="url(#grade)" stroke-width="9" '
        f'stroke-linecap="round" stroke-dasharray="{circumference * score / 100:.2f} {circumference:.2f}" '
        f'transform="rotate(-90 {cx} {cy})"/>',
        svg_text(cx, cy - 10, "GRADE", 11, MUTED, 700, "middle"),
        svg_text(cx, cy + 24, level, 39, TEXT, 800, "middle"),
        svg_text(cx, cy + 83, f"{score:.0f} / 100", 16, CYAN, 700, "middle"),
        svg_text(34, 285, "GitHub Readme Stats rank method · circle shows activity score · profile-visible contribution data", 12, MUTED),
        svg_text(1026, 285, f"UPDATED {updated}", 11, MUTED, 500, "end"),
    ])
    return card_shell(1040, 310, "".join(body), '<defs><linearGradient id="grade"><stop stop-color="#00F5FF"/><stop offset="1" stop-color="#9B8CFF"/></linearGradient></defs>')


def contribution_card(total: int, current: int, current_start: date | None, current_end: date | None,
                      longest: int, longest_start: date | None, longest_end: date | None,
                      period_start: date, period_end: date) -> str:
    total_range = f"{display_date(period_start)} – Present"
    current_range = (f"{display_date(current_start)} – {display_date(current_end)}"
                     if current_start else "No active streak")
    longest_range = (f"{display_date(longest_start)} – {display_date(longest_end)}"
                     if longest_start else "No recorded streak")
    values = [
        ("TOTAL CONTRIBUTIONS", f"{total:,}", "LAST 365 DAYS", total_range),
        ("CURRENT STREAK", f"{current} days", "CONSECUTIVE DAYS", current_range),
        ("LONGEST STREAK", f"{longest} days", "ALL-TIME RECORD", longest_range),
    ]
    body = [svg_text(34, 40, "CONTRIBUTIONS & STREAK", 20, CYAN, 700)]
    for index, (label, value, note, dates) in enumerate(values):
        left = index * 346
        center = left + 173
        if index:
            body.append(f'<line x1="{left}" y1="61" x2="{left}" y2="214" stroke="{BORDER}"/>')
        body.extend([
            svg_text(center, 83, label, 14, MUTED, 700, "middle"),
            svg_text(center, 131, value, 41, TEXT, 750, "middle"),
            svg_text(center, 155, note, 12, PURPLE, 600, "middle"),
            svg_text(center, 190, dates, 13, TEXT, 600, "middle"),
        ])
    return card_shell(1040, 230, "".join(body))


def languages_card(languages: list[dict], all_languages: list[dict], updated: str) -> str:
    languages = languages[:6]
    top_bytes = sum(item["size"] for item in languages)
    all_bytes = sum(item["size"] for item in all_languages)
    if not all_bytes:
        entries = [{"name": "No language data", "color": OTHER, "size": 1}]
        all_bytes = 1
    else:
        other_size = max(0, all_bytes - top_bytes)
        entries = languages + ([{"name": "Other", "color": OTHER, "size": other_size}] if other_size else [])

    bar_x, bar_y, bar_width, bar_height = 34, 92, 972, 18
    body = [
        svg_text(34, 40, "MOST USED LANGUAGES", 20, CYAN, 700),
        svg_text(34, 66, "Public, non-fork repositories · share of detected source bytes", 14, MUTED),
        f'<clipPath id="bar"><rect x="{bar_x}" y="{bar_y}" width="{bar_width}" height="{bar_height}" rx="9"/></clipPath>',
    ]
    offset = 0.0
    for index, item in enumerate(entries):
        ratio = item["size"] / all_bytes if all_bytes else 0
        width = bar_width * ratio
        if width > 0:
            color = OTHER if item["name"] == "Other" else LANGUAGE_PALETTE[index % len(LANGUAGE_PALETTE)]
            body.append(f'<rect x="{bar_x + offset:.2f}" y="{bar_y}" width="{width + .2:.2f}" height="{bar_height}" fill="{color}" clip-path="url(#bar)"/>')
            offset += width

    for index, item in enumerate(entries):
        col, row = index % 2, index // 2
        x = 34 + col * 500
        y = 144 + row * 36
        color = OTHER if item["name"] == "Other" else LANGUAGE_PALETTE[index % len(LANGUAGE_PALETTE)]
        percent = 100 * item["size"] / all_bytes if all_bytes else 0
        body.extend([
            f'<circle cx="{x + 6}" cy="{y - 4}" r="5" fill="{color}"/>',
            svg_text(x + 20, y, item["name"], 15, TEXT, 600),
            svg_text(x + 448, y, f"{percent:.1f}%", 16, color, 700, "end"),
        ])
    body.append(svg_text(1006, 286, f"UPDATED {updated}", 10, MUTED, 500, "end"))
    return card_shell(1040, 300, "".join(body))


def collect(token: str) -> dict:
    now = datetime.now(timezone.utc)
    today = now.date()
    year_start = today - timedelta(days=364)
    summary = graphql(token, SUMMARY_QUERY, {
        "login": USERNAME,
        "from": iso_start(year_start),
        "to": now.isoformat().replace("+00:00", "Z"),
        "prQuery": f"author:{USERNAME} is:public type:pr",
        "issueQuery": f"author:{USERNAME} is:public type:issue",
    })
    user = summary.get("user")
    if not user:
        raise RuntimeError(f"GitHub user {USERNAME} was not found")

    year_days = calendar_days(
        user["contributionsCollection"]["contributionCalendar"], year_start, today
    )
    total_contributions = sum(year_days.values())

    all_days, total_reviews = fetch_history(
        token, date.fromisoformat(user["createdAt"][:10]), today
    )
    current_streak, current_start, current_end, longest_streak, longest_start, longest_end = streaks(all_days, today)

    language_sizes: dict[str, dict] = {}
    stars = 0
    cursor = None
    repos_contributed = user["repositoriesContributedTo"]["totalCount"]
    repo_count = None
    while True:
        repo_data = graphql(token, REPOSITORIES_QUERY, {"login": USERNAME, "after": cursor})["user"]["repositories"]
        repo_count = repo_data["totalCount"]
        for repo in repo_data["nodes"]:
            stars += repo["stargazerCount"]
            for edge in repo["languages"]["edges"]:
                name = edge["node"]["name"]
                entry = language_sizes.setdefault(name, {"name": name, "color": edge["node"]["color"], "size": 0})
                entry["size"] += edge["size"]
                if not entry["color"]:
                    entry["color"] = edge["node"]["color"]
        if not repo_data["pageInfo"]["hasNextPage"]:
            break
        cursor = repo_data["pageInfo"]["endCursor"]

    languages = sorted(language_sizes.values(), key=lambda item: item["size"], reverse=True)
    stats = {
        "stars": stars,
        "commits": user["contributionsCollection"]["totalCommitContributions"],
        "prs": summary["prs"]["issueCount"],
        "issues": summary["issues"]["issueCount"],
        "repos_contributed": repos_contributed,
        "followers": user["followers"]["totalCount"],
        "repos_owned": repo_count,
    }
    stats["grade"] = grade(
        stats["stars"], stats["commits"], stats["prs"], stats["issues"],
        total_reviews, stats["followers"],
    )
    return {
        "stats": stats,
        "contributions": (total_contributions, current_streak, current_start, current_end,
                          longest_streak, longest_start, longest_end, year_start, today),
        "languages": languages,
        "updated": today.isoformat(),
    }


def main() -> None:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise RuntimeError("Set GITHUB_TOKEN or GH_TOKEN; tokens are read from environment only")
    data = collect(token)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "performance.svg").write_text(performance_card(data["stats"], data["updated"]), encoding="utf-8")
    (OUTPUT / "contributions.svg").write_text(contribution_card(*data["contributions"]), encoding="utf-8")
    (OUTPUT / "languages.svg").write_text(languages_card(data["languages"], data["languages"], data["updated"]), encoding="utf-8")
    print(f"Generated analytics SVGs for {USERNAME} ({data['updated']})")
    print("Stats:", json.dumps(data["stats"], sort_keys=True))
    print("Contributions (last 365 days, current streak, longest):", data["contributions"])
    print("Top languages:", ", ".join(f"{item['name']} {item['size']} bytes" for item in data["languages"][:6]))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Analytics generation failed: {error}", file=sys.stderr)
        raise
