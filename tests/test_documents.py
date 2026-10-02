import io

from app import db
from app.models import MortgageDocument

from .conftest import PASSWORD, make_user
from .test_mortgages import create


def upload(client, m, files, category="appraisal"):
    return client.post(f"/mortgages/{m.id}/documents", data={"files": files, "category": category, "note": "2026 appraisal"},
                       content_type="multipart/form-data")


def test_upload_download_delete(client):
    m = create(client)
    resp = upload(client, m, [(io.BytesIO(b"%PDF-1.4 test"), "Appraisal Report.pdf"), (io.BytesIO(b"<script>"), "evil.html")])
    assert resp.status_code == 302
    doc = MortgageDocument.query.one()  # the .html file is rejected
    assert doc.category == "appraisal" and doc.size == 13 and doc.content_type == "application/pdf"
    page = client.get(f"/mortgages/{m.id}")
    assert b"Appraisal Report.pdf" in page.data
    resp = client.get(f"/mortgages/documents/{doc.id}/download")
    assert resp.data == b"%PDF-1.4 test" and "attachment" in resp.headers["Content-Disposition"]
    assert "sandbox" in resp.headers["Content-Security-Policy"]
    assert "inline" in client.get(f"/mortgages/documents/{doc.id}/download?view=1").headers["Content-Disposition"]
    client.post(f"/mortgages/documents/{doc.id}/delete")
    assert MortgageDocument.query.count() == 0


def test_filename_is_sanitised_and_mortgage_delete_cascades(client):
    m = create(client)
    upload(client, m, [(io.BytesIO(b"x"), '../../etc/pa*ss wd.txt')])
    doc = MortgageDocument.query.one()
    assert doc.filename == "pa_ss wd.txt"
    client.post(f"/mortgages/{m.id}/delete", data={"confirm": m.reference})
    assert MortgageDocument.query.count() == 0


def test_viewer_can_download_not_upload(app, client):
    m = create(client)
    upload(client, m, [(io.BytesIO(b"x"), "a.pdf")])
    doc_id = MortgageDocument.query.one().id
    make_user("v@example.com", role="viewer")
    c = app.test_client()
    c.post("/login", data={"email": "v@example.com", "password": PASSWORD})
    assert c.get(f"/mortgages/documents/{doc_id}/download").status_code == 200
    assert upload(c, m, [(io.BytesIO(b"x"), "b.pdf")]).status_code == 403
    assert db.session.query(MortgageDocument).count() == 1
