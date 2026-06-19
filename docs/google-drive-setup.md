# Connect Google to yourOS

This guide walks you through linking your Google account so Drive, Gmail, Calendar, and Gemini AI all work inside yourOS.

## What you need

A Google account and a Google Cloud project with credentials. If you already have a `google_credentials.json` file, skip to step 3.

---

## Step 1: Create a Google Cloud project

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Click **Select a project** at the top, then **New Project**.
3. Give it a name (for example, "yourOS") and click **Create**.

---

## Step 2: Enable the APIs

Inside your project, go to **APIs and Services > Library** and enable each of these:

- Google Drive API
- Google Calendar API
- Gmail API
- Google Slides API (if you use Slides)
- Cloud AI Platform API (for Gemini)

---

## Step 3: Create OAuth credentials

1. Go to **APIs and Services > Credentials**.
2. Click **Create Credentials > OAuth client ID**.
3. Choose **Web application** as the type.
4. Under **Authorized redirect URIs**, add **both** of these:

   ```
   https://localhost:8000/api/auth/google/callback
   https://127.0.0.1:8000/api/auth/google/callback
   ```

   > Both are required. Google treats `localhost` and `127.0.0.1` as different addresses, and your browser may use either one depending on how you opened yourOS.

5. Click **Create**.
6. Download the JSON file and save it to `~/.youros/google_credentials.json`.

---

## Step 4: Connect inside yourOS

Go to **Settings > Connections** (or the Drive, Gmail, or Calendar page) and click **Connect Google**. You will be taken to Google's sign-in screen. After you approve, yourOS stores your token and all Google surfaces become active.

---

## Troubleshooting

**"This URL is not registered" error on the Drive page**

Google rejected the connection because the redirect URI was not added in step 3. Add both URIs listed above to your credentials, then click Connect again.

**"Needs reconnect" showing on Gmail or Calendar after connecting Drive**

This usually means an older token is in place. Click Reconnect on the affected card. One sign-in covers Drive, Gmail, Calendar, and Gemini together.

**Using a reverse proxy or custom domain**

Set the `GOOGLE_REDIRECT_URI` environment variable to the exact URI you registered in Google Cloud Console. yourOS will use that value instead of the auto-detected one.
