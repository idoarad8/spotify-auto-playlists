import os
import spotipy
from spotipy.oauth2 import SpotifyOAuth


print("Starting Spotify auth test...")

CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI")
REFRESH_TOKEN = os.getenv("SPOTIPY_REFRESH_TOKEN")

SCOPE = "playlist-modify-private playlist-modify-public playlist-read-private"

auth = SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI,
    scope=SCOPE
)

# Inject refresh token manually
auth.token_info = {"refresh_token": REFRESH_TOKEN}

# Attempt to refresh access token
token_info = auth.refresh_access_token(REFRESH_TOKEN)

access_token = token_info.get("access_token")
if not access_token:
    raise Exception("ERROR: refresh token invalid or expired.")

print("Access token retrieved successfully!")

# Create Spotify client
sp = spotipy.Spotify(auth_manager=auth)

# Make a simple API call
me = sp.current_user()

print("Authenticated as:", me["display_name"])
print("User ID:", me["id"])

print("SUCCESS: GitHub Actions can authenticate & call Spotify API!")
