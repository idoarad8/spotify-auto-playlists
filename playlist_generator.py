import os
import random
import datetime
import time
import spotipy
from spotipy.oauth2 import SpotifyOAuth

# =========================================================
# ENV / AUTH
# =========================================================
CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI")
REFRESH_TOKEN = os.getenv("SPOTIPY_REFRESH_TOKEN")

if not all([CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, REFRESH_TOKEN]):
    raise RuntimeError(
        "Missing one or more env vars: SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, "
        "SPOTIPY_REDIRECT_URI, SPOTIPY_REFRESH_TOKEN"
    )

SCOPE = "playlist-modify-private playlist-modify-public playlist-read-private"

auth = SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI,
    scope=SCOPE,
)

# Inject refresh token and refresh once (non-interactive; works on GitHub Actions)
auth.token_info = {"refresh_token": REFRESH_TOKEN}
auth.refresh_access_token(REFRESH_TOKEN)

sp = spotipy.Spotify(auth_manager=auth)

# =========================================================
# LOGGING
# =========================================================
LOG_EVERY_SEC = 10  # print a progress line at least every 10s
API_CALLS = 0

def log(msg: str):
    # flush=True is important for GitHub Actions (ensures you see logs live)
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

# Reduce Indian-heavy output by capping Indian-tagged tracks per playlist
MAX_INDIAN_PERCENT = 0.06  # 6% of 50 -> max 3

# "Famous" tiers: enforce some minimum track popularity (0–100)
KNOWN_MIN_TRACK_POPULARITY = 55
FAMOUS_MIN_TRACK_POPULARITY = 70

# Market affects what Spotify returns. Using US for mainstream tiers tends to be more global.
MARKET_DEFAULT = "IL"
MARKET_MAINSTREAM = "US"

# Rate limiting (prevents 429 loops). Keep conservative.
MIN_DELAY_SEC = 0.12  # ~8 requests/sec
_last_call_ts = 0.0

def rate_limit():
    global _last_call_ts
    now = time.time()
    wait = MIN_DELAY_SEC - (now - _last_call_ts)
    if wait > 0:
        time.sleep(wait)
    _last_call_ts = time.time()

# =========================================================
# PLAYLIST TIERS (followers)
# =========================================================
PLAYLISTS = {
    "Random Songs (unknown artists)": {"max": 200, "min": 0},
    "Random Songs (tiny artists)": {"max": 1000, "min": 200},
    "Random Songs (small artists)": {"max": 10000, "min": 1000},
    "Random Songs (medium artists)": {"max": 50000, "min": 10000},
    "Random Songs (known artists)": {"max": 500000, "min": 50000},
    "Random Songs (famous artists)": {"max": None, "min": 500000},
}

# =========================================================
# SEEDS
# =========================================================
HEB_LETTERS = list("אבגדהוזחטיכלמנסעפצקרשת")
HEB_BIGRAMS = ["של", "את", "ים", "אה", "יו", "לי"]
HEBREW_SEEDS = HEB_LETTERS + HEB_BIGRAMS

# Obscure seeds (good for unknown/small tiers)
OBSCURE_SEEDS = [
    "qz", "zxq", "zzx", "qxx", "zqq", "kjj", "ptk", "xhz",
    "vqx", "zzq", "tzz", "xxa", "mqq", "qvv", "zzp"
]

# Mainstream seeds (good for known/famous tiers)
MAINSTREAM_SEEDS = [
    "a", "e", "i", "o", "u",
    "love", "you", "the", "feat",
    "2024", "2023", "2022",
    "rem", "ver", "mix"
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
    if FILTER_REMIX and ("remix" in n or " mix" in n or "(mix" in n):
        return True
    if FILTER_KARAOKE and ("karaoke" in n or "instrumental" in n):
        return True
    return False

# Indian detection (practical heuristic)
INDIAN_GENRE_KEYWORDS = {
    "bollywood", "desi", "indian", "filmi", "tollywood",
    "punjabi", "bhangra", "tamil", "telugu", "malayalam",
    "kannada", "bengali", "gujarati", "hindi", "urdu"
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
    if has_indic_script(track.get("name", "")):
        return True
    album = track.get("album") or {}
    if has_indic_script(album.get("name", "")):
        return True
    if has_indic_script(artist_obj.get("name", "")):
        return True
    genres = " ".join(artist_obj.get("genres", [])).lower()
    return any(k in genres for k in INDIAN_GENRE_KEYWORDS)

def pick_seed(require_hebrew: bool, mainstream: bool) -> str:
    if require_hebrew:
        return random.choice(HEBREW_SEEDS)
    return random.choice(MAINSTREAM_SEEDS if mainstream else OBSCURE_SEEDS)

# =========================================================
# SPOTIFY API HELPERS (BATCHED)
# =========================================================
def batch_search_tracks(seed: str, market: str) -> list:
    """Fetch up to 50 tracks in one API call (with query-level filtering)."""
    global API_CALLS
    offset = random.randint(0, 900)
    q = f'{seed} -live -karaoke -instrumental -remix'
    rate_limit()
    API_CALLS += 1
    res = sp.search(q=q, type="track", limit=50, offset=offset, market=market)
    return res.get("tracks", {}).get("items", []) or []

def batch_fetch_artist_info(artist_ids: list) -> dict:
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

    rate_limit()
    API_CALLS += 1
    artists = sp.artists(uniq).get("artists", []) or []
    info = {}
    for a in artists:
        info[a["id"]] = {
            "followers": (a.get("followers") or {}).get("total", 999999),
            "genres": a.get("genres", []) or [],
            "name": a.get("name", "") or "",
        }
    return info

# =========================================================
# TRACK GENERATION (with progress logs + rejection stats)
# =========================================================
def generate_tracks_for_playlist(max_followers, min_followers, playlist_name=""):
    start = time.time()
    last_log = start

    hebrew_needed = int(TRACK_COUNT * HEBREW_PERCENT)
    global_needed = TRACK_COUNT - hebrew_needed

    hebrew_tracks = []
    global_tracks = []

    artist_counts = {}          # artist_id -> count (max MAX_SONGS_PER_ARTIST)
    seen_uris = set()           # avoid duplicate tracks
    seen_artist_title = set()   # avoid same artist + same title duplicates

    max_indian = int(TRACK_COUNT * MAX_INDIAN_PERCENT)
    indian_count = 0

    mainstream_mode = (min_followers is not None and min_followers >= 50000)
    market = MARKET_MAINSTREAM if mainstream_mode else MARKET_DEFAULT

    min_popularity = None
    if mainstream_mode:
        min_popularity = (
            FAMOUS_MIN_TRACK_POPULARITY
            if (min_followers and min_followers >= 500000)
            else KNOWN_MIN_TRACK_POPULARITY
        )

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
    }

    while len(hebrew_tracks) < hebrew_needed or len(global_tracks) < global_needed:
        now = time.time()
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
            top = sorted(rejects.items(), key=lambda x: x[1], reverse=True)[:3]
            log(f"[{playlist_name}] top_rejects: " + ", ".join([f"{k}={v}" for k, v in top]))
            last_log = now

        need_hebrew_now = len(hebrew_tracks) < hebrew_needed
        seed = pick_seed(require_hebrew=need_hebrew_now, mainstream=mainstream_mode)

        batch = batch_search_tracks(seed, market=market)
        if not batch:
            continue

        artist_ids = [t["artists"][0]["id"] for t in batch if t and t.get("artists")]
        artist_map = batch_fetch_artist_info(artist_ids)

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

            if max_followers is not None and followers > max_followers:
                rejects["followers"] += 1
                continue
            if min_followers is not None and followers < min_followers:
                rejects["followers"] += 1
                continue

            if min_popularity is not None and (track.get("popularity") or 0) < min_popularity:
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
        f"hebrew={len(hebrew_tracks)} global={len(global_tracks)} | "
        f"api_calls={API_CALLS}"
    )

    final_tracks = hebrew_tracks + global_tracks
    random.shuffle(final_tracks)
    return final_tracks

# =========================================================
# PLAYLIST MANAGEMENT
# =========================================================
def find_or_create_playlist(user_id: str, name: str) -> str:
    global API_CALLS
    rate_limit()
    API_CALLS += 1
    playlists = sp.user_playlists(user_id, limit=50)
    for p in playlists.get("items", []) or []:
        if (p.get("name") or "").lower() == name.lower():
            return p["id"]
    rate_limit()
    API_CALLS += 1
    new_pl = sp.user_playlist_create(user_id, name, public=False)
    return new_pl["id"]

def clear_playlist(pid: str):
    global API_CALLS
    rate_limit()
    API_CALLS += 1
    sp.playlist_replace_items(pid, [])

def process_playlist(user_id: str, name: str, limits: dict, timestamp: str):
    global API_CALLS
    max_f = limits["max"]
    min_f = limits["min"]

    log(f"\n=== START {name} | followers min={min_f} max={max_f} ===")

    pid = find_or_create_playlist(user_id, name)
    clear_playlist(pid)

    tracks = generate_tracks_for_playlist(max_f, min_f, playlist_name=name)

    rate_limit()
    API_CALLS += 1
    sp.playlist_add_items(pid, tracks)

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
    sp.playlist_change_details(pid, description=description)

    log(f"=== END {name} | added={len(tracks)} | total_api_calls={API_CALLS} ===")

# =========================================================
# MAIN (SEQUENTIAL: avoids rate-limit hangs)
# =========================================================
def main():
    global API_CALLS
    rate_limit()
    API_CALLS += 1
    user_id = sp.current_user()["id"]
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for name, limits in PLAYLISTS.items():
        process_playlist(user_id, name, limits, timestamp)

    log("\nAll playlists updated!")

if __name__ == "__main__":
    main()
