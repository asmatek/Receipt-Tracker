# Upgrade the live Streamlit app to Receipt Tracker 2.0

## A. Update GitHub

1. Extract the new ZIP.
2. Open the extracted `receipt-processing-tool` folder.
3. In the existing GitHub `Receipt-Tracker` repository, click **Add file → Upload files**.
4. Drag everything inside the extracted folder into the upload page.
5. Confirm that GitHub reports replacements for existing files and additions for new files.
6. Commit directly to `main` with the message `Upgrade to Supabase receipt tracker`.

Required new or changed files include:

```text
streamlit_app.py
requirements.txt
packages.txt
supabase_setup.sql
src/receipt_processor/__init__.py
src/receipt_processor/tracker.py
src/receipt_processor/core.py
```

Do not upload a real `.streamlit/secrets.toml` file.

## B. Create the Supabase database

1. Go to <https://supabase.com/> and create an account or sign in.
2. Click **New project**.
3. Choose an organization, enter a project name such as `receipt-tracker`, generate a strong database password, and choose the closest region.
4. Wait for the project to finish provisioning.
5. Open **SQL Editor → New query**.
6. Open `supabase_setup.sql` from the downloaded package, copy all of it, paste it into the SQL editor, and click **Run**.
7. Confirm that the query completes without an error.

## C. Copy Supabase credentials

1. In Supabase, open **Project Settings → API Keys**.
2. Copy the project URL.
3. Copy the anon/publishable key.
4. Reveal and copy the service-role/secret key.

The service-role key has administrative access. Never put it in GitHub, email, screenshots, or chat messages.

## D. Add Streamlit Secrets

1. Return to the deployed Streamlit app.
2. Click **Manage app → Settings → Secrets**.
3. Paste the following, replacing every placeholder:

```toml
[supabase]
url = "https://YOUR-PROJECT.supabase.co"
anon_key = "YOUR-ANON-KEY"
service_role_key = "YOUR-SERVICE-ROLE-KEY"

[app]
admin_emails = ["YOUR-EMAIL@example.com"]
```

4. Use the exact email address you intend to use for your administrator account.
5. Save the secrets and reboot the app.

## E. Configure the authentication URL

1. Copy the complete public URL of the Streamlit app.
2. In Supabase, open **Authentication → URL Configuration**.
3. Set **Site URL** to the Streamlit app URL.
4. Add the same URL to allowed redirect URLs if Supabase displays that option.

## F. Create the first account

1. Open the Streamlit app.
2. Select **Create invited account**.
3. Enter the exact administrator email from Streamlit Secrets.
4. Create a password of at least eight characters.
5. If a confirmation email arrives, confirm the address.
6. Return to the app and sign in.
7. Open **Businesses** and create the first business.

## G. Invite another user

1. Sign in as an administrator.
2. Open **Team**.
3. Add the person's exact email and choose `member` or `admin`.
4. Send the person the Streamlit app URL.
5. The person selects **Create invited account** and registers with the same email.

## Troubleshooting

- **Supabase is not connected:** Streamlit Secrets are missing or malformed.
- **Relation does not exist:** run the complete `supabase_setup.sql` file.
- **Email has not been invited:** add the exact lowercase email under **Team**, or add the administrator email in Streamlit Secrets.
- **Receipt upload fails:** confirm that the `receipts` Storage bucket exists and the service-role key is correct.
- **Tesseract dependency conflict:** `packages.txt` should contain only the five Tesseract package lines included in this release.
