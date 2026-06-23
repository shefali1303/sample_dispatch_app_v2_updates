from __future__ import annotations

import base64
import csv
import json
import math
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

import requests
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
FIXED_BILL_TO = "TO THE ORDER\nREVEDA LLC\n15526 Black pepper ln,\nodessa, Florida, 33556,\nUSA"

DEFAULT_HSN_CODE = "2921.29"

BOXES = {
    "box1": {
        "label": "Box 1 - 0.5 kg",
        "dimension": "23 x 15 x 10 cm",
        "extra_weight": 0.5,
    },
    "box2": {
        "label": "Box 2 - 1 kg",
        "dimension": "33.7 x 18.2 x 8.1 cm",
        "extra_weight": 0.5,
    },
    "box3": {
        "label": "Box 3 - 20 kg to 25 kg",
        "dimension": "41.7 x 35.9 x 36.9 cm",
        "extra_weight": 1.5,
    },
    "box4": {
        "label": "Box 4 - 20 kg to 25 kg",
        "dimension": "43 x 43 x 30 cm",
        "extra_weight": 1.5,
    },
}

CSV_COLUMNS = [
    "request_id",
    "invoice_no",
    "invoice_date",
    "client_name",
    "email",
    "phone_number",
    "company_name",
    "address_line",
    "city",
    "postal_code",
    "country",
    "full_ship_to_address",
    "product_name",
    "hsn_code",
    "quantity",
    "rate",
    "amount",
    "sub_total",
    "total_in_words",
    "selected_bill_to",
    "port_of_receipt",
    "port_of_loading",
    "port_of_discharge",
    "mode_of_transport",
    "quantity_in_boxes",
    "selected_box",
    "dimension",
    "net_weight",
    "gross_weight",
    "total_weight",
    "additional_details",
    "coa_file_link",
    "sample_type",
    "sample_approved_by",
    "customer_shipping_account",
    "shipping_service",
    "requested_bde",
    "email_bde",
    "sample_request_date",
    "document_status",
    "sample_invoice_pdf_link",
    "packing_list_pdf_link",
    "invoice_pdf_file_name",
    "packing_pdf_file_name",
    "created_at",
    "email_recipient",
    "email_status",
    "email_sent_at",
    "email_error",
    "apps_script_status",
    "apps_script_message",
]


def truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def get_email_recipient() -> str:
    return os.getenv("EMAIL_TO", "shiva@reveda.com").strip()


def parse_float(value: str, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except Exception:
        return default


def money(value: float) -> str:
    return f"${value:,.2f}"


def number_to_words(n: int) -> str:
    ones = [
        "Zero",
        "One",
        "Two",
        "Three",
        "Four",
        "Five",
        "Six",
        "Seven",
        "Eight",
        "Nine",
        "Ten",
        "Eleven",
        "Twelve",
        "Thirteen",
        "Fourteen",
        "Fifteen",
        "Sixteen",
        "Seventeen",
        "Eighteen",
        "Nineteen",
    ]
    tens = [
        "",
        "",
        "Twenty",
        "Thirty",
        "Forty",
        "Fifty",
        "Sixty",
        "Seventy",
        "Eighty",
        "Ninety",
    ]

    if n < 20:
        return ones[n]

    if n < 100:
        return tens[n // 10] + ((" " + ones[n % 10]) if n % 10 else "")

    if n < 1000:
        return ones[n // 100] + " Hundred" + (
            (" " + number_to_words(n % 100)) if n % 100 else ""
        )

    if n < 100000:
        return number_to_words(n // 1000) + " Thousand" + (
            (" " + number_to_words(n % 1000)) if n % 1000 else ""
        )

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
    line3 = ", ".join(
        [x for x in [data.get("city", ""), data.get("postal_code", "")] if x]
    )
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

    data.update(
        {
            "request_id": form.get("request_id")
            or f"REQ-{uuid.uuid4().hex[:8].upper()}",
            "invoice_no": invoice_no
            or form.get("invoice_no")
            or get_next_invoice_no(),
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
            "invoice_pdf_file_name": "",
            "packing_pdf_file_name": "",
            "email_recipient": get_email_recipient(),
            "email_status": "Not Sent",
            "email_sent_at": "",
            "email_error": "",
            "apps_script_status": "",
            "apps_script_message": "",
        }
    )

    return data


def save_local_record(record: Dict[str, Any]) -> None:
    RECORDS_JSON_DIR.mkdir(parents=True, exist_ok=True)

    (RECORDS_JSON_DIR / f"{record['request_id']}.json").write_text(
        json.dumps(record, indent=2), encoding="utf-8"
    )

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


def html_to_pdf(html: str, out_path: Path) -> None:
    try:
        from weasyprint import HTML
    except ImportError as exc:
        raise RuntimeError(
            "WeasyPrint is not installed. Run: pip install -r requirements.txt"
        ) from exc

    HTML(string=html, base_url=str(BASE_DIR)).write_pdf(
        str(out_path),
        optimize_images=True,
        jpeg_quality=85,
        dpi=180,
    )


def safe_file_name(value: str, fallback: str = "Sample Product") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9\-_. ]+", "", str(value or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or fallback


def generate_document_pdfs(record: Dict[str, Any]) -> List[Path]:
    product_name = safe_file_name(record.get("product_name"), "Sample Product")

    invoice_filename = f"{product_name}-Invoice.pdf"
    packing_filename = f"{product_name}-Packing list.pdf"

    invoice_path = GENERATED_DIR / invoice_filename
    packing_path = GENERATED_DIR / packing_filename

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
    record["invoice_pdf_file_name"] = invoice_filename
    record["packing_pdf_file_name"] = packing_filename

    return [invoice_path, packing_path]


def post_to_apps_script(
    record: Dict[str, Any], attachment_paths: Iterable[Path]
) -> Dict[str, Any]:
    webapp_url = os.getenv("APPS_SCRIPT_WEBAPP_URL", "").strip()
    secret = os.getenv("APPS_SCRIPT_SECRET", "").strip()
    email_to = os.getenv("EMAIL_TO", "shiva@reveda.com").strip()

    if not webapp_url:
        return {
            "status": "failed",
            "message": "APPS_SCRIPT_WEBAPP_URL is missing.",
        }

    if not secret:
        return {
            "status": "failed",
            "message": "APPS_SCRIPT_SECRET is missing.",
        }

    files = []

    for path in [Path(p) for p in attachment_paths]:
        if not path.exists():
            return {
                "status": "failed",
                "message": f"Missing attachment: {path.name}",
            }

        files.append(
            {
                "filename": path.name,
                "mime_type": "application/pdf",
                "content_base64": base64.b64encode(path.read_bytes()).decode("utf-8"),
            }
        )

    payload = {
        "secret": secret,
        "email_to": email_to,
        "processing_mode": "direct_with_queue_fallback",
        "record": record,
        "files": files,
    }

    timeout_seconds = int(os.getenv("APPS_SCRIPT_TIMEOUT_SECONDS", "35"))

    try:
        response = requests.post(webapp_url, json=payload, timeout=timeout_seconds)
        response.raise_for_status()
        return response.json()

    except requests.exceptions.Timeout:
        return {
            "status": "queued",
            "message": "Apps Script is taking longer than expected. Request may still be processing.",
        }

    except Exception as exc:
        return {
            "status": "failed",
            "message": str(exc),
        }


def finalize_and_email_record(record: Dict[str, Any]) -> Dict[str, Any]:
    record["document_status"] = "Saved"
    record["email_status"] = "Not Started"
    save_local_record(record)

    try:
        pdf_paths = generate_document_pdfs(record)
        record["document_status"] = "PDF Generated"

    except Exception as exc:
        record["document_status"] = "PDF Failed"
        record["email_status"] = "Not Sent"
        record["email_error"] = f"PDF generation failed: {exc}"
        save_local_record(record)

        return {
            "email_result": {
                "status": "failed",
                "message": record["email_error"],
            }
        }

    apps_script_result = post_to_apps_script(record, pdf_paths)
    status = apps_script_result.get("status", "failed")

    if status == "sent":
        record["email_status"] = "Sent"
        record["email_sent_at"] = datetime.now().isoformat(timespec="seconds")
        record["email_error"] = ""

    elif status == "queued":
        record["email_status"] = "Queued"
        record["email_error"] = apps_script_result.get("message", "")

    elif status == "saved":
        record["email_status"] = "Pending"
        record["email_error"] = ""

    else:
        record["email_status"] = "Failed"
        record["email_error"] = apps_script_result.get(
            "message", "Unknown Apps Script error"
        )

    record["apps_script_status"] = status
    record["apps_script_message"] = apps_script_result.get("message", "")

    save_local_record(record)

    return {
        "email_result": apps_script_result,
    }


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

    return render_template(
        "preview.html",
        record=record,
        boxes=BOXES,
        email_to=get_email_recipient(),
        email_dry_run=False,
    )


@app.route("/save", methods=["POST"])
def save():
    payload = request.form.get("payload", "{}")
    record = json.loads(payload)

    result = finalize_and_email_record(record)
    email_result = result["email_result"]
    status = email_result.get("status")

    if status == "sent":
        flash("Document saved, PDFs uploaded, and email sent successfully.")

    elif status == "queued":
        flash("Document saved, PDFs uploaded, and email queued successfully.")

    elif status == "saved":
        flash("Document saved and sent to Google Sheet for email processing.")

    else:
        flash(f"Document saved, but email processing failed: {email_result.get('message')}")

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
        filename = f"{safe_file_name(record.get('product_name'), 'Sample Product')}-Invoice.pdf"

    elif doc_type == "packing":
        html = render_template(
            "packing.html",
            record=record,
            pdf_mode=True,
            logo_path=logo_path,
            css_path=css_path,
        )
        filename = f"{safe_file_name(record.get('product_name'), 'Sample Product')}-Packing list.pdf"

    else:
        flash("Invalid document type")
        return redirect(url_for("record_page", request_id=request_id))

    out_path = GENERATED_DIR / filename
    html_to_pdf(html, out_path)

    return send_file(out_path, as_attachment=True, download_name=filename)


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)