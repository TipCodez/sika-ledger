def test_privacy_notice_page_is_public(client):
    client.post("/api/auth/logout")

    response = client.get("/privacy")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_privacy_notice_is_linked_and_describes_prototype_limits(client):
    script = client.get("/static/app.js")

    assert script.status_code == 200
    assert script.text.count('href="/privacy"') >= 3
    assert "How Sika Ledger handles information" in script.text
    assert "have no automatic deletion workflow" in script.text
    assert "Data Protection Act, 2012 (Act 843)" in script.text
