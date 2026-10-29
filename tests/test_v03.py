import asyncio
import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app.db import write,susu_payments,contributions,sms_attempts,sms_reports,sms_callback_inbox,entries
from app.domain import uid,now,today
from app.savings import confirm_contribution
from app.jobs import process_webhooks,submit_sandbox
from app.sms_callbacks import process_callbacks

@pytest.fixture
def account(client):
    r=client.post('/api/susu',json={'name':'Abena Mensah','phone':'0241234567',
        'label':'Daily savings','daily_target':'20'})
    assert r.status_code==201
    return r.json()

def savings_checkout(client,a,amount='20',key='savings-checkout-001',mode='simulation'):
    return client.post('/api/susu-public/'+a['public_token']+'/checkout',json={
        'amount':amount,'mode':mode,'idempotency_key':key})

def test_contribution_pending_confirmation_and_duplicate_replay_do_not_touch_debt(client,debt,account):
    r=savings_checkout(client,account);ref=r.json()['reference']
    assert client.get('/api/susu-public/'+account['public_token']).json()['balance_minor']==0
    url='/api/susu-public/'+account['public_token']+'/simulate/'+ref
    assert client.post(url).json()['savings_balance_minor']==2000
    assert client.post(url).json()['duplicate']
    assert client.get('/api/debts/'+debt['id']).json()['balance_minor']==50000
    assert len(client.get('/api/susu').json()[0]['entries'])==1
    assert savings_checkout(client,account,'21').status_code==409

def test_savings_multiple_contributions_and_payout(client,account):
    for index in range(2):
        ref=savings_checkout(client,account,'20',f'contribution-key-{index}').json()['reference']
        client.post('/api/susu-public/'+account['public_token']+'/simulate/'+ref)
    result=client.post('/api/susu/'+account['id']+'/entries',json={
        'amount':'15','kind':'payout','reference':'payout-after-online','note':'Cash paid out externally'})
    assert result.json()['balance_minor']==2500
    assert client.get('/api/susu-public/'+account['public_token']).json()['balance_minor']==2500

def test_savings_public_link_has_no_phone_or_merchant_id_and_rotates(client,account):
    d=client.get('/api/susu-public/'+account['public_token']).json()
    assert 'phone' not in d and 'merchant_id' not in d
    new=client.post('/api/susu/'+account['id']+'/rotate-link').json()['public_token']
    assert client.get('/api/susu-public/'+account['public_token']).status_code==404
    assert client.get('/api/susu-public/'+new).status_code==200

def test_savings_links_and_simulations_cannot_cross_accounts(client,account,app):
    second=client.post('/api/susu',json={'name':'Other Person','phone':'0241119999',
        'label':'Other savings','daily_target':'10'}).json()
    ref=savings_checkout(client,account).json()['reference']
    assert client.post('/api/susu-public/'+second['public_token']+'/simulate/'+ref).status_code==404
    with TestClient(app) as other:
        other.post('/api/auth/register',json={'name':'Other Store','login':'other-v03','password':'Other-Test-2026'})
        assert other.get('/api/susu').json()==[]
        assert other.post('/api/susu/'+account['id']+'/rotate-link').status_code==404

def test_concurrent_savings_confirmation_once(client,account):
    ref=savings_checkout(client,account).json()['reference']
    def confirm(_):
        with write(client.app.state.engine) as conn:return confirm_contribution(conn,ref)
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(confirm,range(4)))
    assert sum(not r['duplicate'] for r in results)==1
    assert client.get('/api/susu-public/'+account['public_token']).json()['balance_minor']==2000

def test_paystack_savings_initialization_verification_and_webhook(client,debt,account,monkeypatch):
    client.app.state.settings.update(paystack_key='sk_test_v03',paystack_email='test@example.com')
    captured={}
    class FakeClient:
        def __init__(self,**k):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        async def post(self,url,headers,json):
            captured.update(json)
            return httpx.Response(200,request=httpx.Request('POST',url),json={
                'status':True,'data':{'authorization_url':'https://checkout.paystack.com/test-contribution'}})
        async def get(self,url,headers):
            return httpx.Response(200,request=httpx.Request('GET',url),json={'status':True,'data':{
                'reference':captured['reference'],'amount':captured['amount'],'currency':'GHS','domain':'test',
                'status':'success','metadata':captured['metadata']}})
    import app.main as main
    monkeypatch.setattr(main.httpx,'AsyncClient',FakeClient)
    result=savings_checkout(client,account,'50','provider-savings-01','paystack_test')
    assert result.status_code==200
    assert captured['metadata']['purpose']=='susu_contribution' and 'debt_id' not in captured['metadata']
    assert 'channels' not in captured
    ref=result.json()['reference']
    event=json.dumps({'event':'charge.success','data':{'reference':ref}}).encode()
    sig=hmac.new(b'sk_test_v03',event,hashlib.sha512).hexdigest()
    assert client.post('/api/webhooks/paystack',content=event,headers={'x-paystack-signature':sig}).json()['queued']
    assert asyncio.run(process_webhooks(client.app))['processed']==1
    assert client.get('/api/susu-public/'+account['public_token']).json()['balance_minor']==5000
    assert client.get('/api/debts/'+debt['id']).json()['balance_minor']==50000
    # Alter the provider purpose: no new savings credit is permitted.
    captured['metadata']['purpose']='debt_repayment'
    assert client.post('/api/susu-public/'+account['public_token']+'/verify/'+ref).status_code==409

def test_manual_savings_cannot_reserve_provider_reference(client,account):
    assert client.post('/api/susu/'+account['id']+'/entries',json={'amount':'20',
        'kind':'contribution','reference':'susu-payment-reserved','note':'Bad reserved reference'}).status_code==422

@pytest.fixture
def sms(client):
    d=client.post('/api/debts',json={'name':'SMS Person','phone':'0241112222',
        'amount':'300','due_date':today().isoformat()}).json()
    client.post('/api/reminders/preview')
    reminder=client.get('/api/reminders').json()[0]
    mid=client.get('/api/me').json()['id'];aid=uid()
    with write(client.app.state.engine) as conn:
        conn.execute(sms_attempts.insert().values(id=aid,merchant_id=mid,outbox_id=reminder['id'],
            mode='arkesel_sandbox',status='sandbox_accepted',provider_id='sms-v03-test',started_at=now(),finished_at=now()))
    client.app.state.settings.update(arkesel_callbacks=True,arkesel_key='fake-sms-key')
    return {'debt':d,'attempt_id':aid,'reminder_id':reminder['id'],'provider_id':'sms-v03-test'}

def callback(client,sms,status='DELIVERED'):
    return client.get('/api/webhooks/arkesel',params={'sms_id':sms['provider_id'],'status':status})

def test_forged_callback_status_is_only_a_hint_not_delivery_truth(client,sms,monkeypatch):
    assert callback(client,sms).json()['queued']
    assert callback(client,sms).json()['duplicate']
    assert client.get('/api/reminders').json()[0]['attempts'][0]['delivery_report'] is None
    import app.sms_callbacks as callbacks
    async def actual(settings,provider_id):return {'status':'QUEUED','recipient':sms['debt']['phone']}
    monkeypatch.setattr(callbacks,'provider_status',actual)
    assert asyncio.run(process_callbacks(client.app))['processed']==1
    r=client.get('/api/reminders').json()[0]['attempts'][0]
    assert r['status']=='sandbox_accepted'
    assert r['delivery_report']['status']=='QUEUED'
    assert r['delivery_report']['source']=='authenticated_provider_lookup'

def test_unknown_sms_id_and_unrecognized_status_do_not_change_reports(client,sms):
    assert client.get('/api/webhooks/arkesel',params={'sms_id':'not-ours','status':'DELIVERED'}).json()['ignored']
    assert callback(client,sms,'INVENTED').json()['ignored']
    assert client.get('/api/integrations/sms-callbacks').json()==[]

def test_callback_terminal_status_cannot_regress(client,sms,monkeypatch):
    import app.sms_callbacks as callbacks
    actual={'status':'DELIVERED','recipient':sms['debt']['phone']}
    async def lookup(*args):return actual
    monkeypatch.setattr(callbacks,'provider_status',lookup)
    callback(client,sms);asyncio.run(process_callbacks(client.app))
    actual['status']='QUEUED'
    callback(client,sms,'QUEUED')
    assert asyncio.run(process_callbacks(client.app))['needs_review']==1
    assert client.get('/api/reminders').json()[0]['attempts'][0]['delivery_report']['status']=='DELIVERED'

def test_wrong_provider_recipient_is_rejected(client,sms,monkeypatch):
    import app.sms_callbacks as callbacks
    async def lookup(*args):return {'status':'DELIVERED','recipient':'+233200000000'}
    monkeypatch.setattr(callbacks,'provider_status',lookup)
    callback(client,sms)
    assert asyncio.run(process_callbacks(client.app))['needs_review']==1
    assert client.get('/api/reminders').json()[0]['attempts'][0]['delivery_report'] is None

def test_actual_sms_lookup_contract_is_authenticated(client,sms,monkeypatch):
    captured={}
    class FakeClient:
        def __init__(self,**k):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        async def get(self,url,headers):
            captured.update(url=url,headers=headers)
            return httpx.Response(200,request=httpx.Request('GET',url),json={'status':'success','data':{
                'id':sms['provider_id'],'status':'SUBMITTED','recipient':sms['debt']['phone']}})
    import app.sms_callbacks as callbacks
    monkeypatch.setattr(callbacks.httpx,'AsyncClient',FakeClient)
    callback(client,sms,'SUBMITTED');asyncio.run(process_callbacks(client.app))
    assert captured['url'].endswith('/api/v2/sms/'+sms['provider_id'])
    assert captured['headers']['api-key']=='fake-sms-key'

def test_sms_lookup_retry_requires_owner_and_reason_and_does_not_resend(client,sms,monkeypatch,app):
    import app.sms_callbacks as callbacks
    async def lookup(*args):return {'status':'QUEUED','recipient':sms['debt']['phone']}
    monkeypatch.setattr(callbacks,'provider_status',lookup)
    callback(client,sms);asyncio.run(process_callbacks(app))
    job=client.get('/api/integrations/sms-callbacks').json()[0]
    url='/api/integrations/sms-callbacks/'+job['id']+'/retry'
    with TestClient(app) as other:
        other.post('/api/auth/register',json={'name':'Other Store','login':'other-sms-status','password':'Other-Test-2026'})
        assert other.post(url,json={'reason':'Check provider status again'}).status_code==404
    assert client.post(url,json={'reason':'x'}).status_code==422
    assert client.post(url,json={'reason':'Recheck after provider delay'}).json()['queued']
    assert client.post(url,json={'reason':'Repeated lookup request'}).status_code==409
    assert asyncio.run(process_callbacks(app))['processed']==1
    with app.state.engine.connect() as conn:
        assert conn.scalar(select(func.count()).select_from(sms_attempts))==1

def test_sms_transient_lookup_failure_never_uses_callback_claim(client,sms,monkeypatch):
    import app.sms_callbacks as callbacks
    from fastapi import HTTPException
    async def unavailable(*args):raise HTTPException(502,'Provider unavailable')
    monkeypatch.setattr(callbacks,'provider_status',unavailable)
    callback(client,sms,'DELIVERED')
    assert asyncio.run(process_callbacks(client.app))['retry']==1
    assert client.get('/api/reminders').json()[0]['attempts'][0]['delivery_report'] is None

def test_callback_url_added_to_https_sandbox_submission(client,monkeypatch):
    captured={}
    class FakeClient:
        def __init__(self,**k):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        async def post(self,url,headers,json):
            captured.update(json)
            return httpx.Response(200,request=httpx.Request('POST',url),json={'status':'success','data':[{'id':'msg-test'}]})
    import app.jobs as jobs
    monkeypatch.setattr(jobs.httpx,'AsyncClient',FakeClient)
    settings={**client.app.state.settings,'arkesel_key':'fake','arkesel_callbacks':True,'base_url':'https://test.example'}
    asyncio.run(submit_sandbox(settings,'+233241234567','Test reminder'))
    assert captured['callback_url']=='https://test.example/api/webhooks/arkesel'
    assert captured['sandbox'] is True

def change(client,d,**fields):
    body={'kind':'adjustment','amount':'100','direction':'increase','reason':'Correct the recorded sale amount',
        'request_key':'correction-key-001','expected_balance_minor':d['balance_minor']}
    body.update(fields)
    return client.post('/api/debts/'+d['id']+'/changes',json=body)

def test_adjustment_changes_amount_owed_without_inventing_repayments(client,debt):
    assert change(client,debt).json()['balance_minor']==60000
    assert change(client,debt).json()['duplicate']
    d=client.get('/api/debts/'+debt['id']).json()
    assert d['principal_minor']==50000 and d['adjusted_total_minor']==60000
    assert d['paid_minor']==0 and d['balance_minor']==60000
    assert len(d['ledger'])==2
    assert change(client,debt,amount='101').status_code==409

def test_due_date_correction_retains_previous_value_and_reason(client,debt):
    assert change(client,debt,kind='due_date',due_date='2026-11-20',amount=None,direction=None).status_code==200
    history=client.get('/api/debts/'+debt['id']+'/changes').json()
    payload=json.loads(history[0]['payload'])
    assert payload['before']['due_date']==debt['due_date']
    assert payload['request']['due_date']=='2026-11-20'

def test_correction_stale_balance_and_negative_balance_are_rejected(client,debt):
    assert change(client,debt,expected_balance_minor=1).status_code==409
    assert change(client,debt,amount='501',direction='decrease').status_code==422
    assert client.get('/api/debts/'+debt['id']).json()['balance_minor']==50000

def test_cancellation_preserves_history_and_does_not_claim_collection(client,debt):
    assert change(client,debt,kind='cancel',amount=None,direction=None).status_code==200
    d=client.get('/api/debts/'+debt['id']).json()
    assert d['status']=='cancelled' and d['balance_minor']==0 and d['paid_minor']==0
    assert [e['kind'] for e in d['ledger']]==['opening','cancellation']
    assert client.get('/api/dashboard').json()['collected_minor']==0

def test_cancellation_with_pending_payment_is_rejected(client,debt):
    client.post('/api/public/'+debt['public_token']+'/checkout',json={
        'amount':'100','mode':'simulation','idempotency_key':'pending-before-cancel'})
    assert change(client,debt,kind='cancel',amount=None,direction=None).status_code==409

def test_payment_recovery_reverification_scoped_to_owner(client,debt,app,monkeypatch):
    mid=client.get('/api/me').json()['id'];pid=uid()
    from app.db import payments
    with write(app.state.engine) as conn:
        conn.execute(payments.insert().values(id=pid,merchant_id=mid,debt_id=debt['id'],reference='sika-recovery-test',
            amount_minor=10000,applied_minor=0,unallocated_minor=0,source='paystack_test',status='pending',created_at=now()))
    async def fake(ref):assert ref=='sika-recovery-test';return {'status':'pending','balance_unchanged':True}
    monkeypatch.setattr(app.state,'verify_paystack',fake)
    assert len(client.get('/api/payment-recovery').json())==1
    assert client.post('/api/payment-recovery/debt/'+pid+'/verify').json()['balance_unchanged']
    with TestClient(app) as other:
        other.post('/api/auth/register',json={'name':'Other Store','login':'other-recovery','password':'Other-Test-2026'})
        assert other.get('/api/payment-recovery').json()==[]
        assert other.post('/api/payment-recovery/debt/'+pid+'/verify').status_code==404
        assert change(other,debt).status_code==404

def test_reports_distinguish_adjustments_repayments_and_current_aging(client,debt):
    change(client,debt)
    client.post('/api/debts/'+debt['id']+'/payments',json={'amount':'200','source':'cash',
        'idempotency_key':'report-payment-01','note':'Cash received'})
    r=client.get('/api/reports/collections',params={'start':today().isoformat(),'end':today().isoformat()}).json()
    assert r['new_credit_minor']==50000 and r['adjustments_minor']==10000
    assert r['net_recorded_repayments_minor']==20000 and r['outstanding_now_minor']==40000
    assert sum(r['aging'].values())==40000
    statement=client.get('/api/reports/customers/'+debt['customer_id']+'/statement')
    assert 'TOTAL OUTSTANDING: GHS 400.00' in statement.text
    assert 'adjustment' in statement.text
    assert client.get('/api/reports/collections',params={'start':'2026-11-02','end':'2026-11-01'}).status_code==422

def test_excess_credit_review_and_statement_are_scoped_and_leave_money_unchanged(client,debt,app):
    refs=[]
    for i in range(2):
        refs.append(client.post('/api/public/'+debt['public_token']+'/checkout',json={
            'amount':'400','mode':'simulation','idempotency_key':f'credit-review-payment-{i}'}).json()['reference'])
    for ref in refs:client.post('/api/public/'+debt['public_token']+'/simulate/'+ref)
    payment=next(p for p in client.get('/api/payment-recovery').json() if p['unallocated_minor'])
    url='/api/payment-recovery/debt/'+payment['id']+'/review'
    statement='/api/reports/customers/'+debt['customer_id']+'/statement'
    with TestClient(app) as other:
        other.post('/api/auth/register',json={'name':'Other Store','login':'other-credit-review','password':'Other-Test-2026'})
        assert other.post(url,json={'reason':'Attempt to review another tenant'}).status_code==404
        assert other.get(statement).status_code==404
    assert client.post(url,json={'reason':'Discuss excess credit with customer'}).json()['reviewed']
    after=next(p for p in client.get('/api/payment-recovery').json() if p['id']==payment['id'])
    assert after['unallocated_minor']==30000 and after['review']['note']=='Discuss excess credit with customer'
    assert client.get('/api/debts/'+debt['id']).json()['balance_minor']==0

def test_empty_report_period_has_zero_activity_but_current_balances(client,debt):
    day=(today()-timedelta(days=30)).isoformat()
    r=client.get('/api/reports/collections',params={'start':day,'end':day}).json()
    assert r['new_credit_minor']==0 and r['net_recorded_repayments_minor']==0
    assert r['outstanding_now_minor']==50000
