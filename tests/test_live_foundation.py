import asyncio
import pytest
from sqlalchemy import select
from app.main import create_app
from app import main,provider_accounts,jobs
from app.db import write,provider_accounts as provider_accounts_table,payments
from app.domain import now


class FakeResponse:
    def __init__(self,data,status_code=200):self.data=data;self.status_code=status_code
    def raise_for_status(self):
        if self.status_code>=400:raise RuntimeError("provider error")
    def json(self):return self.data


def test_live_key_requires_every_explicit_production_guard(tmp_path):
    url="sqlite:///"+str(tmp_path/"live-guard.db")
    with pytest.raises(RuntimeError,match="ENABLE_LIVE_PAYMENTS"):
        create_app(url,{"paystack_key":"sk_live_example"})
    with pytest.raises(RuntimeError,match="HTTPS"):
        create_app(url,{"paystack_key":"sk_live_example","enable_live_payments":True,
            "paystack_account_approved":True,"secure_cookie":False,"base_url":"http://example.test"})


def test_live_sms_requires_approval_and_sends_without_sandbox_flag(tmp_path,monkeypatch):
    url="sqlite:///"+str(tmp_path/"sms-guard.db")
    with pytest.raises(RuntimeError,match="Live SMS requires"):
        create_app(url,{"sms_mode":"arkesel_live","arkesel_key":"key"})
    captured={}
    class FakeClient:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,headers=None,json=None):
            captured.update(json)
            return FakeResponse({"status":"success","data":[{"id":"live-message-1"}]})
    monkeypatch.setattr(jobs.httpx,"AsyncClient",FakeClient)
    result=asyncio.run(jobs.submit_sandbox({"arkesel_key":"key","sms_sender":"SikaLedger",
        "sms_mode":"arkesel_live","arkesel_callbacks":True,
        "base_url":"https://ledger.example"},"0241234567","Test reminder"))
    assert result=={"status":"live_accepted","provider_id":"live-message-1"}
    assert captured["sandbox"] is False
    assert captured["callback_url"]=="https://ledger.example/api/webhooks/arkesel"


def test_live_sms_reports_an_actionable_provider_rejection(monkeypatch):
    class FakeClient:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,*args,**kwargs):
            return FakeResponse({"status":"error"},403)
    monkeypatch.setattr(jobs.httpx,"AsyncClient",FakeClient)
    result=asyncio.run(jobs.submit_sandbox({"arkesel_key":"key","sms_sender":"ApprovedName",
        "sms_mode":"arkesel_live","arkesel_callbacks":False,
        "base_url":"https://ledger.example"},"+233241234567","Test reminder"))
    assert result["status"]=="failed"
    assert "sender name or API key" in result["error"]


def test_subaccount_setup_masks_account_number(client,monkeypatch):
    client.app.state.settings["paystack_key"]="sk_test_subaccount"
    captured={}
    class FakeClient:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,headers=None,json=None):
            captured.update(json)
            return FakeResponse({"status":True,"data":{"id":42,"subaccount_code":"ACCT_testmerchant",
                "domain":"test","currency":"GHS","active":True,"is_verified":True,
                "business_name":"Raphael Test Store","settlement_bank":"MTN MoMo",
                "account_name":"R TEST"}})
    monkeypatch.setattr(provider_accounts.httpx,"AsyncClient",FakeClient)
    result=client.post("/api/integrations/paystack/subaccount",json={
        "business_name":"Raphael Test Store","settlement_bank":"MTN",
        "account_number":"0241234567","contact_name":"Raphael",
        "contact_email":"owner@example.com","contact_phone":"0241234567","confirmed":True})
    assert result.status_code==200,result.text
    assert captured["percentage_charge"]==0
    assert result.json()["account"]["account_last4"]=="4567"
    assert "0241234567" not in result.text
    with client.app.state.engine.connect() as conn:
        saved=dict(conn.execute(select(provider_accounts_table)).mappings().one())
    assert saved["account_last4"]=="4567"
    assert "0241234567" not in str(saved)


def test_live_checkout_uses_active_merchant_subaccount_and_customer_email(client,debt,monkeypatch):
    client.app.state.settings.update(paystack_key="sk_live_example",enable_live_payments=True,
        paystack_account_approved=True,secure_cookie=True,base_url="https://ledger.example")
    merchant_id=client.get("/api/me").json()["id"]
    with write(client.app.state.engine) as conn:
        conn.execute(provider_accounts_table.insert().values(merchant_id=merchant_id,provider="paystack",
            subaccount_code="ACCT_live_merchant",provider_account_id="77",domain="live",status="active",
            business_name="Raphael Test Store",settlement_bank="MTN MoMo",account_name="R TEST",
            account_last4="4567",created_at=now(),updated_at=now()))
    captured={}
    class FakeClient:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,headers=None,json=None):
            captured.update(json)
            return FakeResponse({"status":True,"data":{"authorization_url":"https://checkout.paystack.com/live-test"}})
    monkeypatch.setattr(main.httpx,"AsyncClient",FakeClient)
    response=client.post("/api/public/"+debt["public_token"]+"/checkout",json={
        "amount":"10","mode":"paystack_live","idempotency_key":"live-checkout-001",
        "email":"customer@example.com"})
    assert response.status_code==200,response.text
    assert captured["email"]=="customer@example.com"
    assert captured["subaccount"]=="ACCT_live_merchant"
    assert captured["bearer"]=="subaccount"
    assert "channels" not in captured
    with client.app.state.engine.connect() as conn:
        saved=conn.execute(select(payments).where(payments.c.reference==response.json()["reference"])).mappings().one()
    assert saved["source"]=="paystack_live"


def test_live_key_never_advertises_or_accepts_test_checkout(client,debt):
    client.app.state.settings.update(paystack_key="sk_live_example",paystack_email="test@example.com",
        enable_live_payments=True,paystack_account_approved=True,secure_cookie=True,
        base_url="https://ledger.example")
    config=client.get('/api/config').json()
    assert config['paystack_test_configured'] is False
    assert config['paystack_live_configured'] is True
    assert config['payment_mode']=='live'
    response=client.post('/api/public/'+debt['public_token']+'/checkout',json={
        'amount':'10','mode':'paystack_test','idempotency_key':'wrong-mode-001'})
    assert response.status_code==503
