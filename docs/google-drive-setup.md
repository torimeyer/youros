# Connect Google to yourOS

This guide walks you through getting a credentials file from Google and connecting it in yourOS. You do not need a terminal.

---

## What you need

A "credentials file" from Google Cloud Console. This file lets yourOS sign in to Google on your behalf. It stays on your computer and is never sent anywhere.

---

## Step 1: Create a Google Cloud project

1. Go to [https://console.cloud.google.com](https://console.cloud.google.com).
2. Click the project dropdown at the top and choose **New Project**.
3. Give it any name (e.g. "yourOS") and click **Create**.

## Step 2: Turn on the APIs you want

1. In your new project, go to **APIs and Services > Library**.
2. Search for and enable each of these (click the API, then click **Enable**):
   - Google Drive API
   - Google Calendar API
   - Gmail API

## Step 3: Set up the OAuth consent screen

1. Go to **APIs and Services > OAuth consent screen**.
2. Choose **External** and click **Create**.
3. Fill in an app name (e.g. "yourOS") and your email. Leave the rest blank.
4. Click **Save and Continue** through each section until you reach **Test users**.
5. On the **Test users** step, click **Add users** and add your own Google email address.
6. Finish and save.

**Important: Publish your app so it does not expire every 7 days.**

While your app is in testing mode, Google disconnects it every 7 days. To fix this permanently:

1. Go back to **OAuth consent screen**.
2. Click **Publish App** and confirm.

You do not need Google's review. For a personal app used only by you, publishing skips review automatically.

## Step 4: Create OAuth credentials

1. Go to **APIs and Services > Credentials**.
2. Click **Create Credentials > OAuth client ID**.
3. Choose **Desktop app** as the application type.
4. Give it a name (e.g. "yourOS Drive") and click **Create**.
5. Click **Download JSON** in the dialog that appears.

## Step 5: Upload the file in yourOS

Open yourOS and go to the Google connect screen (in onboarding, or the Drive, Calendar, or Gmail tab). Drag and drop the JSON file you downloaded, or click the upload area to browse for it.

Once uploaded, yourOS will take you straight to Google sign-in.

---

## Notes

- Your credentials file and sign-in token are saved locally at `~/.youros/`. yourOS never sends them anywhere.
- To disconnect, click **Disconnect** in the Drive tab.
- File previews are cached for 1 hour at `~/.youros/drive_cache/`. Delete this folder at any time to clear the cache.
