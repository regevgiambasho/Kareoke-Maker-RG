from __future__ import annotations

import json
import os
import re
import unicodedata
import urllib.error
import urllib.request
import urllib.parse
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any


RTL_LANGUAGES = {"he", "ar", "fa", "ur"}


@dataclass
class LyricsCandidate:
    lyrics: str
    title: str | None = None
    artist: str | None = None
    source: str = "licensed-api"
    licensed: bool = True
    synced_lyrics: str | None = None
    provider_id: str | None = None
    provider_duration: float | None = None


@dataclass
class LyricsAlignmentResult:
    lines: list[dict[str, Any]]
    confidence: float
    coverage: float
    source: str
    verified: bool
    title: str | None = None
    artist: str | None = None


def _strip_diacritics(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text)
        if unicodedata.category(ch) != "Mn"
    )


def normalize_token(token: str) -> str:
    token = _strip_diacritics(unicodedata.normalize("NFKC", token or ""))
    token = token.casefold()
    token = token.replace("׳", "'").replace("״", '"')
    token = re.sub(r"[^\w\u0590-\u05FF\u0600-\u06FF']+", "", token, flags=re.UNICODE)
    return token.strip("_'")


def token_similarity(a: str, b: str) -> float:
    a = normalize_token(a)
    b = normalize_token(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) >= 2 and len(b) >= 2 and (a in b or b in a):
        return min(len(a), len(b)) / max(len(a), len(b))
    return SequenceMatcher(None, a, b).ratio()


def _clean_canonical_lines(lyrics: str) -> list[str]:
    out: list[str] = []
    for raw in (lyrics or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        # Do not render section labels such as [Chorus] / [Verse 1].
        if re.fullmatch(r"\[[^\]]{1,40}\]", line):
            continue
        out.append(line)
    return out


def _split_long_line(text: str, max_chars: int) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text or len(text) <= max_chars:
        return [text] if text else []

    words = text.split()
    chunks: list[str] = []
    start = 0

    while start < len(words):
        end = start
        current = ""
        while end < len(words):
            candidate = " ".join(words[start:end + 1])
            if len(candidate) > max_chars and end > start:
                break
            current = candidate
            end += 1

        if end >= len(words):
            chunks.append(" ".join(words[start:]))
            break

        # Prefer a nearby punctuation boundary rather than a mechanical word count.
        search_from = max(start + 2, end - 4)
        preferred = None
        for i in range(end - 1, search_from - 1, -1):
            if re.search(r"[,;:!?….\-–—]$", words[i]):
                preferred = i + 1
                break

        cut = preferred or max(start + 1, end)
        chunks.append(" ".join(words[start:cut]))
        start = cut

    return [x for x in chunks if x]


def canonical_lines(lyrics: str, language: str) -> list[str]:
    max_chars = 38 if language in RTL_LANGUAGES else 48
    lines: list[str] = []
    for line in _clean_canonical_lines(lyrics):
        lines.extend(_split_long_line(line, max_chars=max_chars))
    return lines


def synced_lyrics_compatible(
    provider_duration: float | None,
    media_duration: float | None,
    absolute_tolerance: float = 3.0,
    relative_tolerance: float = 0.018,
) -> bool:
    if provider_duration is None or media_duration is None:
        return False
    try:
        provider=float(provider_duration); media=float(media_duration)
    except Exception:
        return False
    if provider <= 0 or media <= 0:
        return False
    tolerance=max(float(absolute_tolerance), min(6.0, media*float(relative_tolerance)))
    return abs(provider-media) <= tolerance


def parse_synced_lyrics_lines(
    synced_lyrics: str | None,
    media_duration: float,
    language: str,
) -> list[dict[str, Any]]:
    raw=(synced_lyrics or '').strip()
    if not raw or media_duration <= 0:
        return []
    timestamp_re=re.compile(r'^\[(?P<min>\d{1,3}):(?P<sec>\d{2}(?:\.\d{1,3})?)\]\s*(?P<text>.*)$')
    stamped=[]
    for source_line in raw.splitlines():
        m=timestamp_re.match(source_line.strip())
        if not m: continue
        text=re.sub(r'\s+',' ',m.group('text') or '').strip()
        if not text: continue
        start=int(m.group('min'))*60.0+float(m.group('sec'))
        if 0 <= start <= media_duration+2.0:
            stamped.append((start,text))
    if not stamped: return []
    stamped.sort(key=lambda x:x[0])
    deduped=[]; seen=set()
    for item in stamped:
        key=(round(item[0],3),item[1])
        if key in seen: continue
        seen.add(key); deduped.append(item)
    gaps=[deduped[i+1][0]-deduped[i][0] for i in range(len(deduped)-1) if deduped[i+1][0]>deduped[i][0]]
    import statistics as _statistics
    typical_gap=_statistics.median(gaps) if gaps else 3.0
    typical_gap=min(7.0,max(1.0,typical_gap))
    lines=[]
    for i,(start,text) in enumerate(deduped):
        if i+1 < len(deduped): end=max(start+0.25,deduped[i+1][0]-0.05)
        else: end=min(media_duration,start+typical_gap)
        end=min(media_duration,max(start+0.25,end))
        tokens=text.split()
        if not tokens: continue
        weights=[max(1,len(t)) for t in tokens]; total=float(sum(weights)); cursor=start; words=[]
        for wi,(token,weight) in enumerate(zip(tokens,weights)):
            span=(end-start)*weight/total
            we=end if wi==len(tokens)-1 else min(end,cursor+span)
            words.append({'text':token,'start':cursor,'end':max(cursor+0.03,we)})
            cursor=we
        lines.append({'start':start,'end':end,'text':text,'words':words,'verified':True,'timing_source':'provider-synced'})
    return lines


def _http_json_get(
    url: str,
    timeout: float = 12.0,
    user_agent: str = "Karaoke-Maker-V2/2.0 (local app)",
) -> Any | None:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": user_agent,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 429, 503):
            return None
        return None
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None


def _lrclib_candidate_from_record(record: dict[str, Any]) -> LyricsCandidate | None:
    if not isinstance(record, dict) or bool(record.get("instrumental")):
        return None

    plain = str(record.get("plainLyrics") or "").strip()
    synced = str(record.get("syncedLyrics") or "").strip()

    if not plain and synced:
        plain_lines: list[str] = []
        for raw in synced.splitlines():
            # Strip standard LRC timestamps while preserving lyric text.
            line = re.sub(r"^\[\d{1,3}:\d{2}(?:\.\d+)?\]\s*", "", raw).strip()
            if line:
                plain_lines.append(line)
        plain = "\n".join(plain_lines).strip()

    if not plain:
        return None

    duration = record.get("duration")
    try:
        duration_value = float(duration) if duration is not None else None
    except Exception:
        duration_value = None

    return LyricsCandidate(
        lyrics=plain,
        title=str(record.get("trackName") or record.get("name") or "").strip() or None,
        artist=str(record.get("artistName") or "").strip() or None,
        source="lrclib",
        # LRCLIB is a public/community provider, not a commercial lyrics licence.
        licensed=False,
        synced_lyrics=synced or None,
        provider_id=str(record.get("id") or "").strip() or None,
        provider_duration=duration_value,
    )


def _lrclib_match_score(
    record: dict[str, Any],
    title: str,
    artist: str,
    duration: float | None = None,
) -> float:
    record_title = _normalize_match_text(
        str(record.get("trackName") or record.get("name") or "")
    )
    record_artist = _normalize_match_text(str(record.get("artistName") or ""))
    wanted_title = _normalize_match_text(title)
    wanted_artist = _normalize_match_text(artist)

    title_score = SequenceMatcher(None, wanted_title, record_title).ratio()
    artist_score = SequenceMatcher(None, wanted_artist, record_artist).ratio()
    score = 0.64 * title_score + 0.36 * artist_score

    if duration is not None and record.get("duration") is not None:
        try:
            delta = abs(float(record["duration"]) - float(duration))
            if delta <= 2.0:
                score += 0.08
            elif delta <= 8.0:
                score += 0.03
            elif delta > 20.0:
                score -= 0.12
        except Exception:
            pass

    if bool(record.get("instrumental")):
        score -= 0.50

    return max(0.0, min(1.0, score))


def fetch_lrclib_lyrics(
    title: str | None,
    artist: str | None,
    duration: float | None = None,
    timeout: float = 12.0,
) -> LyricsLookupResult:
    """
    Free/community lyrics lookup through LRCLIB.

    Strategy:
      1. Exact /api/get with title + artist + duration when available.
      2. Structured /api/search fallback.
      3. Conservative title/artist scoring before accepting a result.
    """
    if not title or not artist:
        return LyricsLookupResult(
            candidate=None,
            provider="lrclib",
            status="missing_identity",
            message="LRCLIB needs song title and artist.",
        )

    base = "https://lrclib.net"

    exact_params = {
        "track_name": title,
        "artist_name": artist,
    }
    if duration is not None and 1 <= float(duration) <= 3600:
        exact_params["duration"] = str(int(round(float(duration))))

    exact_url = base + "/api/get?" + urllib.parse.urlencode(exact_params)
    exact = _http_json_get(exact_url, timeout=timeout)

    if isinstance(exact, dict):
        candidate = _lrclib_candidate_from_record(exact)
        score = _lrclib_match_score(exact, title, artist, duration)
        if candidate is not None and score >= 0.74:
            sync_note = " with synced lines" if candidate.synced_lyrics else ""
            return LyricsLookupResult(
                candidate=candidate,
                provider="lrclib",
                status="ok",
                message=(
                    f"LRCLIB exact match{sync_note}: "
                    f"{candidate.title or title} — {candidate.artist or artist} "
                    f"(match {score:.0%})."
                ),
            )

    search_url = base + "/api/search?" + urllib.parse.urlencode({
        "track_name": title,
        "artist_name": artist,
    })
    results = _http_json_get(search_url, timeout=timeout)

    if isinstance(results, list):
        best = None
        best_score = 0.0
        for record in results:
            if not isinstance(record, dict):
                continue
            score = _lrclib_match_score(record, title, artist, duration)
            if score > best_score:
                best = record
                best_score = score

        if best is not None and best_score >= 0.76:
            candidate = _lrclib_candidate_from_record(best)
            if candidate is not None:
                sync_note = " with synced lines" if candidate.synced_lyrics else ""
                return LyricsLookupResult(
                    candidate=candidate,
                    provider="lrclib",
                    status="ok",
                    message=(
                        f"LRCLIB search match{sync_note}: "
                        f"{candidate.title or title} — {candidate.artist or artist} "
                        f"(match {best_score:.0%})."
                    ),
                )

    return LyricsLookupResult(
        candidate=None,
        provider="lrclib",
        status="not_found",
        message="No sufficiently confident LRCLIB match was found.",
    )



def _musixmatch_api_get(
    method: str,
    params: dict[str, Any],
    timeout: float = 12.0,
) -> dict[str, Any] | None:
    api_key = os.environ.get("MUSIXMATCH_API_KEY", "").strip()
    if not api_key:
        return None

    query = {
        "apikey": api_key,
        "format": "json",
        **params,
    }
    url = (
        "https://api.musixmatch.com/ws/1.1/"
        + method
        + "?"
        + urllib.parse.urlencode(query)
    )
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Karaoke-Maker-V2/2.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        TimeoutError,
        ValueError,
    ):
        return None


def _mxm_header_status(data: dict[str, Any] | None) -> int:
    try:
        return int(
            (((data or {}).get("message") or {}).get("header") or {}).get("status_code")
            or 0
        )
    except Exception:
        return 0


def _normalize_match_text(value: str | None) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    value = _strip_diacritics(value)
    value = re.sub(r"[^\w\u0590-\u05FF\u0600-\u06FF]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def _track_match_score(
    track: dict[str, Any],
    wanted_title: str,
    wanted_artist: str,
) -> float:
    track_title = _normalize_match_text(str(track.get("track_name") or ""))
    track_artist = _normalize_match_text(str(track.get("artist_name") or ""))
    wanted_title_n = _normalize_match_text(wanted_title)
    wanted_artist_n = _normalize_match_text(wanted_artist)

    title_score = SequenceMatcher(None, wanted_title_n, track_title).ratio()
    artist_score = SequenceMatcher(None, wanted_artist_n, track_artist).ratio()

    # Title is a bit more important than artist aliases/transliterations.
    score = 0.62 * title_score + 0.38 * artist_score

    if int(track.get("has_lyrics") or 0) == 1:
        score += 0.05
    if int(track.get("instrumental") or 0) == 1:
        score -= 0.50

    return max(0.0, min(1.0, score))


def search_musixmatch_track(
    title: str | None,
    artist: str | None,
    timeout: float = 12.0,
) -> dict[str, Any] | None:
    if not title or not artist:
        return None
    if not os.environ.get("MUSIXMATCH_API_KEY", "").strip():
        return None

    data = _musixmatch_api_get(
        "track.search",
        {
            "q_track": title,
            "q_artist": artist,
            "f_has_lyrics": 1,
            "s_track_rating": "desc",
            "page": 1,
            "page_size": 8,
        },
        timeout=timeout,
    )
    if _mxm_header_status(data) != 200:
        return None

    try:
        items = ((data["message"]["body"]).get("track_list") or [])
    except Exception:
        return None

    best_track = None
    best_score = 0.0
    for item in items:
        track = (item or {}).get("track") or {}
        if not track:
            continue
        score = _track_match_score(track, title, artist)
        if score > best_score:
            best_score = score
            best_track = track

    # Keep the threshold conservative. If the match is weak, Review should win
    # instead of attaching lyrics from the wrong song.
    if not best_track or best_score < 0.68:
        return None

    return {
        "track": best_track,
        "match_score": round(best_score, 4),
    }


def fetch_musixmatch_lyrics_by_track(
    track: dict[str, Any],
    title: str | None,
    artist: str | None,
    timeout: float = 12.0,
) -> LyricsCandidate | None:
    track_id = track.get("track_id")
    if not track_id:
        return None

    data = _musixmatch_api_get(
        "track.lyrics.get",
        {"track_id": track_id},
        timeout=timeout,
    )
    if _mxm_header_status(data) != 200:
        return None

    try:
        lyrics_obj = data["message"]["body"]["lyrics"]
    except Exception:
        return None

    body = str(lyrics_obj.get("lyrics_body") or "").strip()
    restricted = int(lyrics_obj.get("restricted") or 0)

    if not body or restricted == 1:
        return None

    lowered = body.casefold()
    if (
        "this lyrics is not for commercial use" in lowered
        or "lyrics is not for commercial use" in lowered
    ):
        return None

    lines = [x.strip() for x in body.splitlines() if x.strip()]
    if len(lines) < 2:
        return None

    return LyricsCandidate(
        lyrics=body,
        title=str(track.get("track_name") or title or "").strip() or None,
        artist=str(track.get("artist_name") or artist or "").strip() or None,
        source="musixmatch",
        licensed=True,
    )


def parse_musixmatch_url_hint(url: str | None) -> dict[str, str | None]:
    """
    Parse only identity hints from a public Musixmatch URL.
    No page scraping and no lyric extraction.
    """
    raw = (url or "").strip()
    if not raw:
        return {"artist": None, "title": None}

    try:
        parsed = urllib.parse.urlparse(raw)
        if "musixmatch.com" not in parsed.netloc.casefold():
            return {"artist": None, "title": None}

        parts = [urllib.parse.unquote(x) for x in parsed.path.split("/") if x]
        # Expected public shape: /lyrics/<artist>/<title>
        if len(parts) < 3 or parts[0].casefold() != "lyrics":
            return {"artist": None, "title": None}

        artist = parts[1].replace("-", " ").strip() or None
        title = parts[2].replace("-", " ").strip() or None
        return {"artist": artist, "title": title}
    except Exception:
        return {"artist": None, "title": None}


def _candidate_from_musixmatch_response(
    data: dict[str, Any],
    fallback_title: str | None,
    fallback_artist: str | None,
) -> LyricsCandidate | None:
    """
    Parse the official Musixmatch API response.

    We intentionally reject restricted/truncated developer responses instead
    of treating a partial lyric body as canonical karaoke text.
    """
    try:
        message = data.get("message") or {}
        header = message.get("header") or {}
        if int(header.get("status_code") or 0) != 200:
            return None

        lyrics = ((message.get("body") or {}).get("lyrics") or {})
        body = str(lyrics.get("lyrics_body") or "").strip()
        if not body:
            return None

        if int(lyrics.get("restricted") or 0) == 1:
            return None

        lowered = body.casefold()
        truncation_markers = (
            "this lyrics is not for commercial use",
            "lyrics is not for commercial use",
        )
        if any(marker in lowered for marker in truncation_markers):
            return None

        # A usable canonical response should contain actual lyric structure.
        nonempty_lines = [x.strip() for x in body.splitlines() if x.strip()]
        if len(nonempty_lines) < 2:
            return None

        return LyricsCandidate(
            lyrics=body,
            title=fallback_title,
            artist=fallback_artist,
            source="musixmatch",
            licensed=True,
        )
    except Exception:
        return None


@dataclass
class LyricsLookupResult:
    candidate: LyricsCandidate | None
    provider: str
    status: str
    message: str


def _provider_configured() -> bool:
    return bool(
        os.environ.get("MUSIXMATCH_API_KEY", "").strip()
        or os.environ.get("LICENSED_LYRICS_API_URL", "").strip()
    )


def fetch_musixmatch_lyrics(
    title: str | None,
    artist: str | None,
    timeout: float = 12.0,
) -> LyricsCandidate | None:
    """
    Official Musixmatch flow:
      track.search -> best trustworthy track -> track.lyrics.get

    This follows Musixmatch's documented common integration pattern.
    """
    match = search_musixmatch_track(title, artist, timeout=timeout)
    if not match:
        return None

    track = match["track"]
    return fetch_musixmatch_lyrics_by_track(
        track=track,
        title=title,
        artist=artist,
        timeout=timeout,
    )



def _lyricsovh_exact(
    artist: str,
    title: str,
    timeout: float = 10.0,
) -> LyricsCandidate | None:
    """
    Exact lyrics.ovh lookup:
      GET https://api.lyrics.ovh/v1/{artist}/{title}
    """
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not artist or not title:
        return None

    url = (
        "https://api.lyrics.ovh/v1/"
        + urllib.parse.quote(artist, safe="")
        + "/"
        + urllib.parse.quote(title, safe="")
    )
    data = _http_json_get(url, timeout=timeout)
    if not isinstance(data, dict):
        return None

    body = str(data.get("lyrics") or "").strip()
    if not body:
        return None

    return LyricsCandidate(
        lyrics=body,
        title=title,
        artist=artist,
        source="lyrics.ovh",
        licensed=False,
    )


def _lyricsovh_suggest(
    query: str,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    """
    lyrics.ovh suggestion endpoint. Its own project documents this endpoint
    as returning Deezer search results.
    """
    query = (query or "").strip()
    if not query:
        return []

    url = "https://api.lyrics.ovh/suggest/" + urllib.parse.quote(query, safe="")
    data = _http_json_get(url, timeout=timeout)
    if not isinstance(data, dict):
        return []

    rows = data.get("data")
    return rows if isinstance(rows, list) else []


def _lyricsovh_suggestion_score(
    row: dict[str, Any],
    wanted_title: str,
    wanted_artist: str,
) -> float:
    title = _normalize_match_text(str(row.get("title") or ""))
    artist_obj = row.get("artist") or {}
    artist = _normalize_match_text(str(artist_obj.get("name") or ""))

    wanted_title_n = _normalize_match_text(wanted_title)
    wanted_artist_n = _normalize_match_text(wanted_artist)

    title_score = SequenceMatcher(None, wanted_title_n, title).ratio()
    artist_score = SequenceMatcher(None, wanted_artist_n, artist).ratio()

    return max(0.0, min(1.0, 0.64 * title_score + 0.36 * artist_score))


def fetch_lyricsovh_title_only(
    title: str | None,
    timeout: float = 10.0,
) -> LyricsLookupResult:
    """
    Resolve a song from title only using lyrics.ovh /suggest, then fetch lyrics.

    This is intentionally conservative: title similarity must be strong enough
    before an artist is inferred automatically.
    """
    title = (title or "").strip()
    if not title:
        return LyricsLookupResult(
            candidate=None,
            provider="lyrics.ovh",
            status="missing_title",
            message="A song title is required.",
        )

    suggestions = _lyricsovh_suggest(title, timeout=timeout)
    ranked: list[tuple[float, dict[str, Any]]] = []

    wanted = _normalize_match_text(title)

    for row in suggestions:
        if not isinstance(row, dict):
            continue
        candidate_title = str(row.get("title") or "").strip()
        if not candidate_title:
            continue

        score = SequenceMatcher(
            None,
            wanted,
            _normalize_match_text(candidate_title),
        ).ratio()

        # Prefer near-exact title matches. This prevents a famous unrelated
        # song from being selected just because the words are similar.
        if score >= 0.82:
            ranked.append((score, row))

    ranked.sort(key=lambda x: x[0], reverse=True)

    for score, row in ranked[:5]:
        candidate_title = str(row.get("title") or "").strip()
        artist_obj = row.get("artist") or {}
        candidate_artist = str(artist_obj.get("name") or "").strip()
        if not candidate_title or not candidate_artist:
            continue

        exact = _lyricsovh_exact(
            artist=candidate_artist,
            title=candidate_title,
            timeout=timeout,
        )
        if exact is not None:
            exact.title = candidate_title
            exact.artist = candidate_artist
            return LyricsLookupResult(
                candidate=exact,
                provider="lyrics.ovh",
                status="ok",
                message=(
                    "lyrics.ovh inferred artist from title-only filename: "
                    f"{candidate_title} — {candidate_artist} "
                    f"(title match {score:.0%})."
                ),
            )

    return LyricsLookupResult(
        candidate=None,
        provider="lyrics.ovh",
        status="title_only_not_found",
        message="Could not infer a confident artist from the filename title.",
    )



def fetch_lyricsovh_lyrics(
    title: str | None,
    artist: str | None,
    timeout: float = 10.0,
) -> LyricsLookupResult:
    """
    Free/keyless lyrics lookup using lyrics.ovh.

    Strategy:
      1. Exact artist/title lookup.
      2. Search suggestions to recover canonical title/artist spelling.
      3. Retry exact lookup for the best few confident suggestions.
    """
    title = (title or "").strip()
    artist = (artist or "").strip()

    if not title or not artist:
        return LyricsLookupResult(
            candidate=None,
            provider="lyrics.ovh",
            status="missing_identity",
            message="lyrics.ovh needs both song title and artist.",
        )

    # First try the exact identity we already have.
    exact = _lyricsovh_exact(artist=artist, title=title, timeout=timeout)
    if exact is not None:
        return LyricsLookupResult(
            candidate=exact,
            provider="lyrics.ovh",
            status="ok",
            message=f"lyrics.ovh exact match: {title} — {artist}.",
        )

    # Then use the documented suggestion endpoint to recover spelling,
    # punctuation and transliteration differences.
    suggestions = _lyricsovh_suggest(
        f"{artist} {title}",
        timeout=timeout,
    )

    ranked: list[tuple[float, dict[str, Any]]] = []
    for row in suggestions:
        if not isinstance(row, dict):
            continue
        score = _lyricsovh_suggestion_score(row, title, artist)
        if score >= 0.62:
            ranked.append((score, row))

    ranked.sort(key=lambda item: item[0], reverse=True)

    attempted: set[tuple[str, str]] = set()
    for score, row in ranked[:4]:
        candidate_title = str(row.get("title") or "").strip()
        artist_obj = row.get("artist") or {}
        candidate_artist = str(artist_obj.get("name") or "").strip()
        if not candidate_title or not candidate_artist:
            continue

        key = (
            _normalize_match_text(candidate_artist),
            _normalize_match_text(candidate_title),
        )
        if key in attempted:
            continue
        attempted.add(key)

        candidate = _lyricsovh_exact(
            artist=candidate_artist,
            title=candidate_title,
            timeout=timeout,
        )
        if candidate is not None:
            candidate.title = candidate_title
            candidate.artist = candidate_artist
            return LyricsLookupResult(
                candidate=candidate,
                provider="lyrics.ovh",
                status="ok",
                message=(
                    "lyrics.ovh suggestion match: "
                    f"{candidate_title} — {candidate_artist} "
                    f"(identity match {score:.0%})."
                ),
            )

    return LyricsLookupResult(
        candidate=None,
        provider="lyrics.ovh",
        status="not_found",
        message="lyrics.ovh did not return usable lyrics for this identity.",
    )



def is_probably_hebrew_identity(title: str | None, artist: str | None, language: str | None) -> bool:
    if (language or '').lower() == 'he':
        return True
    combined = f"{title or ''} {artist or ''}"
    return bool(re.search(r"[\u0590-\u05FF]", combined))


def automatic_provider_order(
    title: str | None,
    artist: str | None,
    language: str | None,
    musixmatch_configured: bool,
    generic_configured: bool,
) -> list[str]:
    """
    Provider order.

    Hebrew:
      Musixmatch (if configured) -> custom licensed API (if configured)
      -> lyrics.ovh -> LRCLIB.

    Other languages:
      lyrics.ovh -> LRCLIB -> Musixmatch/custom licensed fallbacks.
    """
    hebrew = is_probably_hebrew_identity(title, artist, language)

    if hebrew:
        order: list[str] = []
        if musixmatch_configured:
            order.append("musixmatch")
        if generic_configured:
            order.append("licensed-api")
        order.extend(["lyrics.ovh", "lrclib"])
        return order

    order = ["lyrics.ovh", "lrclib"]
    if musixmatch_configured:
        order.append("musixmatch")
    if generic_configured:
        order.append("licensed-api")
    return order



def _lookup_musixmatch_with_status(title: str, artist: str, timeout: float) -> LyricsLookupResult:
    match = search_musixmatch_track(title, artist, timeout=timeout)
    if not match:
        return LyricsLookupResult(None, 'musixmatch', 'track_not_found', 'Musixmatch did not return a confident track match.')
    track = match['track']
    candidate = fetch_musixmatch_lyrics_by_track(track=track, title=title, artist=artist, timeout=timeout)
    if candidate is not None:
        return LyricsLookupResult(candidate, 'musixmatch', 'ok', f"Trusted lyrics returned by Musixmatch for {track.get('track_name', title)} — {track.get('artist_name', artist)} (match {match['match_score']:.0%}).")
    return LyricsLookupResult(None, 'musixmatch', 'track_found_no_usable_lyrics', 'Musixmatch matched the track, but the current API account/plan did not return usable full lyrics.')


def _lookup_generic_licensed_with_status(title: str, artist: str, language: str, transcript_hint: str | None, timeout: float) -> LyricsLookupResult:
    generic_url = os.environ.get('LICENSED_LYRICS_API_URL', '').strip()
    if not generic_url:
        return LyricsLookupResult(None, 'licensed-api', 'not_configured', 'Custom licensed lyrics API is not configured.')
    payload={'title':title,'artist':artist,'language':language or 'auto','transcript_hint':(transcript_hint or '')[:180]}
    headers={'Content-Type':'application/json','Accept':'application/json','User-Agent':'Karaoke-Maker-V2/2.0'}
    token=os.environ.get('LICENSED_LYRICS_API_TOKEN','').strip()
    if token: headers['Authorization']=f'Bearer {token}'
    req=urllib.request.Request(generic_url,data=json.dumps(payload,ensure_ascii=False).encode('utf-8'),headers=headers,method='POST')
    try:
        with urllib.request.urlopen(req,timeout=timeout) as resp: data=json.loads(resp.read().decode('utf-8'))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError): data={}
    body=str(data.get('lyrics') or '').strip(); licensed=bool(data.get('licensed') or data.get('authorized'))
    if body and licensed:
        source=str(data.get('source') or 'licensed-api')
        return LyricsLookupResult(LyricsCandidate(lyrics=body,title=(data.get('title') or title or None),artist=(data.get('artist') or artist or None),source=source,licensed=True),source,'ok','Trusted lyrics returned by the configured provider.')
    return LyricsLookupResult(None,'licensed-api','no_usable_lyrics','The configured licensed lyrics API returned no usable full lyrics.')


def fetch_licensed_lyrics_with_status(
    title: str | None,
    artist: str | None,
    language: str,
    transcript_hint: str | None = None,
    duration: float | None = None,
    timeout: float = 12.0,
) -> LyricsLookupResult:
    if not title or not artist:
        return LyricsLookupResult(None, 'identity', 'missing_identity', 'Song title and artist are required for automatic lyrics lookup.')
    musix_key=os.environ.get('MUSIXMATCH_API_KEY','').strip(); generic_url=os.environ.get('LICENSED_LYRICS_API_URL','').strip()
    order=automatic_provider_order(title,artist,language,bool(musix_key),bool(generic_url))
    attempts=[]
    for provider in order:
        if provider == "musixmatch":
            result = _lookup_musixmatch_with_status(title, artist, timeout)
        elif provider == "licensed-api":
            result = _lookup_generic_licensed_with_status(
                title, artist, language, transcript_hint, timeout
            )
        elif provider == "lyrics.ovh":
            result = fetch_lyricsovh_lyrics(
                title=title,
                artist=artist,
                timeout=timeout,
            )
        else:
            result = fetch_lrclib_lyrics(
                title=title,
                artist=artist,
                duration=duration,
                timeout=timeout,
            )
        attempts.append(result)
        if result.candidate is not None: return result
    hebrew=is_probably_hebrew_identity(title,artist,language)
    if hebrew and not musix_key:
        return LyricsLookupResult(
            None,
            'lyrics.ovh',
            'hebrew_free_sources_not_found',
            'Hebrew automatic lyrics: Musixmatch is not configured, and neither lyrics.ovh nor LRCLIB returned a confident usable result.'
        )
    if attempts:
        last=attempts[-1]
        return LyricsLookupResult(None,last.provider,last.status,'Automatic lyrics lookup did not find usable full lyrics. '+last.message)
    return LyricsLookupResult(None,'none','no_provider','No automatic lyrics provider was available.')


def fetch_licensed_lyrics(
    title: str | None,
    artist: str | None,
    language: str,
    transcript_hint: str | None = None,
    duration: float | None = None,
    timeout: float = 12.0,
) -> LyricsCandidate | None:
    return fetch_licensed_lyrics_with_status(
        title=title,
        artist=artist,
        language=language,
        transcript_hint=transcript_hint,
        duration=duration,
        timeout=timeout,
    ).candidate

def _flatten_canonical(lines: list[str]) -> tuple[list[str], list[tuple[int, int]]]:
    tokens: list[str] = []
    positions: list[tuple[int, int]] = []
    for line_index, line in enumerate(lines):
        for word_index, token in enumerate(line.split()):
            tokens.append(token)
            positions.append((line_index, word_index))
    return tokens, positions


def _alignment_score(canon: str, heard: str) -> float:
    sim = token_similarity(canon, heard)
    if sim >= 0.999:
        return 3.2
    if sim >= 0.88:
        return 2.35
    if sim >= 0.72:
        return 1.25
    if sim >= 0.58:
        return 0.25
    return -1.7


def _sequence_align(
    canonical_tokens: list[str],
    whisper_words: list[dict[str, Any]],
) -> tuple[dict[int, int], list[float]]:
    """
    Global sequence alignment. Canonical lyrics remain authoritative for text;
    Whisper remains authoritative for time.
    """
    observed = [str(w.get("text") or "") for w in whisper_words]
    n, m = len(canonical_tokens), len(observed)
    if not n or not m:
        return {}, []

    gap = -0.82
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    trace = [[0] * (m + 1) for _ in range(n + 1)]  # 1 diag, 2 up, 3 left

    for i in range(1, n + 1):
        dp[i][0] = i * gap
        trace[i][0] = 2
    for j in range(1, m + 1):
        dp[0][j] = j * gap
        trace[0][j] = 3

    for i in range(1, n + 1):
        c = canonical_tokens[i - 1]
        for j in range(1, m + 1):
            diag = dp[i - 1][j - 1] + _alignment_score(c, observed[j - 1])
            up = dp[i - 1][j] + gap
            left = dp[i][j - 1] + gap
            if diag >= up and diag >= left:
                dp[i][j] = diag
                trace[i][j] = 1
            elif up >= left:
                dp[i][j] = up
                trace[i][j] = 2
            else:
                dp[i][j] = left
                trace[i][j] = 3

    mapping: dict[int, int] = {}
    sims: list[float] = []
    i, j = n, m
    while i > 0 or j > 0:
        direction = trace[i][j]
        if i > 0 and j > 0 and direction == 1:
            sim = token_similarity(canonical_tokens[i - 1], observed[j - 1])
            if sim >= 0.50:
                mapping[i - 1] = j - 1
                sims.append(sim)
            i -= 1
            j -= 1
        elif i > 0 and (direction == 2 or j == 0):
            i -= 1
        elif j > 0:
            j -= 1
        else:
            break

    sims.reverse()
    return mapping, sims


def _interpolate_word_times(
    canonical_tokens: list[str],
    whisper_words: list[dict[str, Any]],
    mapping: dict[int, int],
) -> list[dict[str, Any]]:
    count=len(canonical_tokens)
    starts=[None]*count; ends=[None]*count
    for ci,wi in mapping.items():
        heard=whisper_words[wi]
        st=float(heard.get('start',0.0)); en=max(st+0.03,float(heard.get('end',st+0.20)))
        starts[ci]=st; ends[ci]=en
    matched=sorted(mapping)
    if not matched: return []
    rates=[]
    for left,right in zip(matched,matched[1:]):
        span=right-left
        if span<=0: continue
        t1=float(starts[left] or 0.0); t2=float(starts[right] or t1)
        if t2>t1: rates.append((t2-t1)/span)
    if rates:
        import statistics as _statistics
        sec_per_token=_statistics.median(rates)
    else:
        d=[max(0.04,float(w.get('end',0.0))-float(w.get('start',0.0))) for w in whisper_words if w.get('start') is not None and w.get('end') is not None]
        import statistics as _statistics
        sec_per_token=_statistics.median(d) if d else 0.28
    sec_per_token=min(0.85,max(0.09,sec_per_token))
    for left,right in zip(matched,matched[1:]):
        if right-left<=1: continue
        gap_start=float(ends[left] or starts[left] or 0.0); gap_end=float(starts[right] or gap_start); missing=right-left-1
        available=max(0.03*missing,gap_end-gap_start); step=available/max(1,missing); cursor=gap_start
        for idx in range(left+1,right):
            st=cursor; en=min(gap_end,st+max(0.04,step)); starts[idx]=st; ends[idx]=max(st+0.03,en); cursor=en
    first=matched[0]; cursor=float(starts[first] or 0.0)
    for idx in range(first-1,-1,-1):
        en=max(0.03,cursor-0.02); st=max(0.0,en-sec_per_token); starts[idx]=st; ends[idx]=en; cursor=st
    last=matched[-1]; cursor=float(ends[last] or starts[last] or 0.0)
    observed_last=max([float(w.get('end',0.0)) for w in whisper_words if w.get('end') is not None] or [cursor]); soft_limit=max(cursor,observed_last+5.0)
    for idx in range(last+1,count):
        st=cursor+0.02; en=min(soft_limit,st+sec_per_token); en=en if en>st else st+0.04; starts[idx]=st; ends[idx]=en; cursor=en
    out=[]; cursor=0.0
    for idx,token in enumerate(canonical_tokens):
        st=float(starts[idx] if starts[idx] is not None else cursor); en=float(ends[idx] if ends[idx] is not None else st+sec_per_token)
        st=max(cursor,st); en=max(st+0.03,en); out.append({'text':token,'start':st,'end':en}); cursor=en
    return out


def align_verified_lyrics(
    lyrics: str,
    whisper_words: list[dict[str, Any]],
    language: str,
    source: str,
    title: str | None = None,
    artist: str | None = None,
    minimum_confidence: float = 0.60,
) -> LyricsAlignmentResult:
    lines_text = canonical_lines(lyrics, language)
    canonical_tokens, positions = _flatten_canonical(lines_text)
    if not canonical_tokens or not whisper_words:
        return LyricsAlignmentResult([], 0.0, 0.0, source, False, title, artist)

    mapping, sims = _sequence_align(canonical_tokens, whisper_words)
    coverage = len(mapping) / max(1, len(canonical_tokens))
    mean_sim = (sum(sims) / len(sims)) if sims else 0.0
    confidence = 0.58 * coverage + 0.42 * mean_sim

    if confidence < minimum_confidence:
        return LyricsAlignmentResult(
            [], confidence, coverage, source, False, title, artist
        )

    timed_tokens = _interpolate_word_times(canonical_tokens, whisper_words, mapping)
    if not timed_tokens:
        return LyricsAlignmentResult(
            [], confidence, coverage, source, False, title, artist
        )

    by_line: list[list[dict[str, Any]]] = [[] for _ in lines_text]
    for idx, word in enumerate(timed_tokens):
        line_index, _ = positions[idx]
        by_line[line_index].append(word)

    lines: list[dict[str, Any]] = []
    for line_index, words in enumerate(by_line):
        if not words:
            continue
        lines.append({
            "start": float(words[0]["start"]),
            "end": float(words[-1]["end"]),
            "text": lines_text[line_index],
            "words": words,
            "verified": True,
            "lyrics_source": source,
        })

    return LyricsAlignmentResult(
        lines=lines,
        confidence=confidence,
        coverage=coverage,
        source=source,
        verified=True,
        title=title,
        artist=artist,
    )

def short_transcript_hint(words: list[dict[str, Any]], max_chars: int = 160) -> str:
    parts: list[str] = []
    for w in words:
        token = str(w.get("text") or "").strip()
        if not token:
            continue
        candidate = " ".join(parts + [token])
        if len(candidate) > max_chars:
            break
        parts.append(token)
    return " ".join(parts)
