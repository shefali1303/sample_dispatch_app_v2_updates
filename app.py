from __future__ import annotations

import csv
import json
import math
import mimetypes
import os
import re
import smtplib
import uuid
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, Iterable, List

from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, send_file, url_for

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
GENERATED_DIR = DATA_DIR / "generated"
RECORDS_CSV = DATA_DIR / "sample_requests.csv"
RECORDS_JSON_DIR = DATA_DIR / "records"
RECORDS_JSON_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(BASE_DIR / ".env")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-this-secret-key")

BILL_TO_DISPLAY = "REVEDA"
FIXED_BILL_TO = "TO THE ORDER\nREVEDA LLC\n15526 Black pepper ln,\nodessa, Florida, 33556,\nUSA"# FIXED_BILL_TO = "TO THE ORDER\nREVEDA LLC"

DEFAULT_HSN_CODE = "2921.29"

BOXES = {
    "box1": {"label": "Box 1 - 0.5 kg", "dimension": "23 x 15 x 10 cm", "extra_weight": 0.5},
    "box2": {"label": "Box 2 - 1 kg", "dimension": "33.7 x 18.2 x 8.1 cm", "extra_weight": 0.5},
    "box3": {"label": "Box 3 - 20 kg to 25 kg", "dimension": "41.7 x 35.9 x 36.9 cm", "extra_weight": 1.5},
    "box4": {"label": "Box 4 - 20 kg to 25 kg", "dimension": "43 x 43 x 30 cm", "extra_weight": 1.5},
}

CSV_COLUMNS = [
    "request_id", "invoice_no", "invoice_date", "client_name", "email", "phone_number",
    "company_name", "address_line", "city", "postal_code", "country", "full_ship_to_address",
    "product_name", "hsn_code", "quantity", "rate", "amount", "sub_total", "total_in_words",
    "selected_bill_to", "port_of_receipt", "port_of_loading", "port_of_discharge", "mode_of_transport",
    "quantity_in_boxes", "selected_box", "dimension", "net_weight", "gross_weight", "total_weight",
    "additional_details", "coa_file_link", "sample_type", "sample_approved_by", "customer_shipping_account",
    "shipping_service", "requested_bde", "email_bde", "sample_request_date", "document_status",
    "sample_invoice_pdf_link", "packing_list_pdf_link", "created_at", "email_recipient", "email_status",
    "email_sent_at", "email_error",
]


def truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def get_email_recipient() -> str:
    return os.getenv("EMAIL_TO", "asd@medikonda.com").strip()


def parse_float(value: str, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except Exception:
        return default


def money(value: float) -> str:
    return f"${value:,.2f}"


def number_to_words(n: int) -> str:
    ones = ["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]
    if n < 20:
        return ones[n]
    if n < 100:
        return tens[n // 10] + ((" " + ones[n % 10]) if n % 10 else "")
    if n < 1000:
        return ones[n // 100] + " Hundred" + ((" " + number_to_words(n % 100)) if n % 100 else "")
    if n < 100000:
        return number_to_words(n // 1000) + " Thousand" + ((" " + number_to_words(n % 1000)) if n % 1000 else "")
    return str(n)


def amount_to_words(amount: float) -> str:
    dollars = int(round(amount))
    return f"United States Dollar {number_to_words(dollars)}"


def calculate_invoice(quantity: float) -> Dict[str, Any]:
    if quantity <= 1:
        rate = 1
        amount = 1.00
    elif quantity < 3:
        rate = 2
        amount = quantity * rate
    else:
        rate = math.ceil(quantity)
        amount = quantity * rate
    return {
        "rate": rate,
        "amount": round(amount, 2),
        "sub_total": round(amount, 2),
        "total_in_words": amount_to_words(amount),
    }


def choose_box(quantity: float, manual_box: str | None) -> str:
    if abs(quantity - 0.5) < 0.0001:
        return "box1"
    if abs(quantity - 1.0) < 0.0001:
        return "box2"
    if 20 <= quantity <= 25 and manual_box in {"box3", "box4"}:
        return manual_box
    if manual_box in BOXES:
        return manual_box
    return "box2"


def calculate_packing(quantity: float, manual_box: str | None) -> Dict[str, Any]:
    selected_box = choose_box(quantity, manual_box)
    box = BOXES[selected_box]
    net_weight = quantity
    gross_weight = round(net_weight + box["extra_weight"], 2)
    return {
        "selected_box_key": selected_box,
        "selected_box": box["label"],
        "dimension": box["dimension"],
        "net_weight": round(net_weight, 2),
        "gross_weight": gross_weight,
        "total_weight": gross_weight,
        "quantity_in_boxes": 1,
    }


def get_next_invoice_no() -> str:
    start = int(os.getenv("START_INVOICE_NUMBER", "521"))
    max_no = start - 1
    if RECORDS_CSV.exists():
        with RECORDS_CSV.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                match = re.search(r"INV-SMP(\d+)", row.get("invoice_no", ""))
                if match:
                    max_no = max(max_no, int(match.group(1)))
    return f"INV-SMP{max_no + 1}"


def full_ship_to(data: Dict[str, Any]) -> str:
    line1 = data.get("company_name", "")
    line2 = data.get("address_line", "")
    line3 = ", ".join([x for x in [data.get("city", ""), data.get("postal_code", "")] if x])
    line4 = data.get("country", "")
    return "\n".join([x for x in [line1, line2, line3, line4] if x])


def build_record(form: Dict[str, Any], invoice_no: str | None = None) -> Dict[str, Any]:
    quantity = parse_float(form.get("quantity"), 0)
    invoice_calc = calculate_invoice(quantity)
    packing_calc = calculate_packing(quantity, form.get("selected_box"))
    today = datetime.now().strftime("%b %d, %Y").upper()
    sample_request_date = datetime.now().strftime("%Y-%m-%d")
    data = {k: str(form.get(k, "")).strip() for k in form.keys()}
    hsn_code = data.get("hsn_code") or DEFAULT_HSN_CODE
    data.update({
        "request_id": form.get("request_id") or f"REQ-{uuid.uuid4().hex[:8].upper()}",
        "invoice_no": invoice_no or form.get("invoice_no") or get_next_invoice_no(),
        "invoice_date": today,
        "quantity": quantity,
        "rate": invoice_calc["rate"],
        "amount": invoice_calc["amount"],
        "sub_total": invoice_calc["sub_total"],
        "amount_display": money(invoice_calc["amount"]),
        "sub_total_display": money(invoice_calc["sub_total"]),
        "total_in_words": invoice_calc["total_in_words"],
        "hsn_code": hsn_code,
        "selected_bill_to": FIXED_BILL_TO,
        "full_ship_to_address": full_ship_to(data),
        "port_of_receipt": "Hyderabad",
        "port_of_loading": "Hyderabad, India",
        "port_of_discharge": data.get("country", ""),
        "mode_of_transport": "Air",
        "terms": "100% Advance Payment",
        "s_no": 1,
        "quantity_in_boxes": packing_calc["quantity_in_boxes"],
        "selected_box_key": packing_calc["selected_box_key"],
        "selected_box": packing_calc["selected_box"],
        "dimension": packing_calc["dimension"],
        "net_weight": packing_calc["net_weight"],
        "gross_weight": packing_calc["gross_weight"],
        "total_weight": packing_calc["total_weight"],
        "sample_request_date": sample_request_date,
        "document_status": "Previewed",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "sample_invoice_pdf_link": "",
        "packing_list_pdf_link": "",
        "email_recipient": get_email_recipient(),
        "email_status": "Not Sent",
        "email_sent_at": "",
        "email_error": "",
    })
    return data


def save_local_record(record: Dict[str, Any]) -> None:
    RECORDS_JSON_DIR.mkdir(parents=True, exist_ok=True)
    (RECORDS_JSON_DIR / f"{record['request_id']}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    write_header = not RECORDS_CSV.exists()
    with RECORDS_CSV.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow({col: record.get(col, "") for col in CSV_COLUMNS})


def load_record(request_id: str) -> Dict[str, Any]:
    path = RECORDS_JSON_DIR / f"{request_id}.json"
    if not path.exists():
        raise FileNotFoundError("Record not found")
    return json.loads(path.read_text(encoding="utf-8"))


def save_to_google_sheet(record: Dict[str, Any]) -> bool:
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    service_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not sheet_id or not service_json:
        return False
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds_info = json.loads(service_json)
        creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
        client = gspread.authorize(creds)
        sh = client.open_by_key(sheet_id)
        ws = sh.worksheet(os.getenv("GOOGLE_SHEET_TAB", "Sample Requests"))
        ws.append_row([record.get(col, "") for col in CSV_COLUMNS], value_input_option="USER_ENTERED")
        return True
    except Exception as exc:
        print("Google Sheet save failed:", exc)
        return False


def html_to_pdf(html: str, out_path: Path) -> None:
    try:
        from weasyprint import HTML
    except ImportError as exc:
        raise RuntimeError("WeasyPrint is not installed. Run: pip install -r requirements.txt") from exc
    HTML(string=html, base_url=str(BASE_DIR)).write_pdf(str(out_path))


def generate_document_pdfs(record: Dict[str, Any]) -> List[Path]:
    invoice_filename = f"{record['invoice_no']}_Sample_Invoice.pdf"
    packing_filename = f"{record['invoice_no']}_Packing_List.pdf"
    invoice_path = GENERATED_DIR / invoice_filename
    packing_path = GENERATED_DIR / packing_filename
    # invoice_html = render_template("invoice.html", record=record, pdf_mode=True)
    # packing_html = render_template("packing.html", record=record, pdf_mode=True)
    logo_path = (BASE_DIR / "static" / "logo.png").resolve().as_uri()
    css_path = (BASE_DIR / "static" / "styles.css").resolve().as_uri()

    invoice_html = render_template(
    "invoice.html",
    record=record,
    pdf_mode=True,
    logo_path=logo_path,
    css_path=css_path,
)

    packing_html = render_template(
    "packing.html",
    record=record,
    pdf_mode=True,
    logo_path=logo_path,
    css_path=css_path,
)
    html_to_pdf(invoice_html, invoice_path)
    html_to_pdf(packing_html, packing_path)
    record["sample_invoice_pdf_link"] = str(invoice_path)
    record["packing_list_pdf_link"] = str(packing_path)
    return [invoice_path, packing_path]


def send_documents_email(record: Dict[str, Any], attachment_paths: Iterable[Path]) -> Dict[str, Any]:
    if not truthy(os.getenv("EMAIL_ENABLED", "true")):
        return {"status": "disabled", "message": "Email sending is disabled."}

    email_to = get_email_recipient()
    email_from = os.getenv("EMAIL_FROM", os.getenv("EMAIL_USER", "")).strip()
    email_user = os.getenv("EMAIL_USER", "").strip()
    email_password = os.getenv("EMAIL_PASSWORD", "").strip()
    email_host = os.getenv("EMAIL_HOST", "smtp.gmail.com").strip()
    email_port = int(os.getenv("EMAIL_PORT", "587"))
    dry_run = truthy(os.getenv("EMAIL_DRY_RUN", "true"))

    subject = f"Sample Dispatch Documents - {record.get('invoice_no')} - {record.get('company_name')}"
    body = f"""Dear Team,

Please find attached the sample dispatch documents.

Invoice No: {record.get('invoice_no')}
Company: {record.get('company_name')}
Product: {record.get('product_name')}
Quantity: {record.get('quantity')} kg
Ship To Country: {record.get('country')}

Sample Approval details:
1. Customer Shipping Account: {record.get('customer_shipping_account') or '-'}
   Service: {record.get('shipping_service') or '-'}
2. Requested (Business Development Executive): {record.get('requested_bde') or '-'}
   Email (Business Development Executive): {record.get('email_bde') or '-'}
3. Sample Approved By: {record.get('sample_approved_by') or '-'}
   Sample Type: {record.get('sample_type') or '-'}

Attached:
1. Sample Invoice
2. Packing List

Regards,
Sample Dispatch System
"""

    attachment_paths = [Path(path) for path in attachment_paths]
    if dry_run:
        return {
            "status": "dry_run",
            "message": "Dry run only. No real email was sent.",
            "to": email_to,
            "subject": subject,
            "attachments": [path.name for path in attachment_paths],
        }

    missing = [path.name for path in attachment_paths if not path.exists()]
    if missing:
        return {"status": "failed", "message": "Missing attachment(s): " + ", ".join(missing)}
    if not email_to or not email_from or not email_user or not email_password:
        return {"status": "failed", "message": "Email settings are incomplete. Check .env."}

    msg = EmailMessage()
    msg["From"] = email_from
    msg["To"] = email_to
    msg["Subject"] = subject
    msg.set_content(body)

    for path in attachment_paths:
        mime_type, _ = mimetypes.guess_type(path.name)
        maintype, subtype = (mime_type or "application/pdf").split("/", 1)
        msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)

    try:
        with smtplib.SMTP(email_host, email_port, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.login(email_user, email_password)
            server.send_message(msg)
        return {"status": "sent", "message": f"Email sent to {email_to}.", "to": email_to}
    except Exception as exc:
        return {"status": "failed", "message": str(exc), "to": email_to}


def finalize_and_email_record(record: Dict[str, Any]) -> Dict[str, Any]:
    record["document_status"] = "Saved"
    save_local_record(record)
    google_saved = save_to_google_sheet(record)
    try:
        pdf_paths = generate_document_pdfs(record)
        record["document_status"] = "PDF Generated"
    except Exception as exc:
        record["document_status"] = "PDF Failed"
        record["email_status"] = "Not Sent"
        record["email_error"] = f"PDF generation failed: {exc}"
        save_local_record(record)
        return {"google_saved": google_saved, "email_result": {"status": "failed", "message": record["email_error"]}}

    email_result = send_documents_email(record, pdf_paths)
    status = email_result.get("status", "failed")
    if status == "sent":
        record["email_status"] = "Sent"
        record["email_sent_at"] = datetime.now().isoformat(timespec="seconds")
        record["email_error"] = ""
    elif status == "dry_run":
        record["email_status"] = "Dry Run"
        record["email_sent_at"] = datetime.now().isoformat(timespec="seconds")
        record["email_error"] = "Dry run only. No real email was sent."
    elif status == "disabled":
        record["email_status"] = "Disabled"
        record["email_error"] = email_result.get("message", "Email disabled")
    else:
        record["email_status"] = "Failed"
        record["email_error"] = email_result.get("message", "Unknown email error")
    save_local_record(record)
    return {"google_saved": google_saved, "email_result": email_result}


@app.route("/", methods=["GET"])
def form_page():
    return render_template(
        "form.html",
        boxes=BOXES,
        bill_to_display=BILL_TO_DISPLAY,
        fixed_bill_to=FIXED_BILL_TO,
        default_hsn_code=DEFAULT_HSN_CODE,
    )

@app.route("/preview", methods=["POST"])
def preview():
    record = build_record(request.form.to_dict())
    return render_template("preview.html", record=record, boxes=BOXES, email_to=get_email_recipient(), email_dry_run=truthy(os.getenv("EMAIL_DRY_RUN", "true")))


@app.route("/save", methods=["POST"])
def save():
    payload = request.form.get("payload", "{}")
    record = json.loads(payload)
    result = finalize_and_email_record(record)
    email_result = result["email_result"]
    if email_result.get("status") == "sent":
        flash("Record saved, PDFs generated, and email sent successfully.")
    elif email_result.get("status") == "dry_run":
        flash("Record saved and PDFs generated. Email dry run completed; no real email was sent.")
    else:
        flash(f"Record saved, but email was not sent: {email_result.get('message')}")
    return redirect(url_for("record_page", request_id=record["request_id"]))


@app.route("/record/<request_id>")
def record_page(request_id: str):
    record = load_record(request_id)
    return render_template("record.html", record=record)


@app.route("/invoice/<request_id>")
def invoice_html(request_id: str):
    record = load_record(request_id)
    return render_template("invoice.html", record=record)


@app.route("/packing/<request_id>")
def packing_html(request_id: str):
    record = load_record(request_id)
    return render_template("packing.html", record=record)


@app.route("/download/<doc_type>/<request_id>")
def download_pdf(doc_type: str, request_id: str):
    record = load_record(request_id)

    logo_path = (BASE_DIR / "static" / "logo.png").resolve().as_uri()
    css_path = (BASE_DIR / "static" / "styles.css").resolve().as_uri()

    if doc_type == "invoice":
        html = render_template(
            "invoice.html",
            record=record,
            pdf_mode=True,
            logo_path=logo_path,
            css_path=css_path,
        )
        filename = f"{record['invoice_no']}_Sample_Invoice.pdf"

    elif doc_type == "packing":
        html = render_template(
            "packing.html",
            record=record,
            pdf_mode=True,
            logo_path=logo_path,
            css_path=css_path,
        )
        filename = f"{record['invoice_no']}_Packing_List.pdf"

    else:
        flash("Invalid document type")
        return redirect(url_for("record_page", request_id=request_id))

    out_path = GENERATED_DIR / filename
    html_to_pdf(html, out_path)

    return send_file(out_path, as_attachment=True, download_name=filename)


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
