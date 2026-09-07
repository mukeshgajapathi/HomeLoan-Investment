import imaplib
import email
import io
import os
import re
import pandas as pd
import pdfplumber
import gspread
from google.oauth2.service_account import Credentials
from pypdf import PdfReader, PdfWriter
from datetime import datetime

# --- ACCOUNT CONFIGURATIONS ---
ACCOUNTS = [
    {
        "owner": "Self",
        "user": os.environ.get("MY_GMAIL_USER"),
        "pass": os.environ.get("MY_GMAIL_PASS"),
        "pan": os.environ.get("MY_PAN", "").strip().upper()
    },
    {
        "owner": "Spouse",
        "user": os.environ.get("WIFE_GMAIL_USER"),
        "pass": os.environ.get("WIFE_GMAIL_PASS"),
        "pan": os.environ.get("WIFE_PAN", "").strip().upper()
    }
]

def decrypt_pdf(pdf_bytes, pan_password):
    """Decrypts contract note PDF using the account's PAN password."""
    if not pan_password:
        return None
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        if reader.is_encrypted:
            if reader.decrypt(pan_password):
                writer = PdfWriter()
                for page in reader.pages:
                    writer.add_page(page)
                out_stream = io.BytesIO()
                writer.write(out_stream)
                out_stream.seek(0)
                return out_stream
        else:
            return io.BytesIO(pdf_bytes)
    except Exception:
        pass
    return None

def parse_contract_note(pdf_stream):
    """Parses trade execution table from decrypted PDF stream."""
    extracted_trades = []
    with pdfplumber.open(pdf_stream) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    if not row:
                        continue
                    row_str = " ".join([str(c).strip() for c in row if c])
                    if re.search(r'\b(B|S|BUY|SELL)\b', row_str, re.IGNORECASE):
                        tokens = row_str.split()
                        nums = [t for t in tokens if re.match(r'^\d+(\.\d+)?$', t)]
                        if len(nums) >= 2:
                            extracted_trades.append(row_str)
    return extracted_trades

def fetch_multi_account_trades():
    """Iterates through all configured Gmail inboxes and extracts contract notes."""
    all_parsed_trades = []

    for acc in ACCOUNTS:
        if not acc["user"] or not acc["pass"]:
            print(f"⚠️ Skipping {acc['owner']}: Missing credentials.")
            continue

        print(f"📧 Connecting to {acc['owner']}'s Inbox ({acc['user']})...")
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(acc["user"], acc["pass"])
            mail.select("inbox")

            status, messages = mail.search(None, 'FROM "zerodha" FILENAME "pdf"')
            email_ids = messages[0].split()
            print(f"📁 Found {len(email_ids)} contract note emails for {acc['owner']}.")

            for e_id in email_ids:
                res, msg_data = mail.fetch(e_id, "(RFC822)")
                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        for part in msg.walk():
                            if part.get_content_maintype() == 'multipart':
                                continue
                            if part.get('Content-Disposition') is None:
                                continue

                            filename = part.get_filename()
                            if filename and filename.lower().endswith('.pdf'):
                                pdf_bytes = part.get_payload(decode=True)
                                decrypted_stream = decrypt_pdf(pdf_bytes, acc["pan"])

                                if decrypted_stream:
                                    trades = parse_contract_note(decrypted_stream)
                                    for t in trades:
                                        all_parsed_trades.append({
                                            "owner": acc["owner"],
                                            "trade_raw": t
                                        })

            mail.logout()
        except Exception as e:
            print(f"❌ Error fetching for {acc['owner']}: {e}")

    print(f"🎉 Total Combined Household Trades Extracted: {len(all_parsed_trades)}")
    return all_parsed_trades

if __name__ == "__main__":
    combined_trades = fetch_multi_account_trades()
