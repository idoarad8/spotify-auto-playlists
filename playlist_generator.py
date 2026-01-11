import os
import random
import datetime
import time
from typing import List, Dict, Optional, Set, Tuple

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException

# =========================================================
# ENV / AUTH
# =========================================================
CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI")
REFRESH_TOKEN = os.getenv("SPOTIPY_REFRESH_TOKEN")

if not all([CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, REFRESH_TOKEN]):
    raise RuntimeError(
        "Missing env vars: SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, "
        "SPOTIPY_REDIRECT_URI, SPOTIPY_REFRESH_TOKEN"
    )

SCOPE = "playlist-modify-private playlist-modify-public playlist-read-private"

auth = SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI,
    scope=SCOPE,
)

# Non-interactive auth for GitHub Actions
auth.token_info = {"refresh_token": REFRESH_TOKEN}
auth.refresh_access_token(REFRESH_TOKEN)

sp = spotipy.Spotify(auth_manager=auth)

# =========================================================
# LOGGING
# =========================================================
LOG_EVERY_SEC = 10
API_CALLS = 0

def log(msg: str):
    print(msg, flush=True)

def progress_line(name, heb_cnt, glob_cnt, heb_need, glob_need, api_calls, ind_cnt, max_ind, elapsed):
    log(
        f"[{name}] elapsed={elapsed:.0f}s | "
        f"hebrew={heb_cnt}/{heb_need} global={glob_cnt}/{glob_need} | "
        f"indian={ind_cnt}/{max_ind} | api_calls={api_calls}"
    )

# =========================================================
# USER CONFIG
# =========================================================
TRACK_COUNT = 50
HEBREW_PERCENT = 0.30
MAX_SONGS_PER_ARTIST = 3

FILTER_LIVE = True
FILTER_REMIX = True
FILTER_KARAOKE = True

# Indian caps (tighter for medium and below)
MAX_INDIAN_SMALL_TIERS = 1   # unknown/tiny/small/medium
MAX_INDIAN_KNOWN = 1
MAX_INDIAN_FAMOUS = 0

# Track popularity floors for mainstream tiers
KNOWN_MIN_TRACK_POPULARITY = 45
FAMOUS_MIN_TRACK_POPULARITY = 70

MARKET_DEFAULT = "IL"
MARKET_MAINSTREAM = "US"

# Rate limiting (conservative)
MIN_DELAY_SEC = 0.12
_last_call_ts = 0.0

def rate_limit():
    global _last_call_ts
    now = time.time()
    wait = MIN_DELAY_SEC - (now - _last_call_ts)
    if wait > 0:
        time.sleep(wait)
    _last_call_ts = time.time()

def safe_call(fn, *args, **kwargs):
    """Prevent crashes on intermittent 404/429/5xx. Returns None on error."""
    try:
        return fn(*args, **kwargs)
    except SpotifyException as e:
        log(f"[WARN] SpotifyException {e.http_status}: {e}")
        return None
    except Exception as e:
        log(f"[WARN] Unexpected exception: {e}")
        return None

# =========================================================
# PLAYLIST TIERS (followers)
# =========================================================
PLAYLISTS = {
    "Random Songs (unknown artists)": {"max": 200, "min": 0},
    "Random Songs (tiny artists)": {"max": 1000, "min": 200},
    "Random Songs (small artists)": {"max": 10000, "min": 1000},
    "Random Songs (medium artists)": {"max": 50000, "min": 10000},

    # Known: do NOT cap, mainstream artists are often >>500k
    "Random Songs (known artists)": {"max": None, "min": 50000},

    # Famous: higher threshold so you actually get famous
    "Random Songs (famous artists)": {"max": None, "min": 2000000},
}

# =========================================================
# SEEDS
# =========================================================
HEB_LETTERS = list("אבגדהוזחטיכלמנסעפצקרשת")
HEB_BIGRAMS = ["של", "את", "ים", "אה", "יו", "לי"]
HEBREW_SEEDS = HEB_LETTERS + HEB_BIGRAMS

# Old gibberish seeds (can bias to specific catalogs)
OBSCURE_SEEDS_1 = [
    "qz", "zxq", "zzx", "qxx", "zqq", "kjj", "ptk", "xhz",
    "vqx", "zzq", "tzz", "xxa", "mqq", "qvv", "zzp"
]

# Added: “semi-random” English words + letter pairs to diversify search space
OBSCURE_SEEDS_2 = [
    "wx", "qj", "kp", "zr", "vy", "jt", "lz", "qc",
    "midnight", "plastic", "satellite", "neon", "paper",
    "echo", "hollow", "canyon", "dust", "mirror", "violet",
    "wander", "signal", "orbit", "feather", "cinema"
]

MAINSTREAM_SEEDS = [
    "a", "e", "i", "o", "u",
    "love", "you", "the", "feat", "night", "baby", "dance",
    "2024", "2023", "2022"
]

# Playlist-search keywords for mainstream pool (fallback if no env playlist IDs)
MAINSTREAM_PLAYLIST_QUERIES = [
    "today's top hits",
    "top hits",
    "viral hits",
    "pop rising",
    "mint",
    "rap caviar",
    "rock classics",
    "all out 00s",
    "songs to sing in the car",
    "hot hits",
    "global top 50",
    "new music friday",
]

# =========================================================
# DIVERSITY / FILTER HELPERS
# =========================================================
def is_hebrew_text(text: str) -> bool:
    return any("\u0590" <= ch <= "\u05FF" for ch in (text or ""))

def is_hebrew_track(track) -> bool:
    if is_hebrew_text(track.get("name", "")):
        return True
    album = track.get("album") or {}
    if is_hebrew_text(album.get("name", "")):
        return True
    for a in track.get("artists") or []:
        if is_hebrew_text(a.get("name", "")):
            return True
    return False

def is_bad_version(name: str) -> bool:
    n = (name or "").lower()
    if FILTER_LIVE and (" live" in n or "(live" in n or "session" in n):
        return True
    if FILTER_REMIX and (" remix" in n or "(remix" in n or " edit" in n):
        return True
    if FILTER_KARAOKE and ("karaoke" in n or "instrumental" in n):
        return True
    return False

INDIAN_GENRE_KEYWORDS = {
    "bollywood", "desi", "indian", "filmi", "tollywood",
    "punjabi", "bhangra", "tamil", "telugu", "malayalam",
    "kannada", "bengali", "gujarati", "hindi", "urdu"
}
INDIAN_TEXT_KEYWORDS = {
    "bollywood", "t-series", "tseries", "desi", "filmi", "tollywood",
    "punjabi", "bhangra", "hindi", "urdu",
    "tamil", "telugu", "malayalam", "kannada", "bengali", "gujarati",
    # common high-frequency names that dominate results
    "arijit", "pritam", "shreya", "atif", "rahat", "neha", "badshah"
}

def has_indic_script(text: str) -> bool:
    if not text:
        return False
    return any(
        ("\u0900" <= ch <= "\u097F") or  # Devanagari
        ("\u0980" <= ch <= "\u09FF") or  # Bengali
        ("\u0A00" <= ch <= "\u0A7F") or  # Gurmukhi
        ("\u0A80" <= ch <= "\u0AFF") or  # Gujarati
        ("\u0B80" <= ch <= "\u0BFF") or  # Tamil
        ("\u0C00" <= ch <= "\u0C7F") or  # Telugu
        ("\u0C80" <= ch <= "\u0CFF") or  # Kannada
        ("\u0D00" <= ch <= "\u0D7F")     # Malayalam
        for ch in text
    )

def is_indian_track(track, artist_obj) -> bool:
    # Indic scripts
    if has_indic_script(track.get("name", "")):
        return True
    album = track.get("album") or {}
    if has_indic_script(album.get("name", "")):
        return True
    if has_indic_script(artist_obj.get("name", "")):
        return True

    # text keywords
    t = (track.get("name") or "").lower()
    a = (album.get("name") or "").lower()
    an = (artist_obj.get("name") or "").lower()
    if any(k in t for k in INDIAN_TEXT_KEYWORDS):
        return True
    if any(k in a for k in INDIAN_TEXT_KEYWORDS):
        return True
    if any(k in an for k in INDIAN_TEXT_KEYWORDS):
        return True

    # genres
    genres = " ".join(artist_obj.get("genres", [])).lower()
    return any(k in genres for k in INDIAN_GENRE_KEYWORDS)

def pick_seed(require_hebrew: bool, mainstream: bool) -> str:
    if require_hebrew:
        return random.choice(HEBREW_SEEDS)
    if mainstream:
        return random.choice(MAINSTREAM_SEEDS)
    return random.choice(OBSCURE_SEEDS_1 + OBSCURE_SEEDS_2)

# =========================================================
# SPOTIFY API HELPERS (BATCHED)
# =========================================================
def batch_search_tracks(seed: str, market: str) -> List[dict]:
    """Search-based sampler (good for obscure + Hebrew)."""
    global API_CALLS
    offset = random.randint(0, 900)

    # query-level filtering
    q = f'{seed} -live -karaoke -instrumental -remix -edit'

    # stronger negative filters to reduce India-heavy search results
    q += (
        " -bollywood -punjabi -hindi -tamil -telugu -bhangra -desi -filmi"
        " -t-series -tseries -arijit -pritam -shreya -atif -rahat -neha -badshah"
    )

    rate_limit()
    API_CALLS += 1
    res = safe_call(sp.search, q=q, type="track", limit=50, offset=offset, market=market)
    if not res:
        return []
    return res.get("tracks", {}).get("items", []) or []

def batch_fetch_artist_info(artist_ids: List[str]) -> Dict[str, dict]:
    """Fetch followers + genres + name for up to 50 artists in one request."""
    global API_CALLS

    uniq = []
    seen = set()
    for aid in artist_ids:
        if aid and aid not in seen:
            seen.add(aid)
            uniq.append(aid)
        if len(uniq) >= 50:
            break

    if not uniq:
        return {}

    rate_limit()
    API_CALLS += 1
    res = safe_call(sp.artists, uniq)
    if not res:
        return {}

    artists = res.get("artists", []) or []
    info = {}
    for a in artists:
        info[a["id"]] = {
            "followers": (a.get("followers") or {}).get("total", 999999),
            "genres": a.get("genres", []) or [],
            "name": a.get("name", "") or "",
        }
    return info

# =========================================================
# MAINSTREAM PLAYLIST POOL (NO BROWSE ENDPOINTS)
# =========================================================
def extract_playlist_id(s: str) -> Optional[str]:
    """
    Accepts:
      - raw playlist ID
      - https://open.spotify.com/playlist/<id>?si=...
      - spotify:playlist:<id>
    """
    if not s:
        return None
    s = s.strip()
    if s.startswith("spotify:playlist:"):
        return s.split(":")[-1].strip() or None
    if "open.spotify.com/playlist/" in s:
        part = s.split("open.spotify.com/playlist/")[-1]
        part = part.split("?")[0].split("/")[0]
        return part.strip() or None
    # assume it's already an ID
    return s if len(s) >= 10 else None

def parse_env_playlist_ids() -> List[str]:
    """
    Optional: set MAINSTREAM_PLAYLIST_IDS as comma-separated playlist URLs or IDs.
    Example:
      MAINSTREAM_PLAYLIST_IDS="https://open.spotify.com/playlist/4IY0u7K6Jj5rPy5pIVCuGp,37i9dQZF1DXcBWIGoYBM5M"
    """
    raw = (os.getenv("MAINSTREAM_PLAYLIST_IDS") or "").strip()
    if not raw:
        return []
    ids = []
    for part in raw.split(","):
        pid = extract_playlist_id(part)
        if pid:
            ids.append(pid)
    # dedup while keeping order
    out = []
    seen = set()
    for pid in ids:
        if pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out

def search_mainstream_playlist_ids(market: str, limit_per_query: int = 10) -> List[str]:
    """Fallback: find playlist IDs via search."""
    global API_CALLS
    ids: List[str] = []
    seen: Set[str] = set()

    for q in MAINSTREAM_PLAYLIST_QUERIES:
        rate_limit()
        API_CALLS += 1
        res = safe_call(sp.search, q=q, type="playlist", limit=limit_per_query, market=market)
        if not res:
            continue
        items = res.get("playlists", {}).get("items", []) or []
        for p in items:
            pid = (p or {}).get("id")
            if pid and pid not in seen:
                seen.add(pid)
                ids.append(pid)
        if len(ids) >= 50:
            break
    return ids

def get_mainstream_playlist_pool() -> List[str]:
    env_ids = parse_env_playlist_ids()
    if env_ids:
        log(f"[MainstreamPool] Using {len(env_ids)} playlist IDs from MAINSTREAM_PLAYLIST_IDS env.")
        return env_ids

    ids = search_mainstream_playlist_ids(market=MARKET_MAINSTREAM, limit_per_query=10)
    log(f"[MainstreamPool] Found {len(ids)} playlist IDs via search fallback.")
    return ids

def get_random_tracks_from_playlist(pid: str) -> List[dict]:
    """Fetch a chunk of tracks from a playlist and return track objects."""
    global API_CALLS
    offset = random.choice([0, 25, 50, 75, 100])

    rate_limit()
    API_CALLS += 1
    data = safe_call(sp.playlist_items, pid, limit=50, offset=offset, additional_types=("track",))
    if not data:
        return []

    items = data.get("items", []) or []
    tracks = []
    for it in items:
        t = (it or {}).get("track")
        if t and t.get("uri") and t.get("artists"):
            tracks.append(t)
    return tracks

# =========================================================
# TRACK GENERATION
# =========================================================
def generate_tracks_for_playlist(max_followers, min_followers, playlist_name=""):
    start = time.time()
    last_log = start

    HARD_TIMEOUT_SEC = 240
    MAX_TOTAL_ITERATIONS = 4500

    hebrew_needed = int(TRACK_COUNT * HEBREW_PERCENT)
    global_needed = TRACK_COUNT - hebrew_needed

    hebrew_tracks: List[str] = []
    global_tracks: List[str] = []

    artist_counts: Dict[str, int] = {}
    seen_uris: Set[str] = set()
    seen_artist_title: Set[Tuple[str, str]] = set()

    mainstream_mode = (min_followers is not None and min_followers >= 50000)

    # popularity floor (only for non-Hebrew)
    min_popularity = None
    if mainstream_mode:
        min_popularity = (
            FAMOUS_MIN_TRACK_POPULARITY
            if (min_followers and min_followers >= 2000000)
            else KNOWN_MIN_TRACK_POPULARITY
        )

    # Indian caps by tier
    if min_followers is not None and min_followers >= 2000000:
        max_indian = MAX_INDIAN_FAMOUS
    elif mainstream_mode:
        max_indian = MAX_INDIAN_KNOWN
    else:
        max_indian = MAX_INDIAN_SMALL_TIERS

    indian_count = 0
    mainstream_pool: Optional[List[str]] = None

    rejects = {
        "dup_uri": 0,
        "dup_title": 0,
        "artist_cap": 0,
        "bad_version": 0,
        "hebrew_quota_full": 0,
        "global_quota_full": 0,
        "followers": 0,
        "popularity": 0,
        "indian_cap": 0,
        "missing_data": 0,
        "timeout": 0,
        "no_batch": 0,
        "pool_empty": 0,
        "batch_india_skip": 0,
    }

    iters = 0
    while len(hebrew_tracks) < hebrew_needed or len(global_tracks) < global_needed:
        iters += 1
        now = time.time()

        if now - start > HARD_TIMEOUT_SEC or iters > MAX_TOTAL_ITERATIONS:
            rejects["timeout"] += 1
            log(
                f"[{playlist_name}] TIMEOUT/STOP. "
                f"hebrew={len(hebrew_tracks)}/{hebrew_needed}, "
                f"global={len(global_tracks)}/{global_needed}, "
                f"elapsed={now - start:.1f}s, iters={iters}"
            )
            break

        if now - last_log >= LOG_EVERY_SEC:
            elapsed = now - start
            progress_line(
                playlist_name,
                len(hebrew_tracks), len(global_tracks),
                hebrew_needed, global_needed,
                API_CALLS,
                indian_count, max_indian,
                elapsed
            )
            top = sorted(rejects.items(), key=lambda x: x[1], reverse=True)[:5]
            log(f"[{playlist_name}] top_rejects: " + ", ".join([f"{k}={v}" for k, v in top]))
            last_log = now

        need_hebrew_now = len(hebrew_tracks) < hebrew_needed

        # Strategy:
        # - Hebrew: search in IL market
        # - Known/Famous global: sample from mainstream playlists (env or search)
        if mainstream_mode and not need_hebrew_now:
            if mainstream_pool is None:
                mainstream_pool = get_mainstream_playlist_pool()
            if not mainstream_pool:
                rejects["pool_empty"] += 1
                seed = pick_seed(require_hebrew=False, mainstream=True)
                batch = batch_search_tracks(seed, market=MARKET_MAINSTREAM)
            else:
                pid = random.choice(mainstream_pool)
                batch = get_random_tracks_from_playlist(pid)
        else:
            seed = pick_seed(require_hebrew=need_hebrew_now, mainstream=mainstream_mode)
            batch = batch_search_tracks(seed, market=MARKET_DEFAULT)

        if not batch:
            rejects["no_batch"] += 1
            continue

        artist_ids = [t["artists"][0]["id"] for t in batch if t and t.get("artists")]
        artist_map = batch_fetch_artist_info(artist_ids)

        # Fast skip India-heavy batches for medium and below (saves time + improves output)
        if not mainstream_mode:
            indian_hits = 0
            checked = 0
            for t in batch[:15]:
                if not t or not t.get("artists"):
                    continue
                aid = t["artists"][0].get("id")
                aobj = artist_map.get(aid, {"genres": [], "name": ""})
                if is_indian_track(t, aobj):
                    indian_hits += 1
                checked += 1
            if checked >= 8 and (indian_hits / checked) > 0.30:
                rejects["batch_india_skip"] += 1
                continue

        for track in batch:
            if not track or not track.get("artists"):
                rejects["missing_data"] += 1
                continue

            uri = track.get("uri")
            if not uri:
                rejects["missing_data"] += 1
                continue
            if uri in seen_uris:
                rejects["dup_uri"] += 1
                continue

            title = (track.get("name") or "").strip()
            if not title:
                rejects["missing_data"] += 1
                continue
            if is_bad_version(title):
                rejects["bad_version"] += 1
                continue

            artist_id = track["artists"][0].get("id")
            if not artist_id:
                rejects["missing_data"] += 1
                continue

            if artist_counts.get(artist_id, 0) >= MAX_SONGS_PER_ARTIST:
                rejects["artist_cap"] += 1
                continue

            title_key = (artist_id, title.lower())
            if title_key in seen_artist_title:
                rejects["dup_title"] += 1
                continue

            track_is_hebrew = is_hebrew_track(track)

            if track_is_hebrew and len(hebrew_tracks) >= hebrew_needed:
                rejects["hebrew_quota_full"] += 1
                continue
            if (not track_is_hebrew) and len(global_tracks) >= global_needed:
                rejects["global_quota_full"] += 1
                continue

            artist_obj = artist_map.get(artist_id, {"followers": 999999, "genres": [], "name": ""})
            followers = artist_obj["followers"]

            # follower constraints:
            # apply to non-Hebrew in mainstream tiers; always apply in non-mainstream tiers
            apply_follower_constraints = (not mainstream_mode) or (not track_is_hebrew)
            if apply_follower_constraints:
                if max_followers is not None and followers > max_followers:
                    rejects["followers"] += 1
                    continue
                if min_followers is not None and followers < min_followers:
                    rejects["followers"] += 1
                    continue

            # popularity floor only for non-Hebrew
            if (min_popularity is not None) and (not track_is_hebrew) and ((track.get("popularity") or 0) < min_popularity):
                rejects["popularity"] += 1
                continue

            indian_flag = is_indian_track(track, artist_obj)
            if indian_flag and indian_count >= max_indian:
                rejects["indian_cap"] += 1
                continue

            # ACCEPT
            seen_uris.add(uri)
            seen_artist_title.add(title_key)
            artist_counts[artist_id] = artist_counts.get(artist_id, 0) + 1
            if indian_flag:
                indian_count += 1

            if track_is_hebrew:
                hebrew_tracks.append(uri)
            else:
                global_tracks.append(uri)

            if len(hebrew_tracks) >= hebrew_needed and len(global_tracks) >= global_needed:
                break

    elapsed = time.time() - start
    log(
        f"[{playlist_name}] DONE in {elapsed:.1f}s | "
        f"hebrew={len(hebrew_tracks)}/{hebrew_needed} global={len(global_tracks)}/{global_needed} | "
        f"api_calls={API_CALLS}"
    )

    final_tracks = hebrew_tracks + global_tracks
    random.shuffle(final_tracks)

    if len(final_tracks) < TRACK_COUNT:
        log(f"[{playlist_name}] WARNING: only collected {len(final_tracks)}/{TRACK_COUNT} tracks due to constraints.")

    return final_tracks[:TRACK_COUNT]

# =========================================================
# PLAYLIST MANAGEMENT
# =========================================================
def find_or_create_playlist(user_id: str, name: str) -> str:
    global API_CALLS
    rate_limit()
    API_CALLS += 1
    playlists = safe_call(sp.user_playlists, user_id, limit=50)
    if playlists:
        for p in playlists.get("items", []) or []:
            if (p.get("name") or "").lower() == name.lower():
                return p["id"]

    rate_limit()
    API_CALLS += 1
    new_pl = safe_call(sp.user_playlist_create, user_id, name, public=False)
    if not new_pl or not new_pl.get("id"):
        raise RuntimeError(f"Failed to create playlist: {name}")
    return new_pl["id"]

def clear_playlist(pid: str):
    global API_CALLS
    rate_limit()
    API_CALLS += 1
    safe_call(sp.playlist_replace_items, pid, [])

def process_playlist(user_id: str, name: str, limits: dict, timestamp: str):
    global API_CALLS
    max_f = limits["max"]
    min_f = limits["min"]

    log(f"\n=== START {name} | followers min={min_f} max={max_f} ===")

    tracks = generate_tracks_for_playlist(max_f, min_f, playlist_name=name)

    if not tracks:
        log(f"=== END {name} | ERROR: generated 0 tracks. Skipping playlist update. ===")
        return

    pid = find_or_create_playlist(user_id, name)

    # Clear only after we have tracks
    clear_playlist(pid)

    rate_limit()
    API_CALLS += 1
    res = safe_call(sp.playlist_add_items, pid, tracks)
    if not res:
        log(f"[WARN] Failed adding tracks to {name} (pid={pid}). Skipping description update.")
        return

    description = (
        f"Auto-updated at {timestamp}. "
        f"Followers: "
        f"{('>' + str(min_f)) if min_f else ''}"
        f"{' and ' if min_f and max_f else ''}"
        f"{('<' + str(max_f)) if max_f else ''}. "
        f"Hebrew % = {int(HEBREW_PERCENT * 100)}%. "
        f"Max {MAX_SONGS_PER_ARTIST} songs/artist."
    )

    rate_limit()
    API_CALLS += 1
    safe_call(sp.playlist_change_details, pid, description=description)

    log(f"=== END {name} | added={len(tracks)} | total_api_calls={API_CALLS} ===")

# =========================================================
# MAIN (SEQUENTIAL)
# =========================================================
def main():
    global API_CALLS
    rate_limit()
    API_CALLS += 1
    me = safe_call(sp.current_user)
    if not me or not me.get("id"):
        raise RuntimeError("Failed to read current user. Check token/scopes.")
    user_id = me["id"]

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for name, limits in PLAYLISTS.items():
        process_playlist(user_id, name, limits, timestamp)

    log("\nAll playlists updated!")

if __name__ == "__main__":
    main()
