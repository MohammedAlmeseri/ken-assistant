"""Opening things in the default browser: websites, Google/YouTube searches, YouTube videos.

Videos are found with the official YouTube Data API v3 (YOUTUBE_API_KEY in .env, free quota:
about 100 searches a day). Without a key, "play X" opens the YouTube results page instead.

Safety: Ken reads messages and emails, which can carry links meant to trick people. A link is
opened directly only when the user asked for it (the site is named in their own words) or it's a
Google/YouTube address Ken built itself. Anything else becomes a card the user clicks.
"""

from __future__ import annotations

import html
import json
import os
import re
import urllib.parse
import urllib.request
import webbrowser

# ----------------------------------------------------------------------------
# Trust rules
#   Ken builds Google / YouTube URLs himself. Redirect paths are refused: they can hide any destination.
# ----------------------------------------------------------------------------

# Hosts Ken may open by itself, and on Google only the search page: google.com/url?q=... is a
# redirect that could lead anywhere, as could YouTube's /redirect.
TRUSTED_HOSTS = {"google.com", "www.google.com", "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
UNSAFE_PATHS = ("/url", "/redirect", "/attribution_link", "/aclk", "/imgres")
API = "https://www.googleapis.com/youtube/v3/search"


# ----------------------------------------------------------------------------
# Building URLs
# ----------------------------------------------------------------------------

def google_search_url(query: str) -> str:
    return "https://www.google.com/search?" + urllib.parse.urlencode({"q": query})


def youtube_search_url(query: str) -> str:
    return "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": query})


def video_url(video_id: str) -> str:
    return "https://www.youtube.com/watch?" + urllib.parse.urlencode({"v": video_id})


# ----------------------------------------------------------------------------
# Checking URLs
# ----------------------------------------------------------------------------

def normalize_url(url: str) -> str | None:
    """'github.com' -> 'https://github.com'. None for anything that isn't a web address."""
    url = url.strip()
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        url = "https://" + url
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or "." not in parts.hostname:
        return None  # no file://, javascript:, data: ...
    return url


def site_name(host: str) -> str:
    """'www.github.com' -> 'github', 'docs.python.org' -> 'python'."""
    labels = [label for label in host.lower().split(".") if label not in ("www", "m")]
    return labels[-2] if len(labels) >= 2 else labels[0]


def asked_for(url: str, user_text: str) -> bool:
    """True if this link may open without a click: a trusted Google/YouTube address, or a site the
    user named in their own message ("open github" -> github.com)."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if host in TRUSTED_HOSTS:
        google = host.endswith("google.com")
        return not parts.path.startswith(UNSAFE_PATHS) and (not google or parts.path in ("", "/", "/search"))
    said = user_text.lower()
    return host in said or bool(re.search(rf"\b{re.escape(site_name(host))}\b", said))


# ----------------------------------------------------------------------------
# Opening
# ----------------------------------------------------------------------------

def open_in_browser(url: str) -> None:
    webbrowser.open(url, new=2)  # a new tab in the default browser


# ----------------------------------------------------------------------------
# YouTube Data API
#   Needs YOUTUBE_API_KEY; without it Ken opens the results page instead.
# ----------------------------------------------------------------------------

def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def youtube_key() -> str:
    return (os.getenv("YOUTUBE_API_KEY") or "").strip()


def youtube_search(query: str, max_results: int = 5) -> list[dict]:
    """Top videos for a query: [{id, title, channel, published}]. Raises without a key."""
    key = youtube_key()
    if not key:
        raise RuntimeError("no YOUTUBE_API_KEY")
    params = {"part": "snippet", "type": "video", "maxResults": max_results, "q": query, "key": key}
    data = _get_json(API + "?" + urllib.parse.urlencode(params))
    out = []
    for item in data.get("items", []):
        vid = item.get("id", {}).get("videoId")
        snip = item.get("snippet", {})
        if vid:
            out.append({"id": vid, "title": html.unescape(snip.get("title", "")),
                        "channel": html.unescape(snip.get("channelTitle", "")),
                        "published": (snip.get("publishedAt") or "")[:10]})
    return out
