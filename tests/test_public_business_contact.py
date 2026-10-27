def set_public_brand(client):
    response = client.put("/api/branding", json={
        "display_name": "Akosua Stores",
        "address": "Private office address",
        "contact": "024 555 0101",
        "footer": "Internal footer text",
        "accent": "#165944",
    })
    assert response.status_code == 200


def test_debt_page_exposes_only_configured_public_business_identity(client, debt):
    assert client.get("/api/public/" + debt["public_token"]).json()["business_contact"] is None
    set_public_brand(client)

    public = client.get("/api/public/" + debt["public_token"]).json()

    assert public["business_name"] == "Akosua Stores"
    assert public["business_contact"] == "024 555 0101"
    assert "address" not in public
    assert "footer" not in public
    assert client.get("/static/app.js").text.count("Business contact:") == 2


def test_susu_page_exposes_configured_public_business_contact(client):
    set_public_brand(client)
    response = client.post("/api/susu", json={
        "name": "Abena Mensah",
        "phone": "0241234567",
        "label": "Daily savings",
        "daily_target": "20",
    })
    assert response.status_code == 201
    account = response.json()

    public = client.get("/api/susu-public/" + account["public_token"]).json()

    assert public["business_name"] == "Akosua Stores"
    assert public["business_contact"] == "024 555 0101"
    assert "address" not in public
    assert "footer" not in public
