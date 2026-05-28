# Connect Google Drive to yourOS

This guide walks you through getting a credentials file from Google and saving it so yourOS can connect to your Drive.

---

## What you need

A "credentials file" from Google Cloud Console. This file lets yourOS sign in to Google on your behalf. It is stored only on your computer and never sent anywhere.

---

## Step 1: Create a Google Cloud project

1. Go to [https://console.cloud.google.com](https://console.cloud.google.com).
2. Click the project dropdown at the top and choose **New Project**.
3. Give it any name (e.g. "yourOS Drive") and click **Create**.

---

## Step 2: Enable the Google Drive API

1. In your new project, go to **APIs and Services > Library**.
2. Search for "Google Drive API" and click it.
3. Click **Enable**.

---

## Step 3: Set up the OAuth consent screen

1. Go to **APIs and Services > OAuth consent screen**.
2. Choose **External** and click **Create**.
3. Fill in the required fields (App name, support email). You can leave the rest blank.
4. Click **Save and Continue** through the remaining steps.
5. On the **Test users** step, add your own Google email address. This lets you sign in while the app is in testing mode.

---

## Step 4: Create OAuth credentials

1. Go to **APIs and Services > Credentials**.
2. Click **Create Credentials > OAuth client ID**.
3. Choose **Web application** as the application type (not "Desktop app").
4. Give it a name (e.g. "yourOS Drive").
5. Under **Authorized redirect URIs**, click **Add URI** and enter exactly:
   ```
   http://localhost:37373/api/drive/auth/callback
   ```
   This must match exactly (no trailing slash, port 37373). If it does not match, Google will reject the sign-in with a "redirect_uri_mismatch" error.
6. Click **Create**.
7. A dialog will appear. Click **Download JSON**.

---

## Step 5: Save the file

Move the downloaded file to:

```
~/.myos/google_credentials.json
```

On a Mac, open Terminal and run:

```bash
mv ~/Downloads/client_secret_*.json ~/.myos/google_credentials.json
```

If the `~/.myos` folder does not exist yet, create it first:

```bash
mkdir -p ~/.myos
```

---

## Step 6: Connect in yourOS

Go to the **Drive** tab in yourOS and click **Connect your Google account**. A Google sign-in page will open in your browser. Sign in and grant the requested permission (read-only access to your Drive files).

Once you sign in, yourOS will sync your file list and you can start browsing.

---

## Notes

- yourOS only requests **read-only** access. It cannot add, edit, or delete any files.
- Your token is saved locally at `~/.myos/google_token.json`. To disconnect, click **Disconnect** in the Drive tab.
- File previews are cached for 1 hour at `~/.myos/drive_cache/`. You can delete this folder at any time to clear the cache.
