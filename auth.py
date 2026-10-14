"""CLI helper: run the OAuth flow once to create token.json without the web app.

Useful for "Desktop app" OAuth clients or headless setups:
    python auth.py
Then start the app normally (python app.py) — it picks up token.json.
"""

from google_auth_oauthlib.flow import InstalledAppFlow

import gmail_client


def main():
    flow = InstalledAppFlow.from_client_secrets_file(
        gmail_client.CREDENTIALS_FILE, gmail_client.SCOPES
    )
    creds = flow.run_local_server(port=0, open_browser=True)
    gmail_client.save_credentials(creds)
    print(f"Saved {gmail_client.TOKEN_FILE}")


if __name__ == "__main__":
    main()
