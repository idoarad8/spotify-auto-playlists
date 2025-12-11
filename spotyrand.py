import os
import random
import datetime
import concurrent.futures
import spotipy
from spotipy.oauth2 import SpotifyOAuth

# =============================
# CONFIGURATION
# =============================

CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI")

SCOPE = "playlist-modify-private playlist-modify-public playlist-read-private"

TRACK_COUNT = 50
HEBREW_PERCENT = 0.30

FILTER_LIVE = True
FILTER_REMIX = True
FILTER_KARAOKE = True

# =============================
# PLAYLIST DEFINITIONS
# =============================
PLAYLISTS = {
    "Random Songs A (unknown artists)": {"max": 200, "min": 0},
    "Random Songs B (tiny artists)": {"max": 1000, "min": 200},
    "Random Songs C (small artists)": {"max": 10000, "min": 1000},
    "Random Songs D (medium artists)": {"max": 50000, "min": 10000},
    "Random Songs E (known artists)": {"max": None, "min": 50000},
}

# =============================
# SEEDS — optimized for obscurity + Hebrew
# =============================
HEB_LETTERS = list("אבגדהוזחטיכלמנסעפצקרשת")
HEB_BIGRAMS = ["של", "את", "ים", "אה", "יו", "לי"]
HEBREW_SEEDS = HEB_LETTERS + HEB_BIGRAMS

# Rare + gibberish seeds for obscure artists
GLOBAL_SEEDS = [
    "qz", "zxq", "zzx", "qxx", "zqq", "kjj", "ptk", "xhz",
    "vqx", "zzq", "tzz", "xxa", "mqq", "qvv", "zzp"
]

# =============================
# SPOTIFY AUTH
# =============================

auth = SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI,
    scope=SCOPE
)

# Inject refresh token manually
auth.refresh_token = os.getenv("SPOTIPY_REFRESH_TOKEN")

sp = spotipy.Spotify(auth_manager=auth)


# =============================
# HELPERS
# =============================

def is_hebrew_text(text):
    """Detect Hebrew with Unicode range."""
    return any('\u0590' <= ch <= '\u05FF' for ch in text)


def is_hebrew_track(track):
    """Hebrew if track, album, or artist contains Hebrew."""
    if is_hebrew_text(track["name"]):
        return True
    if is_hebrew_text(track["album"]["name"]):
        return True
    for artist in track["artists"]:
        if is_hebrew_text(artist["name"]):
            return True
    return False


def is_bad_version(name):
    n = name.lower()
    if FILTER_LIVE and ("live" in n or "session" in n):
        return True
    if FILTER_REMIX and ("remix" in n or "mix" in n):
        return True
    if FILTER_KARAOKE and ("karaoke" in n or "instrumental" in n):
        return True
    return False


def pick_seed(hebrew=False):
    return random.choice(HEBREW_SEEDS if hebrew else GLOBAL_SEEDS)


# =============================
# FAST BATCH SEARCH
# =============================

def batch_search_tracks(seed):
    """Fetch 50 tracks in one API call."""
    offset = random.randint(0, 900)
    res = sp.search(q=seed, type="track", limit=50, offset=offset)
    return res["tracks"]["items"]


def batch_fetch_artist_followers(artist_ids):
    """Fetch follower counts for up to 50 artists in one request."""
    artists = sp.artists(artist_ids)["artists"]
    return {a["id"]: a["followers"]["total"] for a in artists}


# =============================
# TRACK GENERATION (FAST)
# =============================

def generate_tracks_for_playlist(max_followers, min_followers):
    hebrew_needed = int(TRACK_COUNT * HEBREW_PERCENT)
    global_needed = TRACK_COUNT - hebrew_needed

    hebrew_tracks = []
    global_tracks = []

    artist_counts = {}   # <-- NEW: store counts per artist (limit = 3)

    while len(hebrew_tracks) < hebrew_needed or len(global_tracks) < global_needed:

        search_hebrew = len(hebrew_tracks) < hebrew_needed
        seed = pick_seed(hebrew=search_hebrew)

        batch = batch_search_tracks(seed)
        if not batch:
            continue

        artist_ids = [t["artists"][0]["id"] for t in batch]
        follower_map = batch_fetch_artist_followers(artist_ids)

        for track in batch:

            if track is None:
                continue

            artist_id = track["artists"][0]["id"]

            # ---------------------------
            # LIMIT: Max 3 songs per artist
            # ---------------------------
            artist_counts.setdefault(artist_id, 0)
            if artist_counts[artist_id] >= 3:
                continue

            # Filters
            if is_bad_version(track["name"]):
                continue

            track_is_hebrew = is_hebrew_track(track)

            # Hebrew/global quota
            if track_is_hebrew:
                if len(hebrew_tracks) >= hebrew_needed:
                    continue
            else:
                if len(global_tracks) >= global_needed:
                    continue

            # Followers check
            followers = follower_map.get(artist_id, 999999)
            if max_followers is not None and followers > max_followers:
                continue
            if min_followers is not None and followers < min_followers:
                continue

            # Accept
            uri = track["uri"]
            artist_counts[artist_id] += 1

            if track_is_hebrew:
                hebrew_tracks.append(uri)
            else:
                global_tracks.append(uri)

            # Stop early if all quotas are satisfied
            if len(hebrew_tracks) >= hebrew_needed and len(global_tracks) >= global_needed:
                break

    final_tracks = hebrew_tracks + global_tracks
    random.shuffle(final_tracks)
    return final_tracks


# =============================
# PLAYLIST MANAGEMENT
# =============================

def find_or_create_playlist(user_id, name):
    playlists = sp.user_playlists(user_id, limit=50)
    for p in playlists["items"]:
        if p["name"].lower() == name.lower():
            return p["id"]
    new_pl = sp.user_playlist_create(user_id, name, public=False)
    return new_pl["id"]


def clear_playlist(pid):
    sp.playlist_replace_items(pid, [])


# =============================
# MULTITHREADED MAIN
# =============================

def process_playlist(args):
    """Generate and update a single playlist."""
    user_id, name, limits, timestamp = args
    max_f = limits["max"]
    min_f = limits["min"]

    pid = find_or_create_playlist(user_id, name)
    clear_playlist(pid)

    tracks = generate_tracks_for_playlist(max_f, min_f)
    sp.playlist_add_items(pid, tracks)

    description = (
        f"Auto-updated at {timestamp}. "
        f"Artist followers: "
        f"{('>' + str(min_f)) if min_f else ''}"
        f"{' and ' if min_f and max_f else ''}"
        f"{('<' + str(max_f)) if max_f else ''}. "
        f"Hebrew % = {int(HEBREW_PERCENT * 100)}%."
    )

    sp.playlist_change_details(pid, description=description)
    print(f"Updated: {name} ({len(tracks)} tracks)")
    return True


def main():
    user_id = sp.current_user()["id"]
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    args_list = [
        (user_id, name, limits, timestamp)
        for name, limits in PLAYLISTS.items()
    ]

    # Run playlists in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        executor.map(process_playlist, args_list)

    print("\nAll playlists updated in parallel!")


if __name__ == "__main__":
    main()


