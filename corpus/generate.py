"""Synthetic corpus for Kavach: fake but format-valid Indian identifiers, fixed seed.

Writes corpus/files/**, corpus/labels.jsonl and corpus/demo_folder.zip.
Label line: {file, type, value, is_sensitive, holder, expected_tier_of_file}
`value` is the normalised form the detector hashes (digits only / upper PAN / lower email).
Only eval/ and privacy tests may read labels.jsonl.

    python corpus/generate.py
"""
from __future__ import annotations

import csv
import json
import random
import shutil
import string
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "detect_core"))

from faker import Faker  # noqa: E402
from openpyxl import Workbook  # noqa: E402
from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from detect_core.validators import gstin_check_char, verhoeff_check_digit  # noqa: E402

SEED = 20260926
FILES = ROOT / "files"
rng = random.Random(SEED)
fake = Faker("en_IN")
Faker.seed(SEED)

_used: set[str] = set()
labels: list[dict] = []
DEVANAGARI = str.maketrans("0123456789", "०१२३४५६७८९")


# ---------------------------------------------------------------- generators
def _unique(fn):
    while True:
        v = fn()
        if v not in _used:
            _used.add(v)
            return v


def aadhaar() -> str:
    def f():
        b = str(rng.randint(2, 9)) + "".join(rng.choice(string.digits) for _ in range(10))
        return b + verhoeff_check_digit(b)
    return _unique(f)


lookalike_12 = aadhaar  # invoice/order/UTR numbers that pass first-digit + Verhoeff


def mobile() -> str:
    return _unique(lambda: str(rng.randint(6, 9)) + "".join(rng.choice(string.digits) for _ in range(9)))


def pan(kind: str = "P", surname: str = "K") -> str:
    def f():
        return ("".join(rng.choice(string.ascii_uppercase) for _ in range(3)) + kind +
                (surname[:1].upper() if surname[:1].isalpha() else "K") +
                "".join(rng.choice(string.digits) for _ in range(4)) + rng.choice(string.ascii_uppercase))
    return _unique(f)


def gstin(state: str = "29") -> tuple[str, str]:
    p = pan("C", "T")
    body = f"{state}{p}1Z"
    return body + gstin_check_char(body), p


def bank_account() -> str:
    return _unique(lambda: str(rng.randint(1, 9)) + "".join(rng.choice(string.digits) for _ in range(rng.choice([10, 11, 13]))))


IFSC_BANKS = ["HDFC", "SBIN", "ICIC", "UTIB", "KKBK", "PUNB"]


def ifsc() -> str:
    return rng.choice(IFSC_BANKS) + "0" + "".join(rng.choice(string.digits) for _ in range(6))


def person() -> dict:
    first, last = fake.first_name(), fake.last_name()
    handle = f"{first}.{last}".lower().replace(" ", "")
    return {"name": f"{first} {last}", "first": first, "last": last,
            "email": _unique(lambda: f"{handle}{rng.randint(1, 999)}@{rng.choice(['gmail.com', 'yahoo.co.in', 'rediffmail.com', 'outlook.com'])}"),
            "mobile": mobile(), "aadhaar": aadhaar(), "pan": pan("P", last),
            "upi": _unique(lambda: f"{first.lower()}{rng.randint(1, 99)}@{rng.choice(['okaxis', 'oksbi', 'ybl', 'paytm', 'okhdfcbank'])}"),
            "address": fake.address().replace("\n", ", ")}


def spaced(num: str) -> str:
    return " ".join(num[i:i + 4] for i in range(0, len(num), 4))


def label(file: str, type_: str, value: str, sensitive: bool, holder: str, tier: str) -> None:
    labels.append({"file": file, "type": type_, "value": value, "is_sensitive": sensitive,
                   "holder": holder, "expected_tier_of_file": tier})


def out(rel: str) -> Path:
    p = FILES / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------- files
def customer_exports() -> None:
    rel = "Downloads/customer_export_aug.csv"
    people = [person() for _ in range(40)]
    with out(rel).open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Customer Name", "Mobile", "Email", "Aadhaar Number", "PAN", "City"])
        for p in people:
            w.writerow([p["name"], p["mobile"], p["email"], p["aadhaar"], p["pan"], fake.city()])
            label(rel, "MOBILE_IN", p["mobile"], True, "individual", "restricted")
            label(rel, "EMAIL", p["email"], True, "individual", "restricted")
            label(rel, "AADHAAR", p["aadhaar"], True, "individual", "restricted")
            label(rel, "PAN_INDIVIDUAL", p["pan"], True, "individual", "restricted")

    rel = "Downloads/customer_export_aug.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Customers"
    ws.append(["Customer Name", "Mobile", "Aadhaar Number"])
    for _ in range(14):
        p = person()
        ws.append([p["name"], p["mobile"], p["aadhaar"]])
        label(rel, "MOBILE_IN", p["mobile"], True, "individual", "restricted")
        label(rel, "AADHAAR", p["aadhaar"], True, "individual", "restricted")
    ws2 = wb.create_sheet("Legacy")
    ws2.append(["Customer", "ID No", "Joined"])  # unhelpful header
    for _ in range(8):
        p = person()
        ws2.append([p["name"], p["aadhaar"], fake.date_between("-3y", "today").isoformat()])
        label(rel, "AADHAAR", p["aadhaar"], True, "individual", "restricted")
    wb.save(out(rel))


def kyc_pdf() -> None:
    rel = "Downloads/kyc_batch.pdf"
    c = canvas.Canvas(str(out(rel)), pagesize=A4)
    for n in range(3):
        p = person()
        acct, code = bank_account(), ifsc()
        y = 800
        c.setFont("Helvetica-Bold", 16)
        c.drawString(60, y, f"Customer KYC Form - {n + 1} of 3")
        c.setFont("Helvetica", 11)
        rows = [("Full name", p["name"]), ("Date of birth", fake.date_of_birth(minimum_age=21, maximum_age=70).strftime("%d/%m/%Y")),
                ("Aadhaar number", spaced(p["aadhaar"])), ("PAN", p["pan"]), ("Mobile", p["mobile"]),
                ("Address", p["address"][:80]), ("Bank A/c no", acct), ("IFSC", code),
                ("Declaration", "I confirm the above details are true.")]
        for k, v in rows:
            y -= 28
            c.drawString(60, y, f"{k}: {v}")
        c.showPage()
        for t, v in (("AADHAAR", p["aadhaar"]), ("PAN_INDIVIDUAL", p["pan"]), ("MOBILE_IN", p["mobile"]),
                     ("BANK_ACCOUNT", acct)):
            label(rel, t, v, True, "individual", "restricted")
    c.save()


def _font(size: int, hindi: bool = False):
    for name in (["Nirmala.ttc", "Mangal.ttf"] if hindi else ["arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"]):
        for base in ("C:/Windows/Fonts/", "/usr/share/fonts/truetype/dejavu/", ""):
            try:
                return ImageFont.truetype(base + name, size)
            except OSError:
                continue
    return ImageFont.load_default()


def kyc_images() -> None:
    for rel, blurry in (("Desktop/kyc_scan.png", False), ("Desktop/kyc_scan_blurry.png", True)):
        p = person()
        img = Image.new("RGB", (1000, 620), (250, 248, 240))
        d = ImageDraw.Draw(img)
        d.rectangle([20, 20, 980, 600], outline=(40, 40, 40), width=3)
        d.text((60, 45), "भारत सरकार", font=_font(34, hindi=True), fill=(20, 20, 20))
        d.text((330, 50), "GOVERNMENT OF INDIA", font=_font(34), fill=(20, 20, 20))
        d.rectangle([60, 130, 260, 380], outline=(90, 90, 90), width=2)
        d.text((110, 240), "PHOTO", font=_font(28), fill=(150, 150, 150))
        d.text((300, 150), p["name"], font=_font(34), fill=(0, 0, 0))
        d.text((300, 210), f"DOB: {fake.date_of_birth(minimum_age=21, maximum_age=70).strftime('%d/%m/%Y')}", font=_font(30), fill=(0, 0, 0))
        d.text((300, 265), rng.choice(["MALE", "FEMALE"]), font=_font(30), fill=(0, 0, 0))
        d.text((250, 450), spaced(p["aadhaar"]), font=_font(58), fill=(0, 0, 0))
        d.text((260, 540), "आधार - आम आदमी का अधिकार", font=_font(30, hindi=True), fill=(120, 0, 0))
        if blurry:
            img = img.rotate(2.5, expand=True, fillcolor=(235, 235, 235)).filter(ImageFilter.GaussianBlur(1.6))
            px = img.load()
            for _ in range(25000):
                x, y = rng.randrange(img.width), rng.randrange(img.height)
                g = rng.randint(90, 200)
                px[x, y] = (g, g, g)
        img.save(out(rel))
        label(rel, "AADHAAR", p["aadhaar"], True, "individual", "restricted")


def whatsapp_chat() -> None:
    rel = "Documents/whatsapp_chat_support.txt"
    a, b = person(), person()
    order_id, utr = lookalike_12(), lookalike_12()
    dev = spaced(b["aadhaar"]).translate(DEVANAGARI)
    lines = [
        f"[12/09/26, 10:14:05] {a['first']}: bhai mera aadhar no hai {a['aadhaar'][:4]} {a['aadhaar'][4:8]}",
        f"{a['aadhaar'][8:]} yeh wala, KYC pending dikha raha hai",
        "[12/09/26, 10:15:02] Support Desk: Thik hai sir, KYC update ho jayega. PAN bhi bhej dijiye",
        f"[12/09/26, 10:16:40] {a['first']}: PAN bhej diya {a['pan']}",
        f"[12/09/26, 10:17:12] {a['first']}: mera number {a['mobile'][:5]} {a['mobile'][5:]} hai, refund gpay kar do {a['upi']} pe",
        f"[12/09/26, 10:18:55] Support Desk: Sir order id {order_id} ka refund process ho gaya, UTR {utr}",
        f"[13/09/26, 09:02:11] {b['first']}: नमस्ते, मेरा आधार {dev} है, कृपया अपडेट करें",
        f"[13/09/26, 09:03:30] {b['first']}: aur mera email {b['email']} hai",
        "[13/09/26, 09:05:00] Support Desk: Dhanyavaad, ho jayega.",
    ]
    out(rel).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    t = "confidential"
    for ty, v in (("AADHAAR", a["aadhaar"]), ("PAN_INDIVIDUAL", a["pan"]), ("MOBILE_IN", a["mobile"]),
                  ("UPI_ID", a["upi"]), ("AADHAAR", b["aadhaar"]), ("EMAIL", b["email"])):
        label(rel, ty, v, True, "individual", t)
    label(rel, "AADHAAR", order_id, False, "business", t)
    label(rel, "AADHAAR", utr, False, "business", t)


def tickets() -> None:
    rel = "Documents/ticket_export.csv"
    with out(rel).open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Ticket", "Customer", "UPI ID", "Mobile", "Txn Ref", "Issue"])
        for i in range(15):
            p = person()
            ref = lookalike_12()
            w.writerow([f"TKT-{4100 + i}", p["name"], p["upi"], p["mobile"], ref,
                        rng.choice(["Refund not received", "Double debit", "UPI failed", "Cashback missing"])])
            label(rel, "UPI_ID", p["upi"], True, "individual", "confidential")
            label(rel, "MOBILE_IN", p["mobile"], True, "individual", "confidential")
            label(rel, "AADHAAR", ref, False, "business", "confidential")


def invoices() -> None:
    for n in range(5):
        rel = f"Downloads/invoice_{2026090 + n}.pdf"
        g, _ = gstin(rng.choice(["27", "29", "07", "33"]))
        inv, order, utr = lookalike_12(), lookalike_12(), lookalike_12()
        awb = mobile()
        c = canvas.Canvas(str(out(rel)), pagesize=A4)
        c.setFont("Helvetica-Bold", 18)
        c.drawString(60, 800, f"{fake.company()} - TAX INVOICE")
        c.setFont("Helvetica", 11)
        y = 770
        for line in (f"Seller GSTIN: {g}", f"Invoice No: {inv}", f"Invoice Date: 0{n + 1}/09/2026",
                     f"Order ID: {order}", f"Payment UTR: {utr}", f"Courier AWB tracking: {awb}",
                     "Item: Office chairs x 4        Rs 18,400.00",
                     "CGST 9%: Rs 1,656.00   SGST 9%: Rs 1,656.00", "Total payable: Rs 21,712.00",
                     "This is a computer generated invoice."):
            c.drawString(60, y, line)
            y -= 24
        c.showPage()
        c.save()
        label(rel, "GSTIN", g, True, "business", "internal")
        for v in (inv, order, utr):
            label(rel, "AADHAAR", v, False, "business", "internal")
        label(rel, "MOBILE_IN", awb, False, "business", "internal")


def vendor_bill_and_signature() -> None:
    rel = "Documents/vendor_bill_acme.txt"
    g, company_pan = gstin("29")
    helpline = mobile()
    out(rel).write_text(
        f"ACME OFFICE SUPPLIES PVT LTD\nGSTIN: {g}\nCompany PAN: {company_pan}\n"
        f"Bill No: B-{rng.randint(1000, 9999)}  Date: 14/09/2026\n"
        "Printer cartridges x 10 ..... Rs 12,500.00\nGST 18% ....................... Rs 2,250.00\n"
        f"Customer care helpline: {helpline}\nPayment due in 30 days.\n", encoding="utf-8", newline="\n")
    label(rel, "GSTIN", g, True, "business", "internal")
    label(rel, "PAN_BUSINESS", company_pan, True, "business", "internal")
    label(rel, "MOBILE_IN", helpline, False, "business", "internal")

    rel = "Documents/re_quarterly_review.eml"
    p = person()
    toll = mobile()
    work_email = f"{p['first'].lower()}.{p['last'].lower()}@acmecorp.in"
    out(rel).write_text(
        f"From: {p['name']} <{work_email}>\nTo: team@acmecorp.in\nSubject: Re: Quarterly review\n\n"
        "Hi team,\nAttaching the review deck. Let's sync on Thursday.\n\nRegards,\n"
        f"{p['name']}\nRegional Manager, Acme Corp\nToll free helpline: {toll}\nwww.acmecorp.in\n",
        encoding="utf-8", newline="\n")
    label(rel, "EMAIL", work_email, True, "individual", "internal")
    label(rel, "MOBILE_IN", toll, False, "business", "internal")


def config_backup() -> None:
    rel = "Documents/config_backup.env"
    aws = "AKIA" + "".join(rng.choice(string.ascii_uppercase + string.digits) for _ in range(16))
    gh = "ghp_" + "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(36))
    out(rel).write_text(
        f"# backup of prod env - do not share\nAPP_ENV=production\nAWS_ACCESS_KEY_ID={aws}\n"
        f"GITHUB_TOKEN={gh}\nLOG_LEVEL=info\nMAX_WORKERS=8\n", encoding="utf-8", newline="\n")
    label(rel, "SECRET", aws, True, "unknown", "restricted")
    label(rel, "SECRET", gh, True, "unknown", "restricted")


def marked_and_masked() -> None:
    rel = "Documents/board_note_restructuring.txt"
    p = person()
    out(rel).write_text(
        "STRICTLY CONFIDENTIAL\n\nNote to the Board: proposed restructuring of the Pune unit.\n"
        f"Nominated director: {p['name']}, PAN {p['pan']}.\n"
        "The board is requested to approve the proposal at the next meeting.\n", encoding="utf-8", newline="\n")
    label(rel, "PAN_INDIVIDUAL", p["pan"], True, "individual", "restricted")

    rel = "Documents/address_proof_letter.txt"
    p = person()
    out(rel).write_text(
        f"To whom it may concern,\n\nThis is to certify that {p['name']} resides at {p['address']}.\n"
        "Aadhaar: XXXX XXXX 4821 (masked as per UIDAI guidelines)\n\nRegards,\nAdmin Office\n",
        encoding="utf-8", newline="\n")
    label(rel, "AADHAAR", "XXXXXXXX4821", True, "individual", "confidential")


def bank_details() -> None:
    rel = "Documents/vendor_payment_details.txt"
    lines = ["Vendor payout list - September\n"]
    for _ in range(3):
        p = person()
        acct, code = bank_account(), ifsc()
        lines.append(f"Beneficiary: {p['name']}\nA/c no: {acct}\nIFSC: {code}\n")
        label(rel, "BANK_ACCOUNT", acct, True, "individual", "confidential")
    out(rel).write_text("\n".join(lines), encoding="utf-8", newline="\n")


def synced_contacts() -> None:
    rel = "OneDrive - Acme/team_contacts.csv"
    with out(rel).open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Name", "Personal Email", "Mobile", "Team"])
        for _ in range(12):
            p = person()
            w.writerow([p["name"], p["email"], p["mobile"], rng.choice(["Sales", "Ops", "Finance"])])
            label(rel, "EMAIL", p["email"], True, "individual", "confidential")
            label(rel, "MOBILE_IN", p["mobile"], True, "individual", "confidential")


def shipping_manifest() -> None:
    rel = "Downloads/shipping_manifest.csv"
    with out(rel).open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["AWB No", "Destination", "Weight kg", "Status"])
        for _ in range(8):
            awb = mobile()
            w.writerow([awb, fake.city(), round(rng.uniform(0.5, 20), 1), rng.choice(["In transit", "Delivered"])])
            label(rel, "MOBILE_IN", awb, False, "business", "public")


def extra_files() -> None:
    rel = "Desktop/salary_slip_sept.txt"
    p = person()
    acct, code = bank_account(), ifsc()
    out(rel).write_text(
        f"ACME CORP - SALARY SLIP - SEPTEMBER 2026\nEmployee: {p['name']}   Emp code: E{rng.randint(1000, 9999)}\n"
        f"PAN: {p['pan']}\nSalary credited to A/c {acct} (IFSC {code})\n"
        "Basic 45,000 | HRA 18,000 | Special 12,500 | PF -5,400 | TDS -6,200\nNet pay: 63,900\n",
        encoding="utf-8", newline="\n")
    label(rel, "PAN_INDIVIDUAL", p["pan"], True, "individual", "confidential")
    label(rel, "BANK_ACCOUNT", acct, True, "individual", "confidential")

    rel = "Documents/customer_complaint.eml"
    p = person()
    out(rel).write_text(
        f"From: {p['name']} <{p['email']}>\nTo: care@acmecorp.in\nSubject: KYC rejected again\n\n"
        f"Hello, my KYC was rejected twice. My Aadhaar number is {spaced(p['aadhaar'])} and my mobile is "
        f"+91 {p['mobile'][:5]} {p['mobile'][5:]}. Please call me.\n\nThanks,\n{p['first']}\n",
        encoding="utf-8", newline="\n")
    for t, v in (("EMAIL", p["email"]), ("AADHAAR", p["aadhaar"]), ("MOBILE_IN", p["mobile"])):
        label(rel, t, v, True, "individual", "confidential")

    rel = "Downloads/scanned_receipt.png"
    inv, utr = lookalike_12(), lookalike_12()
    img = Image.new("RGB", (900, 500), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.text((40, 30), "PAYMENT RECEIPT", font=_font(40), fill=(0, 0, 0))
    d.text((40, 120), f"Invoice No: {inv}", font=_font(32), fill=(0, 0, 0))
    d.text((40, 180), f"UTR Ref: {utr}", font=_font(32), fill=(0, 0, 0))
    d.text((40, 240), "Amount: Rs 4,250.00   Mode: NEFT", font=_font(32), fill=(0, 0, 0))
    d.text((40, 300), "Thank you for your business", font=_font(28), fill=(60, 60, 60))
    img.filter(ImageFilter.GaussianBlur(0.6)).save(out(rel))
    label(rel, "AADHAAR", inv, False, "business", "public")
    label(rel, "AADHAAR", utr, False, "business", "public")


def clean_files() -> None:
    out("Documents/sample_data_readme.txt").write_text(
        "This folder holds anonymised sample data for the analytics team.\n"
        "Contact the data office for access requests. Last refreshed 2026-09-01.\n", encoding="utf-8", newline="\n")
    out("Documents/meeting_notes.txt").write_text(
        "Weekly sync - 22/09/2026\n- Launch moved to Q4\n- Budget review on Friday at 3 PM\n"
        "- Room 204 booked for 2 hours\n- Action: Priya to share the deck\n", encoding="utf-8", newline="\n")
    out("Documents/recipe_masala_chai.md").write_text(
        "# Masala chai\n\n2 cups water, 1 cup milk, 2 tsp tea, 4 cardamom pods, 1 inch ginger.\n"
        "Boil 5 minutes. Serves 3.\n", encoding="utf-8", newline="\n")
    out("Desktop/todo.txt").write_text(
        "1. Renew laptop warranty\n2. Book train to Mumbai (12 Oct)\n3. Submit expense report 2026-09\n",
        encoding="utf-8", newline="\n")
    with out("Documents/lunch_menu.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Day", "Dish", "Price"])
        for d, dish, pr in (("Mon", "Rajma chawal", 120), ("Tue", "Chole bhature", 140),
                            ("Wed", "Veg biryani", 150), ("Thu", "Masala dosa", 110), ("Fri", "Thali", 180)):
            w.writerow([d, dish, pr])
    out("Documents/project_readme.md").write_text(
        "# Inventory dashboard\n\nRun `make dev` and open http://localhost:3000.\n"
        "Version 1.4.2, released 2026-08-30. Build 20260830.\n", encoding="utf-8", newline="\n")


def main() -> None:
    if FILES.exists():
        shutil.rmtree(FILES)
    FILES.mkdir(parents=True)
    customer_exports()
    kyc_pdf()
    kyc_images()
    whatsapp_chat()
    tickets()
    invoices()
    vendor_bill_and_signature()
    config_backup()
    marked_and_masked()
    bank_details()
    synced_contacts()
    shipping_manifest()
    extra_files()
    clean_files()

    with (ROOT / "labels.jsonl").open("w", encoding="utf-8") as fh:
        for l in labels:
            fh.write(json.dumps(l, ensure_ascii=False) + "\n")

    zpath = ROOT / "demo_folder.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(FILES.rglob("*")):
            if f.is_file():
                z.write(f, Path("demo_folder") / f.relative_to(FILES))

    files = sorted(f for f in FILES.rglob("*") if f.is_file())
    pos = sum(l["is_sensitive"] for l in labels)
    print(f"{len(files)} files, {len(labels)} labels ({pos} sensitive, {len(labels) - pos} hard negatives)")


if __name__ == "__main__":
    main()
