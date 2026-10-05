"""Files kept with each mortgage (commitment letters, appraisals, title, insurance…)."""
import mimetypes
import os
import re

from flask import Blueprint, Response, flash, g, redirect, request, url_for

from .. import db
from ..models import DOCUMENT_CATEGORIES, Mortgage, MortgageDocument, audit
from ..tenancy import get_owned_or_404

bp = Blueprint("documents", __name__, url_prefix="/mortgages")

MAX_DOCUMENT_BYTES = 15 * 1024 * 1024
ALLOWED_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".heic", ".gif", ".webp", ".tif", ".tiff",
    ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt", ".msg", ".eml",
}


def clean_filename(name: str) -> str:
    name = os.path.basename((name or "").replace("\\", "/")).strip()
    name = re.sub(r"[^\w.\- ()&,]+", "_", name)
    return name[:200] or "document"


@bp.route("/<int:mortgage_id>/documents", methods=["POST"])
def upload(mortgage_id):
    m = get_owned_or_404(Mortgage, mortgage_id)
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        flash("Choose one or more files to upload.", "warning")
        return redirect(url_for("mortgages.detail", mortgage_id=m.id, _anchor="documents"))
    category = request.form.get("category", "other")
    if category not in dict(DOCUMENT_CATEGORIES):
        category = "other"
    note = (request.form.get("note") or "").strip()[:300] or None
    saved, rejected = 0, []
    for f in files:
        name = clean_filename(f.filename)
        ext = os.path.splitext(name)[1].lower()
        content = f.read()
        if ext not in ALLOWED_EXTENSIONS:
            rejected.append(f"{name} (file type not allowed)")
            continue
        if len(content) > MAX_DOCUMENT_BYTES:
            rejected.append(f"{name} (larger than 15 MB)")
            continue
        if not content:
            rejected.append(f"{name} (empty)")
            continue
        db.session.add(MortgageDocument(
            mortgage=m, filename=name, size=len(content), category=category, note=note, content=content,
            content_type=mimetypes.guess_type(name)[0] or "application/octet-stream", uploaded_by=g.user,
        ))
        saved += 1
    if saved:
        audit("documents_uploaded", f"{m.reference}: {saved} file(s), {category}")
        db.session.commit()
        flash(f"Uploaded {saved} file(s).", "success")
    if rejected:
        flash("Not uploaded: " + "; ".join(rejected), "warning")
    return redirect(url_for("mortgages.detail", mortgage_id=m.id, _anchor="documents"))


@bp.route("/documents/<int:doc_id>/download")
def download(doc_id):
    doc = get_owned_or_404(MortgageDocument, doc_id)
    inline = request.args.get("view") == "1" and doc.content_type in ("application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp")
    disposition = "inline" if inline else "attachment"
    return Response(doc.content, mimetype=doc.content_type, headers={
        "Content-Disposition": f'{disposition}; filename="{doc.filename}"',
        # Never let an uploaded file run script in the app's origin.
        "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox",
    })


@bp.route("/documents/<int:doc_id>/delete", methods=["POST"])
def delete(doc_id):
    doc = get_owned_or_404(MortgageDocument, doc_id)
    m = doc.mortgage
    audit("document_deleted", f"{m.reference}: {doc.filename}")
    db.session.delete(doc)
    db.session.commit()
    flash(f"Deleted {doc.filename}.", "success")
    return redirect(url_for("mortgages.detail", mortgage_id=m.id, _anchor="documents"))
