import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app import agent
from app.db import write, payments, debts, entries, proposals
from app.domain import today, balance, uid, create_debt

def manual(client,debt,amount='200.00',key='manual-key-0001'):
    return client.post('/api/debts/'+debt['id']+'/payments',json={
        'amount':amount,'source':'cash','idempotency_key':key,'note':'Cash received and counted'})

def checkout(client,debt,amount='200.00',key='checkout-key-001'):
    return client.post('/api/public/'+debt['public_token']+'/checkout',json={
        'amount':amount,'mode':'simulation','idempotency_key':key})

def simulate(client,debt,ref):
    return client.post('/api/public/'+debt['public_token']+'/simulate/'+ref)

def test_registration_login_logout_and_private_access(client):
    assert client.get('/api/me').json()['name']=='Raphael Test Store'
    client.post('/api/auth/logout')
    assert client.get('/api/debts').status_code==401
    assert client.post('/api/auth/login',json={'login':'raphael','password':'wrong-pass-2026'}).status_code==401
    assert client.post('/api/auth/login',json={'login':'raphael','password':'Prototype-Test-2026'}).status_code==200

@pytest.mark.parametrize('amount',['0','-1','1.001','NaN','Infinity','1000000.01'])
def test_exact_money_validation(client,amount):
    r=client.post('/api/debts',json={'name':'Abena','phone':'0241234567',
        'amount':amount,'due_date':'2026-11-07'})
    assert r.status_code==422
    assert client.get('/api/debts').json()==[]

def test_initial_payment_and_phone_normalisation(client):
    r=client.post('/api/debts',json={'name':'Abena','phone':'024 123 4567',
        'amount':'500','initial_paid':'200','due_date':'2026-11-07'})
    d=r.json()
    assert d['phone']=='+233241234567'
    assert d['balance_minor']==30000
    detail=client.get('/api/debts/'+d['id']).json()
    assert len(detail['ledger'])==2
    assert detail['payments'][0]['source']=='manual_initial'

def test_invalid_initial_payment_rolls_back(client):
    r=client.post('/api/debts',json={'name':'Abena','phone':'0241234567',
        'amount':'500','initial_paid':'600','due_date':'2026-11-07'})
    assert r.status_code==422
    assert client.get('/api/dashboard').json()['customers']==0

def test_multiple_debts_per_customer_and_name_collision(client,debt):
    r=client.post('/api/debts',json={'name':'  '+debt['name'].replace(' ','   ')+'  ','phone':debt['phone'],
        'amount':'100','due_date':'2026-11-08'})
    assert r.status_code==201
    assert client.get('/api/dashboard').json()['customers']==1
    r=client.post('/api/debts',json={'name':'Someone Else','phone':debt['phone'],
        'amount':'100','due_date':'2026-11-08'})
    assert r.status_code==409

def test_manual_partial_payment_idempotency_and_reversal(client,debt):
    r=manual(client,debt)
    assert r.status_code==200
    assert r.json()['balance_minor']==30000
    assert manual(client,debt).json()['duplicate'] is True
    assert manual(client,debt,'201').status_code==409
    pid=r.json()['payment_id']
    for _ in range(2):
        assert client.post('/api/payments/'+pid+'/reverse',json={'reason':'Mistaken cash entry corrected'}).status_code==200
    d=client.get('/api/debts/'+debt['id']).json()
    assert d['balance_minor']==50000
    assert [e['kind'] for e in d['ledger']]==['opening','repayment','reversal']

def test_overpayment_rejected_for_manual_entry(client,debt):
    assert manual(client,debt,'501').status_code==422
    assert client.get('/api/debts/'+debt['id']).json()['balance_minor']==50000

def test_checkout_does_not_change_balance_until_confirmed(client,debt):
    ref=checkout(client,debt).json()['reference']
    assert client.get('/api/public/'+debt['public_token']).json()['balance_minor']==50000
    assert simulate(client,debt,ref).json()['balance_minor']==30000
    assert simulate(client,debt,ref).json()['duplicate'] is True
    assert client.get('/api/public/'+debt['public_token']).json()['balance_minor']==30000

def test_two_pending_payments_allocate_excess_without_negative_debt(client,debt):
    ref1=checkout(client,debt,'400','checkout-first-1').json()['reference']
    ref2=checkout(client,debt,'400','checkout-second-2').json()['reference']
    assert simulate(client,debt,ref1).json()['balance_minor']==10000
    result=simulate(client,debt,ref2).json()
    assert result['balance_minor']==0
    assert result['unallocated_minor']==30000
    assert client.get('/api/dashboard').json()['unallocated_minor']==30000

def test_concurrent_confirmation_is_idempotent(client,debt,app):
    ref=checkout(client,debt).json()['reference']
    from app.domain import confirm_payment
    def confirm():
        with write(app.state.engine) as conn:return confirm_payment(conn,ref)
    with ThreadPoolExecutor(max_workers=5) as pool:
        results=list(pool.map(lambda _:confirm(),range(5)))
    assert sum(not r['duplicate'] for r in results)==1
    assert client.get('/api/debts/'+debt['id']).json()['balance_minor']==30000

def test_cross_merchant_access_blocked(client,debt,app):
    with TestClient(app) as other:
        other.post('/api/auth/register',json={'name':'Second Store','login':'second','password':'Different-Test-2026'})
        assert other.get('/api/debts').json()==[]
        assert other.get('/api/debts/'+debt['id']).status_code==404
        assert manual(other,debt).status_code==404
        assert other.post('/api/debts/'+debt['id']+'/pause',json={'paused':True}).status_code==404

def test_public_link_limits_data_and_does_not_accept_arbitrary_reference(client,debt):
    public=client.get('/api/public/'+debt['public_token']).json()
    assert 'phone' not in public and 'public_token' not in public and 'merchant_id' not in public
    assert client.get('/api/public/guessable-invalid-link').status_code==404
    assert simulate(client,debt,'unrelated-reference').status_code==404

def test_dispute_pauses_checkout_and_reminders(client,debt):
    r=client.post('/api/public/'+debt['public_token']+'/dispute',json={'reason':'incorrect_balance','message':'I already paid this in cash.'})
    assert r.status_code==200
    assert checkout(client,debt).status_code==409
    assert client.get('/api/debts/'+debt['id']).json()['disputed'] is True
    sid=client.get('/api/disputes').json()[0]['id']
    client.post('/api/disputes/'+sid+'/resolve',json={'reason':'Checked the receipt with the customer.'})
    d=client.get('/api/debts/'+debt['id']).json()
    assert not d['disputed'] and d['paused']

def test_wrong_number_opt_out_applies_to_all_customer_debts(client,debt):
    client.post('/api/debts',json={'name':debt['name'],'phone':debt['phone'],'amount':'100',
        'due_date':today().isoformat()})
    client.post('/api/public/'+debt['public_token']+'/dispute',json={'reason':'wrong_number','message':'Not my number.'})
    assert all(d['do_not_contact'] for d in client.get('/api/debts').json())
    assert client.post('/api/reminders/preview').json()['created']==0

def test_reminder_preview_rechecks_current_balance_and_deduplicates(client):
    d=client.post('/api/debts',json={'name':'Abena','phone':'0241234567',
        'amount':'500','due_date':today().isoformat()}).json()
    assert client.post('/api/reminders/preview').json()['created']==1
    assert client.post('/api/reminders/preview').json()['created']==0
    assert '500.00' in client.get('/api/reminders').json()[0]['body']
    manual(client,d)
    assert client.get('/api/reminders').json()[0]['status']=='stale'
    assert client.post('/api/reminders/preview').json()['created']==1
    assert '300.00' in client.get('/api/reminders').json()[0]['body']
    manual(client,d,'300','manual-final-002')
    assert client.post('/api/reminders/preview').json()['created']==0

def test_susu_is_separate_with_payout_limits_and_idempotency(client,debt):
    sid=client.post('/api/susu',json={'name':'Abena Mensah','phone':'0241234567',
        'label':'Daily savings','daily_target':'20'}).json()['id']
    data={'amount':'100','kind':'contribution','reference':'contribution-0001','note':'Cash received'}
    assert client.post('/api/susu/'+sid+'/entries',json=data).json()['balance_minor']==10000
    assert client.post('/api/susu/'+sid+'/entries',json=data).json()['duplicate'] is True
    data={'amount':'101','kind':'payout','reference':'payout-test-0001','note':'Cash paid out'}
    assert client.post('/api/susu/'+sid+'/entries',json=data).status_code==422
    data['amount']='40'
    assert client.post('/api/susu/'+sid+'/entries',json=data).json()['balance_minor']==6000
    assert client.get('/api/debts/'+debt['id']).json()['balance_minor']==50000

def test_csv_formula_injection_escaped(client):
    client.post('/api/debts',json={'name':'=HYPERLINK("bad")','phone':'0241234567',
        'amount':'500','due_date':'2026-11-07'})
    result=client.get('/api/export/debts.csv')
    assert result.status_code==200
    assert "'=HYPERLINK" in result.text

def test_csrf_origin_check(client):
    assert client.post('/api/auth/logout',headers={'Origin':'https://attacker.example'}).status_code==403

def test_missing_gemini_key_is_honest_and_does_not_mutate(client):
    result=client.post('/api/agent',json={'message':'Record a debt','consent':True})
    assert result.status_code==503 and 'not configured' in result.json()['detail']
    assert client.get('/api/debts').json()==[]

def plan(**overrides):
    data=dict(action='propose_debt',reply='Review this draft.',customer_name='Kofi',
        phone='0241234567',amount='600',initial_paid='200',due_date='2026-11-07',
        description='Rice',debt_id='')
    data.update(overrides)
    return agent.Plan(**data)

def test_gemini_proposal_requires_confirmation_and_is_single_use(client,monkeypatch):
    async def fake(*args):return plan()
    monkeypatch.setattr(agent,'gemini_plan',fake)
    r=client.post('/api/agent',json={'message':'Kofi owes 600, paid 200','consent':True})
    assert r.status_code==200
    proposal=r.json()['proposal']
    assert proposal['balance_minor']==40000
    assert client.get('/api/debts').json()==[]
    result=client.post('/api/agent/proposals/'+proposal['id']+'/confirm')
    assert result.status_code==200 and result.json()['balance_minor']==40000
    assert client.post('/api/agent/proposals/'+proposal['id']+'/confirm').status_code==409

def test_agent_cannot_use_another_merchants_debt_for_reminders(client,monkeypatch):
    async def fake(*args):return plan(action='draft_reminder',debt_id='another-business-debt')
    monkeypatch.setattr(agent,'gemini_plan',fake)
    r=client.post('/api/agent',json={'message':'Draft a reminder','consent':True})
    assert r.status_code==422

def test_agent_consent_required(client):
    assert client.post('/api/agent',json={'message':'Check my balances','consent':False}).status_code==422

def test_unknown_agent_action_rejected():
    with pytest.raises(ValueError):plan(action='mark_payment_successful')

def test_gemini_http_contract_and_no_saved_contact_or_token_in_context(client,debt,monkeypatch):
    captured={}
    class FakeClient:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,headers,json):
            captured.update(url=url,headers=headers,payload=json)
            return httpx.Response(200,request=httpx.Request('POST',url),json={
                'candidates':[{'content':{'parts':[{'text':plan(action='portfolio_summary').model_dump_json()}]}}]})
    monkeypatch.setattr(agent.httpx,'AsyncClient',FakeClient)
    client.app.state.settings['gemini_key']='fake-key-for-unit-test'
    r=client.post('/api/agent',json={'message':'What is outstanding?','consent':True})
    assert r.status_code==200 and '500.00' in r.json()['reply']
    ctx=json.loads(captured['payload']['contents'][0]['parts'][0]['text'])['merchant_context']
    assert 'phone' not in ctx[0] and 'public_token' not in ctx[0]
    assert captured['payload']['generationConfig']['responseFormat']['text']['mimeType']=='APPLICATION_JSON'

def test_webhook_signature_and_verified_matching_amount(client,debt,monkeypatch):
    client.app.state.settings['paystack_key']='sk_test_fake'
    client.app.state.settings['paystack_email']='test@example.com'
    with write(client.app.state.engine) as conn:
        conn.execute(payments.insert().values(id=uid(),merchant_id=client.get('/api/me').json()['id'],
            debt_id=debt['id'],reference='sika-test-verify-0001',amount_minor=20000,
            applied_minor=0,unallocated_minor=0,source='paystack_test',status='pending',created_at='2026-10-26'))
    event={'event':'charge.success','data':{'reference':'sika-test-verify-0001'}}
    raw=json.dumps(event).encode()
    assert client.post('/api/webhooks/paystack',content=raw,headers={'x-paystack-signature':'invalid'}).status_code==401
    good={'status':'success','reference':'sika-test-verify-0001','amount':20000,'currency':'GHS','domain':'test',
        'metadata':{'merchant_id':client.get('/api/me').json()['id'],'debt_id':debt['id']}}
    class FakeClient:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get(self,url,headers):
            return httpx.Response(200,request=httpx.Request('GET',url),json={'status':True,'data':good})
    import app.main as main
    monkeypatch.setattr(main.httpx,'AsyncClient',FakeClient)
    signature=hmac.new(b'sk_test_fake',raw,hashlib.sha512).hexdigest()
    good['amount']=19999
    import asyncio
    from app.jobs import process_webhooks
    assert client.post('/api/webhooks/paystack',content=raw,headers={'x-paystack-signature':signature}).status_code==200
    assert client.get('/api/debts/'+debt['id']).json()['balance_minor']==50000
    assert asyncio.run(process_webhooks(client.app))['needs_review']==1
    assert client.get('/api/debts/'+debt['id']).json()['balance_minor']==50000
    good['amount']=20000
    jid=client.get('/api/integrations/webhooks').json()[0]['id']
    assert client.post('/api/integrations/webhooks/'+jid+'/retry',json={'reason':'Checked the test provider transaction amount.'}).status_code==200
    for _ in range(2):
        assert client.post('/api/webhooks/paystack',content=raw,headers={'x-paystack-signature':signature}).status_code==200
    assert asyncio.run(process_webhooks(client.app))['processed']==1
    assert client.get('/api/debts/'+debt['id']).json()['balance_minor']==30000

def test_live_keys_are_blocked(tmp_path):
    from app.main import create_app
    with pytest.raises(RuntimeError,match='TEST keys only'):
        create_app('sqlite:///'+str(tmp_path/'blocked.db'),{'paystack_key':'sk_live_blocked'})
