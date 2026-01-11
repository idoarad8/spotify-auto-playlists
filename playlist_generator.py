def generate_tracks_for_playlist(max_followers, min_followers, playlist_name=""):
    start = time.time()
    last_log = start

    HARD_TIMEOUT_SEC = 180  # prevents infinite runs
    MAX_TOTAL_ITERATIONS = 2000  # extra safety

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
        "timeout": 0,
    }

    iters = 0
    while len(hebrew_tracks) < hebrew_needed or len(global_tracks) < global_needed:
        iters += 1
        now = time.time()

        # --- hard stop conditions ---
        if now - start > HARD_TIMEOUT_SEC or iters > MAX_TOTAL_ITERATIONS:
            rejects["timeout"] += 1
            log(
                f"[{playlist_name}] TIMEOUT/STOP. "
                f"hebrew={len(hebrew_tracks)}/{hebrew_needed}, "
                f"global={len(global_tracks)}/{global_needed}, "
                f"elapsed={now - start:.1f}s, iters={iters}"
            )
            break

        # --- periodic logs ---
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

            # ------------------------------------------------------
            # KEY FIX:
            # In known/famous tiers, apply follower thresholds ONLY to
            # the non-Hebrew part. Hebrew part can be any size.
            # This avoids impossible "15 Hebrew songs from 500k+ artists".
            # ------------------------------------------------------
            apply_follower_constraints = (not mainstream_mode) or (not track_is_hebrew)

            if apply_follower_constraints:
                if max_followers is not None and followers > max_followers:
                    rejects["followers"] += 1
                    continue
                if min_followers is not None and followers < min_followers:
                    rejects["followers"] += 1
                    continue

            # popularity floor in mainstream tiers
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
        f"hebrew={len(hebrew_tracks)}/{hebrew_needed} global={len(global_tracks)}/{global_needed} | "
        f"api_calls={API_CALLS}"
    )

    final_tracks = hebrew_tracks + global_tracks
    random.shuffle(final_tracks)

    # If we timed out, pad to TRACK_COUNT with whatever we have (still diverse),
    # so playlist updates don't fail.
    if len(final_tracks) < TRACK_COUNT:
        log(f"[{playlist_name}] WARNING: only collected {len(final_tracks)}/{TRACK_COUNT} tracks due to constraints.")

    return final_tracks[:TRACK_COUNT]
