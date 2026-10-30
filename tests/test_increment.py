import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from app import agent
from app.db import (write, payments, webhook_inbox, outbox, sms_attempts,
                    conversations, turns, proposals, meta, contact_preferences)
from app.domain import uid, now, today
from app.jobs import process_webhooks, process_sms, run_once, future
from app.main import create_app

def configure_payment(client,debt):
    app=client.app
    app.state.settings['paystack_key']='sk_test_increment'
    mid=client.get('/api/me').json()['id']
    ref='sika-increment-'+uid()
    with write(app.state.engine) as conn:
        conn.execute(payments.insert().values(id=uid(),merchant_id=mid,debt_id=debt['id'],
            reference=ref,amount_minor=20000,applied_minor=0,unallocated_minor=0,
            source='paystack_test',status='pending',created_at=now()))
    return ref

def notify(client,ref):
    raw=json.dumps({'event':'charge.success','data':{'reference':ref}}).encode()
    signature=hmac.new(b'sk_test_increment',raw,hashlib.sha512).hexdigest()
    return client.post('/api/webhooks/paystack',content=raw,headers={'x-paystack-signature':signature})

def current_preview(client):
    client.app.state.settings.update(reminder_start=0,reminder_end=24)
    d=client.post('/api/debts',json={'name':'Akua Example','phone':'0245557777',
        'amount':'500','due_date':today().isoformat()}).json()
    assert client.post('/api/customers/'+d['customer_id']+'/contact',json={'expected_phone':d['phone'],
        'phone':d['phone'],'sms_consent':True,'evidence':'Fictional customer granted test permission',
        'reason':'Verify sandbox recipient for this test'}).status_code==200
    client.post('/api/reminders/preview')
    return d,client.get('/api/reminders').json()[0]

def test_notification_is_durable_before_any_provider_call(client,debt,monkeypatch):
    ref=configure_payment(client,debt)
    async def forbidden(*args):raise AssertionError('No provider call during webhook intake')
    monkeypatch.setattr(client.app.state,'verify_paystack',forbidden)
    r=notify(client,ref)
    assert r.status_code==200 and r.json()['queued']
    assert notify(client,ref).json()['duplicate']
    jobs=client.get('/api/integrations/webhooks').json()
    assert len(jobs)==1 and jobs[0]['status']=='pending' and jobs[0]['attempts']==0
    assert client.get('/api/debts/'+debt['id']).json()['balance_minor']==50000

def test_transient_verification_schedules_retry_and_quiet_hours_do_not_block_it(client,debt,monkeypatch):
    ref=configure_payment(client,debt);notify(client,ref)
    async def unavailable(*args):raise HTTPException(502,'Temporary verification failure')
    monkeypatch.setattr(client.app.state,'verify_paystack',unavailable)
    hour=datetime.now(timezone.utc).hour
    client.app.state.settings.update(reminder_start=0 if hour else 1,reminder_end=1 if hour else 2)
    result=asyncio.run(run_once(client.app))['webhooks']
    assert result['retry']==1
    job=client.get('/api/integrations/webhooks').json()[0]
    assert job['status']=='retry' and job['attempts']==1 and job['next_attempt_at']>now()
    # Delay is respected and no second external attempt occurs immediately.
    assert asyncio.run(process_webhooks(client.app))['retry']==0

def test_queue_survives_application_restart(client,debt,tmp_path):
    ref=configure_payment(client,debt);notify(client,ref)
    url=client.app.state.settings['database_url']
    restored=create_app(url,{'demo':True,'worker_enabled':False,'paystack_key':'sk_test_increment'})
    async def verified(reference):
        assert reference==ref
        from app.domain import confirm_payment
        with write(restored.state.engine) as conn:return confirm_payment(conn,reference)
    restored.state.verify_paystack=verified
    assert asyncio.run(process_webhooks(restored))['processed']==1
    assert client.get('/api/debts/'+debt['id']).json()['balance_minor']==30000
    assert asyncio.run(process_webhooks(restored))['processed']==0
    restored.state.engine.dispose()

def test_invalid_signed_transaction_data_rejected(client,debt):
    configure_payment(client,debt)
    raw=json.dumps({'event':'charge.success','data':['not','an','object']}).encode()
    signature=hmac.new(b'sk_test_increment',raw,hashlib.sha512).hexdigest()
    assert client.post('/api/webhooks/paystack',content=raw,
        headers={'x-paystack-signature':signature}).status_code==400

def test_webhook_jobs_are_tenant_scoped(client,debt,app):
    ref=configure_payment(client,debt);notify(client,ref)
    jid=client.get('/api/integrations/webhooks').json()[0]['id']
    with TestClient(app) as other:
        other.post('/api/auth/register',json={'name':'Other Business','login':'otherjobs','password':'Other-Test-2026'})
        assert other.get('/api/integrations/webhooks').json()==[]
        assert other.post('/api/integrations/webhooks/'+jid+'/retry',json={'reason':'Please retry this job'}).status_code==404

def test_reminder_queue_dry_run_and_same_day_deduplication(client,monkeypatch):
    d,preview=current_preview(client)
    url='/api/reminders/'+preview['id']+'/queue'
    assert client.post(url).json()['queued']
    assert client.post(url).json()['duplicate']
    async def forbidden(*args):raise AssertionError('Dry run must never call provider')
    import app.jobs as jobs
    monkeypatch.setattr(jobs,'submit_sandbox',forbidden)
    assert asyncio.run(process_sms(client.app))['processed']==1
    rows=client.get('/api/reminders').json()
    assert rows[0]['status']=='dry_run_complete'
    assert rows[0]['attempts'][0]['mode']=='dry_run'
    assert client.post('/api/reminders/preview').json()['created']==0
    assert asyncio.run(process_sms(client.app))['processed']==0

def test_queued_payment_change_invalidates_then_regenerates_current_balance(client):
    d,preview=current_preview(client)
    client.post('/api/reminders/'+preview['id']+'/queue')
    client.post('/api/debts/'+d['id']+'/payments',json={'amount':'200','source':'cash',
        'idempotency_key':'queued-payment-001','note':'Cash counted'})
    assert client.get('/api/reminders').json()[0]['status']=='stale'
    assert asyncio.run(process_sms(client.app))['processed']==0
    client.post('/api/reminders/preview')
    rows=client.get('/api/reminders').json()
    assert '300.00' in rows[0]['body']

def test_dispute_after_queue_prevents_submission(client):
    d,preview=current_preview(client)
    client.post('/api/reminders/'+preview['id']+'/queue')
    client.post('/api/public/'+d['public_token']+'/dispute',json={'reason':'wrong_number','message':'This is the wrong person.'})
    assert asyncio.run(process_sms(client.app))['processed']==0
    assert client.get('/api/reminders').json()[0]['status']=='stale'

def test_cancelled_pre_send_reminder_explains_reason_and_can_be_prepared_again(client):
    d,preview=current_preview(client)
    client.post('/api/reminders/'+preview['id']+'/queue')
    with write(client.app.state.engine) as conn:
        conn.execute(contact_preferences.update().where(
            contact_preferences.c.customer_id==d['customer_id']).values(sms_consent=0))
    client.app.state.settings.update(sms_mode='arkesel_live',arkesel_key='fake-live-key')
    assert asyncio.run(process_sms(client.app))['processed']==1
    attempt=client.get('/api/reminders').json()[0]['attempts'][0]
    assert attempt['status']=='cancelled'
    assert attempt['error']=='SMS permission is no longer valid for this customer number.'
    assert client.post('/api/customers/'+d['customer_id']+'/contact',json={'expected_phone':d['phone'],
        'phone':d['phone'],'sms_consent':True,'evidence':'Customer reconfirmed permission for this test',
        'reason':'Restore permission after a cancelled pre-send reminder'}).status_code==200
    assert client.post('/api/reminders/preview').json()['created']==1
    assert client.get('/api/reminders').json()[0]['status']=='preview'

def test_sms_submission_is_sandbox_only_and_records_acceptance_not_delivery(client,monkeypatch):
    d,preview=current_preview(client)
    client.app.state.settings.update(sms_mode='arkesel_sandbox',arkesel_key='fake-test-key')
    capture={}
    class FakeClient:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        async def post(self,url,headers,json):
            capture.update(url=url,headers=headers,payload=json)
            return httpx.Response(200,request=httpx.Request('POST',url),
                json={'status':'success','data':[{'recipient':d['phone'],'id':'sandbox-123'}]})
    import app.jobs as jobs
    monkeypatch.setattr(jobs.httpx,'AsyncClient',FakeClient)
    client.post('/api/reminders/'+preview['id']+'/queue')
    assert asyncio.run(process_sms(client.app))['processed']==1
    assert capture['payload']['sandbox'] is True
    assert capture['payload']['recipients']==[d['phone']]
    row=client.get('/api/reminders').json()[0]
    assert row['status']=='sandbox_accepted'
    assert row['attempts'][0]['provider_id']=='sandbox-123'

def test_ambiguous_sms_timeout_never_automatically_resends(client,monkeypatch):
    d,preview=current_preview(client)
    client.app.state.settings.update(sms_mode='arkesel_sandbox',arkesel_key='fake-key')
    calls=[]
    async def unknown(*a):calls.append(1);return {'status':'unknown','error':'Timed out'}
    import app.jobs as jobs
    monkeypatch.setattr(jobs,'submit_sandbox',unknown)
    client.post('/api/reminders/'+preview['id']+'/queue')
    asyncio.run(process_sms(client.app));asyncio.run(process_sms(client.app))
    assert len(calls)==1
    assert client.get('/api/reminders').json()[0]['status']=='unknown'
    assert client.post('/api/reminders/'+preview['id']+'/queue').status_code==409
    assert client.post('/api/reminders/preview').json()['created']==0

def test_crashed_sms_claim_becomes_unknown_without_resend(client):
    _,preview=current_preview(client)
    with write(client.app.state.engine) as conn:
        conn.execute(outbox.update().where(outbox.c.id==preview['id']).values(status='sending'))
        conn.execute(sms_attempts.insert().values(id=uid(),merchant_id=client.get('/api/me').json()['id'],
            outbox_id=preview['id'],mode='arkesel_sandbox',status='sending',started_at=future(-180)))
    assert asyncio.run(process_sms(client.app))['processed']==0
    assert client.get('/api/reminders').json()[0]['status']=='unknown'

def test_live_sms_mode_blocked(tmp_path):
    with pytest.raises(RuntimeError,match='Live SMS is blocked'):
        create_app('sqlite:///'+str(tmp_path/'live.db'),{'sms_mode':'live'})

def test_conversation_followup_history_idempotency_and_clear(client,monkeypatch):
    cid=client.post('/api/agent/conversations').json()['id']
    calls=[]
    async def fake(settings,message,context,history):
        calls.append(history)
        return agent.Plan(action='reply',reply='Please provide the phone and due date.',
            customer_name='',phone='',amount='',initial_paid='',due_date='',description='',debt_id='')
    monkeypatch.setattr(agent,'gemini_plan',fake)
    body={'message':'Kofi owes 600 cedis','consent':True,'conversation_id':cid,'request_id':'chat-request-0001'}
    first=client.post('/api/agent',json=body)
    assert first.status_code==200
    assert client.post('/api/agent',json=body).json()==first.json()
    assert len(calls)==1
    body.update(message='His phone is 0241234567, due next Friday',request_id='chat-request-0002')
    assert client.post('/api/agent',json=body).status_code==200
    assert calls[1][0]['user']=='Kofi owes 600 cedis'
    assert len(client.get('/api/agent/conversations/'+cid).json()['turns'])==2
    assert client.delete('/api/agent/conversations/'+cid).status_code==200
    assert client.get('/api/agent/conversations/'+cid).status_code==404

def test_conversation_cross_tenant_and_expired_access(client,app):
    cid=client.post('/api/agent/conversations').json()['id']
    with TestClient(app) as other:
        other.post('/api/auth/register',json={'name':'Other Store','login':'otherchat','password':'Other-Test-2026'})
        assert other.get('/api/agent/conversations/'+cid).status_code==404
        assert other.delete('/api/agent/conversations/'+cid).status_code==404
    with write(app.state.engine) as conn:
        conn.execute(conversations.update().where(conversations.c.id==cid).values(expires_at=future(-1)))
    assert client.get('/api/agent/conversations/'+cid).status_code==404

def test_failed_gemini_call_releases_conversation_for_retry(client):
    cid=client.post('/api/agent/conversations').json()['id']
    result=client.post('/api/agent',json={'message':'What is outstanding?','consent':True,'conversation_id':cid})
    assert result.status_code==503
    with client.app.state.engine.connect() as conn:
        c=conn.execute(select(conversations).where(conversations.c.id==cid)).mappings().one()
        assert c['busy_token'] is None
        assert conn.scalar(select(func.count()).select_from(turns).where(turns.c.conversation_id==cid))==0

def test_clearing_chat_cancels_unconfirmed_proposal(client,monkeypatch):
    from test_workflows import plan
    async def fake(*args):return plan()
    monkeypatch.setattr(agent,'gemini_plan',fake)
    r=client.post('/api/agent',json={'message':'Draft Kofi debt','consent':True}).json()
    client.delete('/api/agent/conversations/'+r['conversation_id'])
    assert client.post('/api/agent/proposals/'+r['proposal']['id']+'/confirm').status_code==409
    assert client.get('/api/debts').json()==[]

def test_link_rotation_invalidates_old_capability_and_preserves_ledger(client,debt):
    old=debt['public_token']
    result=client.post('/api/debts/'+debt['id']+'/rotate-link')
    assert result.status_code==200
    new=result.json()['public_token']
    assert new!=old
    assert client.get('/api/public/'+old).status_code==404
    assert client.get('/api/public/'+new).json()['balance_minor']==50000
    assert client.get('/api/debts/'+debt['id']).json()['balance_minor']==50000

def test_background_worker_expires_conversation_history(client):
    cid=client.post('/api/agent/conversations').json()['id']
    with write(client.app.state.engine) as conn:
        conn.execute(conversations.update().where(conversations.c.id==cid).values(expires_at=future(-1)))
    asyncio.run(run_once(client.app))
    with client.app.state.engine.connect() as conn:
        assert conn.scalar(select(func.count()).select_from(conversations).where(conversations.c.id==cid))==0

@pytest.mark.parametrize('zero',['0','0.0','0.00','0.000'])
def test_zero_initial_payment_is_valid_in_equivalent_decimal_formats(client,zero):
    result=client.post('/api/debts',json={'name':'Efua','phone':'0241234567',
        'amount':'25.50','initial_paid':zero,'due_date':'2026-11-07'})
    assert result.status_code==201
    assert result.json()['balance_minor']==2550

def test_receipt_download_labels_source_current_balance_and_reversal(client,debt,app):
    result=client.post('/api/debts/'+debt['id']+'/payments',json={'amount':'200',
        'source':'cash','idempotency_key':'receipt-payment-001','note':'Cash counted'})
    pid=result.json()['payment_id']
    receipt=client.get('/api/public/'+debt['public_token']+'/receipts/'+pid)
    assert receipt.status_code==200
    assert 'attachment' in receipt.headers['content-disposition']
    assert 'TRADER-REPORTED' in receipt.text and 'GHS 200.00' in receipt.text
    assert 'Current debt balance at download: GHS 300.00' in receipt.text
    with TestClient(app) as other:
        other.post('/api/auth/register',json={'name':'Other Store','login':'receiptother','password':'Other-Test-2026'})
        assert other.get('/api/payments/'+pid+'/receipt').status_code==404
    client.post('/api/payments/'+pid+'/reverse',json={'reason':'Correct mistaken cash entry'})
    assert 'REVERSED' in client.get('/api/payments/'+pid+'/receipt').text

def test_public_receipt_cannot_read_a_different_debts_payment(client,debt):
    d=client.post('/api/debts',json={'name':'Second Person','phone':'0247778888',
        'amount':'300','due_date':'2026-11-07'}).json()
    pid=client.post('/api/debts/'+debt['id']+'/payments',json={'amount':'100',
        'source':'cash','idempotency_key':'private-receipt-01','note':'Cash received'}).json()['payment_id']
    assert client.get('/api/public/'+d['public_token']+'/receipts/'+pid).status_code==404

def test_editing_cancels_original_proposal_before_manual_record(client,monkeypatch):
    from test_workflows import plan
    async def fake(*args):return plan()
    monkeypatch.setattr(agent,'gemini_plan',fake)
    p=client.post('/api/agent',json={'message':'Draft a debt for Kofi','consent':True}).json()['proposal']
    assert client.post('/api/agent/proposals/'+p['id']+'/cancel').status_code==200
    assert client.post('/api/agent/proposals/'+p['id']+'/confirm').status_code==409
    assert client.get('/api/debts').json()==[]

def test_v01_database_additive_upgrade_preserves_existing_ledger(tmp_path):
    from sqlalchemy import create_engine, inspect
    from app.db import merchants,customers,debts,entries
    url='sqlite:///'+str(tmp_path/'upgrade.db')
    old=create_engine(url)
    old_names={'merchants','sessions','customers','debts','ledger','payments','disputes',
        'outbox','audit','susu_accounts','susu_ledger','agent_proposals'}
    meta.create_all(old,tables=[t for t in meta.sorted_tables if t.name in old_names])
    with old.begin() as conn:
        conn.execute(merchants.insert().values(id='oldmerchant',name='Original Store',login='original',password_hash='unused',created_at=now()))
        conn.execute(customers.insert().values(id='oldcustomer',merchant_id='oldmerchant',name='Customer',phone='+233241234567',do_not_contact=0))
        conn.execute(debts.insert().values(id='olddebt',merchant_id='oldmerchant',customer_id='oldcustomer',
            principal_minor=50000,due_date='2026-11-07',description='Old credit sale',public_token='existing-private-token',paused=0,disputed=0,created_at=now()))
        conn.execute(entries.insert().values(id='oldentry',merchant_id='oldmerchant',debt_id='olddebt',
            kind='opening',amount_minor=50000,reference='original-opening',note='Original opening balance',created_at=now()))
    old.dispose()
    upgraded=create_app(url,{'worker_enabled':False,'paystack_key':'','sms_mode':'dry_run'})
    assert {'webhook_inbox','sms_attempts','agent_conversations','agent_turns','susu_links','susu_payments','sms_callback_inbox','sms_delivery_reports','debt_changes','debt_flags','credit_reviews'} <= set(inspect(upgraded.state.engine).get_table_names())
    with TestClient(upgraded) as c:
        assert c.get('/api/public/existing-private-token').json()['balance_minor']==50000
