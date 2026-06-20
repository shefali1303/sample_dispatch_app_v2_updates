# Sample Dispatch Dashboard - Updated Version

This Flask app creates one Sample Invoice PDF and one Packing List PDF, then emails both documents to the fixed receiver configured in `.env`.

## Updates included

1. Email attachments use the same v15 document template style used by `template_preview_v15.html`.
2. Client details now use one `Client Name` field only.
3. Shipping details now use one `Address Line` only. Street Name and Address Line 2 are removed.
4. HSN code has a default value but remains editable.
5. Sample Type is a dropdown: Free Sample / Paid Sample.
6. Customer Shipping Account has a Service dropdown: FedEx / UPS / DHL.
7. Requested By is replaced with Requested BDE.
8. Requester Email is replaced with Email BDE.
9. The text `maps to Ship To` is removed.
10. Bill To is fixed as `REVEDA LLC`. The dropdown is removed.
11. Email body now includes customer shipping account, service, requested BDE, Email BDE, and sample approved by.

## Run locally on Mac

```bash
cd sample_dispatch_app_v2_updates
python3 -m venv venv
source venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
cp .env.example .env
DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 app.py
```

Open:

```text
http://127.0.0.1:5000
```

## Important WeasyPrint note

This version pins:

```text
WeasyPrint==62.3
pydyf==0.10.0
```

This avoids the PDF error:

```text
'super' object has no attribute 'transform'
```

## Email setup

Edit `.env`:

```env
EMAIL_DRY_RUN=false
EMAIL_ENABLED=true
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USER=dispatch@medikonda.com
EMAIL_PASSWORD=your_real_app_password_without_spaces
EMAIL_FROM=dispatch@medikonda.com
EMAIL_TO=asd@medikonda.com
```

Restart the app after changing `.env`.

## Security

Do not upload `.env` to GitHub. It contains private email password/app password.
