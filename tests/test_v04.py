import asyncio
from contextlib import closing
from datetime import timedelta
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import zipfile
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import select,func
from app.db import write,entries,payments,staff,sessions,audit_actors
from app.domain import today,now,uid
from app.jobs import process_sms
from app.backup import create_backup,restore_backup,inspect_backup,database_path
from app.maintenance_lock import DatabaseLease

def agreement(client,d,**extra):
    body={'expected_balance_minor':d['balance_minor'],'request_key':'agreed-plan-test-001',
        'agreement_confirmed':True,'reason':'Customer agreed dates and amounts in person',
        'instalments':[{'due_date':today().isoformat(),'amount':'100'},
            {'due_date':(today()+timedelta(days=7)).isoformat(),'amount':'400'}]}
    body.update(extra)
    return client.post('/api/debts/'+d['id']+'/plans',json=body)

def pay(client,d,amount='50',key='v04-manual-payment-001'):
    r=client.post('/api/debts/'+d['id']+'/payments',json={'amount':amount,'source':'cash',
        'idempotency_key':key,'note':'Cash received and counted'})
    assert r.status_code==200,r.text
    return r.json()

def consent(client,d,**fields):
    data={'expected_phone':d['phone'],'phone':d['phone'],'sms_consent':True,
        'evidence':'Customer verified number and agreed SMS in person','reason':'Record explicit permission'}
    data.update(fields)
    return client.post('/api/customers/'+d['customer_id']+'/contact',json=data)

@pytest.fixture
def collector(client,app):
    r=client.post('/api/team',json={'name':'Collector Ama','login':'ama-collector','password':'Collector-Test-2026'})
    assert r.status_code==201,r.text
    with TestClient(app) as worker:
        assert worker.post('/api/auth/login',json={'login':'ama-collector','password':'Collector-Test-2026'}).json()['role']=='collector'
        yield worker,r.json()['id']

def test_profile_groups_debts_savings_payments_disputes_and_timeline(client,debt):
    client.post('/api/debts',json={'name':debt['name'],'phone':debt['phone'],'amount':'25','due_date':'2026-11-07'})
    a=client.post('/api/susu',json={'name':debt['name'],'phone':debt['phone'],'label':'Daily','daily_target':'10'}).json()
    client.post('/api/susu/'+a['id']+'/entries',json={'kind':'contribution','amount':'30','reference':'profile-savings-001','note':'Cash contribution'})
    pay(client,debt)
    client.post('/api/public/'+debt['public_token']+'/dispute',json={'reason':'incorrect_balance','message':'Please check this balance'})
    r=client.get('/api/customers/'+debt['customer_id']).json()
    assert r['outstanding_minor']==47500 and r['savings_minor']==3000
    assert len(r['debts'])==2 and len(r['payments'])==1 and len(r['disputes'])==1
    assert len(r['timeline'])==4
    assert not r['customer']['sms_consent']

def test_savings_only_customer_appears_in_directory(client):
    a=client.post('/api/susu',json={'name':'Savings Only','phone':'0245556666','label':'Daily','daily_target':'10'})
    assert a.status_code==201
    c=client.get('/api/customers').json()[0]
    assert c['name']=='Savings Only' and c['debt_count']==0

def test_customer_profile_and_history_cannot_cross_tenants(client,debt,app):
    with TestClient(app) as other:
        other.post('/api/auth/register',json={'name':'Other Business','login':'profile-other','password':'Other-Test-2026'})
        assert other.get('/api/customers').json()==[]
        assert other.get('/api/customers/'+debt['customer_id']).status_code==404
        assert other.get('/api/customers/'+debt['customer_id']+'/contact-history').status_code==404

def test_schedule_does_not_write_money_and_duplicate_agreement_is_idempotent(client,debt):
    assert agreement(client,debt).status_code==200
    assert agreement(client,debt).json()['duplicate']
    d=client.get('/api/debts/'+debt['id']).json()
    assert d['balance_minor']==50000 and len(d['ledger'])==1
    assert d['plan']['next_due_minor']==10000
    assert len(client.get('/api/debts/'+debt['id']+'/plans').json()['history'])==1

@pytest.mark.parametrize('change',[
    {'expected_balance_minor':49999},
    {'agreement_confirmed':False},
    {'reason':'     '},
    {'instalments':[{'due_date':'2026-01-01','amount':'500'}]},
    {'instalments':[{'due_date':today().isoformat(),'amount':'499'}]},
    {'instalments':[{'due_date':today().isoformat(),'amount':'250'},{'due_date':today().isoformat(),'amount':'250'}]},
])
def test_invalid_or_stale_plan_never_changes_ledger(client,debt,change):
    assert agreement(client,debt,**change).status_code in (409,422)
    assert client.get('/api/debts/'+debt['id']).json()['plan'] is None

def test_repayments_apply_oldest_instalments_and_duplicate_payment_once(client,debt):
    agreement(client,debt);pay(client,debt,'150');pay(client,debt,'150')
    d=client.get('/api/debts/'+debt['id']).json();p=d['plan']
    assert d['balance_minor']==35000
    assert [i['remaining_minor'] for i in p['instalments']]==[0,35000]
    assert [i['status'] for i in p['instalments']]==['paid','partial']
    assert p['next_due_date']==(today()+timedelta(days=7)).isoformat()

def test_schedule_reminder_uses_unpaid_instalment_and_current_total(client,debt,monkeypatch):
    base=today();agreement(client,debt);pay(client,debt)
    later=base+timedelta(days=3)
    import app.main as main
    import app.domain as domain
    import app.plans as plans
    import app.jobs as jobs
    for module in (main,domain,plans,jobs):monkeypatch.setattr(module,'today',lambda:later)
    client.post('/api/reminders/preview')
    row=client.get('/api/reminders').json()[0]
    assert 'GHS 50.00 on your agreed repayment plan' in row['body']
    assert 'total outstanding GHS 450.00' in row['body']
    public=client.get('/api/public/'+debt['public_token']).json()
    assert public['plan']['overdue_minor']==5000
    assert client.get('/api/dashboard').json()['overdue_minor']==5000
    from app.agent import Plan,execute_plan
    fields={k:'' for k in ('reply','customer_name','phone','amount','initial_paid','due_date','description','debt_id')}
    with client.app.state.engine.connect() as conn:
        draft=execute_plan(conn,client.get('/api/me').json()['id'],Plan(action='draft_reminder',**{**fields,'debt_id':debt['id']}),[])
        assert 'GHS 50.00 on your agreed repayment plan' in draft['reply']
        summary=execute_plan(conn,client.get('/api/me').json()['id'],Plan(action='portfolio_summary',**fields),[public])
        assert 'Overdue: GHS 50.00' in summary['reply']
    assert 'reason' not in public['plan']

def test_correction_and_reversal_require_plan_review_and_suppress_reminders(client,debt):
    agreement(client,debt);payment=pay(client,debt)
    client.post('/api/payments/'+payment['payment_id']+'/reverse',json={'reason':'Wrong payment recorded'})
    d=client.get('/api/debts/'+debt['id']).json()
    assert d['plan_review_required'] and d['balance_minor']==50000
    assert client.post('/api/reminders/preview').json()['created']==0
    agreement(client,debt,request_key='reviewed-new-plan-001')
    r=client.post('/api/debts/'+debt['id']+'/changes',json={'kind':'adjustment','amount':'25','direction':'increase',
        'expected_balance_minor':50000,'request_key':'plan-adjustment-001','reason':'Correct sale amount after review'})
    assert r.status_code==200
    assert client.get('/api/debts/'+debt['id']).json()['plan']['status']=='needs_review'
    text=pdf_text(client.get('/api/reports/customers/'+debt['customer_id']+'/statement.pdf'))
    assert 'See current ledger' in ' '.join(text.split())

def test_plan_replacement_cancel_completion_and_history(client,debt):
    first=agreement(client,debt).json()['id']
    second=agreement(client,debt,request_key='replace-plan-key-002').json()['id']
    assert first!=second
    assert client.post(f"/api/debts/{debt['id']}/plans/{second}/cancel",json={'reason':'Customer requests another agreement'}).json()['balance_unchanged']
    agreement(client,debt,request_key='final-plan-key-003');pay(client,debt,'500')
    assert client.get('/api/debts/'+debt['id']).json()['plan']['status']=='completed'
    assert len(client.get('/api/debts/'+debt['id']+'/plans').json()['history'])==3

def test_contact_change_rotates_all_links_pauses_and_retains_dispute(client,debt):
    a=client.post('/api/susu',json={'name':debt['name'],'phone':debt['phone'],'label':'Savings','daily_target':'10'}).json()
    client.post('/api/public/'+debt['public_token']+'/dispute',json={'reason':'wrong_number','message':'This number is incorrect'})
    assert consent(client,debt,phone='0249991111').json()['links_rotated']
    d=client.get('/api/debts/'+debt['id']).json()
    assert d['paused'] and d['disputed'] and d['sms_consent']
    assert d['phone']=='+233249991111' and d['balance_minor']==50000
    assert client.get('/api/public/'+debt['public_token']).status_code==404
    assert client.get('/api/susu-public/'+a['public_token']).status_code==404
    h=client.get('/api/customers/'+debt['customer_id']+'/contact-history').json()
    assert json.loads(h[0]['payload'])['before']['phone']==debt['phone']

def test_consent_requires_evidence_current_phone_and_no_contact_collision(client,debt):
    assert consent(client,debt,evidence='').status_code==422
    assert consent(client,debt,expected_phone='0248880000').status_code==409
    client.post('/api/debts',json={'name':'Other Customer','phone':'0248880000','amount':'25','due_date':'2026-11-07'})
    assert consent(client,debt,phone='0248880000').status_code==409
    assert client.get('/api/public/'+debt['public_token']).status_code==200

def test_sandbox_queue_requires_consent_and_revocation_invalidates_queued_work(client,debt,monkeypatch):
    client.post('/api/debts/'+debt['id']+'/changes',json={'kind':'due_date','due_date':today().isoformat(),
        'expected_balance_minor':50000,'request_key':'consent-due-key-001','reason':'Prepare reminder test'})
    client.app.state.settings.update(sms_mode='arkesel_sandbox',arkesel_key='fake-test-key',reminder_start=0,reminder_end=24)
    client.post('/api/reminders/preview');rid=client.get('/api/reminders').json()[0]['id']
    assert client.post('/api/reminders/'+rid+'/queue').status_code==409
    assert consent(client,debt).status_code==200
    client.post('/api/reminders/preview');assert client.post('/api/reminders/'+rid+'/queue').status_code==200
    assert consent(client,debt,sms_consent=False).status_code==200
    import app.jobs as jobs
    async def forbidden(*args):raise AssertionError('Consent revoked; no provider call allowed')
    monkeypatch.setattr(jobs,'submit_sandbox',forbidden)
    assert asyncio.run(process_sms(client.app))['processed']==0

def test_collector_records_payments_with_actor_attribution(client,debt,collector):
    worker,aid=collector
    assert worker.get('/api/customers/'+debt['customer_id']).status_code==200
    pay(worker,debt,'25')
    events=client.get('/api/audit').json()
    event=next(e for e in events if e['action']=='payment.confirmed')
    assert event['actor_name']=='Collector Ama' and event['actor_role']=='collector'
    assert all('password_hash' not in x for x in client.get('/api/team').json())

@pytest.mark.parametrize('method,path',[
    ('GET','/api/team'),('GET','/api/branding'),('GET','/api/audit'),('GET','/api/backup-info'),
    ('GET','/api/export/debts.csv'),('GET','/api/payment-recovery'),('GET','/api/integrations/webhooks'),
    ('POST','/api/team'),('PUT','/api/branding'),('POST','/api/demo/seed'),('POST','/api/agent')])
def test_collector_cannot_bypass_owner_permissions_through_api(collector,method,path):
    worker,_=collector
    assert worker.request(method,path,json={} if method!='GET' else None).status_code==403

def test_collector_cannot_change_contacts_plan_corrections_or_payout(client,debt,collector):
    worker,_=collector
    assert consent(worker,debt).status_code==403
    assert agreement(worker,debt).status_code==403
    assert worker.post('/api/debts/'+debt['id']+'/pause',json={'paused':False}).status_code==403
    a=client.post('/api/susu',json={'name':debt['name'],'phone':debt['phone'],'label':'Daily','daily_target':'10'}).json()
    body={'amount':'20','reference':'staff-contribution-001','kind':'contribution','note':'Cash collected'}
    assert worker.post('/api/susu/'+a['id']+'/entries',json=body).status_code==200
    assert worker.post('/api/susu/'+a['id']+'/entries',json={**body,'reference':'staff-payout-001','kind':'payout'}).status_code==403

def test_disable_reset_and_reactivate_revoke_sessions(client,collector):
    worker,aid=collector
    assert client.post('/api/team/'+aid+'/password',json={'password':'Reset-Collector-2026','reason':'Staff requested password reset'}).json()['sessions_revoked']
    assert worker.get('/api/me').status_code==401
    assert worker.post('/api/auth/login',json={'login':'ama-collector','password':'Collector-Test-2026'}).status_code==401
    assert worker.post('/api/auth/login',json={'login':'ama-collector','password':'Reset-Collector-2026'}).status_code==200
    assert client.post('/api/team/'+aid+'/active',json={'active':False,'reason':'Collector no longer works here'}).status_code==200
    assert worker.get('/api/me').status_code==401
    assert worker.post('/api/auth/login',json={'login':'ama-collector','password':'Reset-Collector-2026'}).status_code==401
    assert client.post('/api/team/'+aid+'/active',json={'active':True,'reason':'Collector returns to work'}).status_code==200
    assert worker.post('/api/auth/login',json={'login':'ama-collector','password':'Reset-Collector-2026'}).status_code==200
    assert worker.post('/api/auth/logout').status_code==200
    assert worker.get('/api/me').status_code==401

def test_owner_cannot_be_disabled_and_login_namespace_is_shared(client,collector):
    owner=client.get('/api/me').json()
    assert client.post('/api/team/'+owner['actor_id']+'/active',json={'active':False,'reason':'Attempt owner lockout'}).status_code==409
    assert client.post('/api/auth/register',json={'name':'Collision','login':'ama-collector','password':'Collision-Test-2026'}).status_code==409
    assert client.get('/api/me').json()['id']==owner['id']

def pdf_text(response):
    assert response.status_code==200,response.text[:300] if not response.content.startswith(b'%PDF') else ''
    assert response.headers['content-type']=='application/pdf'
    return '\n'.join(p.extract_text() for p in PdfReader(BytesIO(response.content)).pages)

def test_branded_pdf_receipt_statement_and_public_scope(client,debt,app):
    client.put('/api/branding',json={'display_name':'Raphael & Sons <Store>','address':'Sunyani, Ghana',
        'contact':'Public business contact','footer':'Thank you, Abena.','accent':'#194b39'})
    p=pay(client,debt,'200')
    url='/api/payments/'+p['payment_id']+'/receipt.pdf'
    text=pdf_text(client.get(url))
    assert 'Raphael & Sons <Store>' in text and 'TRADER-REPORTED' in text
    assert 'GHS 300.00' in text and debt['phone'] not in text and debt['public_token'] not in text
    public='/api/public/'+debt['public_token']+'/receipts/'+p['payment_id']+'/pdf'
    assert 'Repayment acknowledgement' in pdf_text(client.get(public))
    statement='/api/reports/customers/'+debt['customer_id']+'/statement.pdf'
    assert 'Total trade debt outstanding' in pdf_text(client.get(statement))
    with TestClient(app) as other:
        other.post('/api/auth/register',json={'name':'Other Store','login':'pdf-other','password':'Other-Test-2026'})
        assert other.get(url).status_code==404 and other.get(statement).status_code==404
    client.post('/api/debts/'+debt['id']+'/rotate-link')
    assert client.get(public).status_code==404

def test_savings_pdf_labels_simulation_and_cannot_use_another_account_link(client):
    def account(name,number):return client.post('/api/susu',json={'name':name,'phone':number,'label':'Daily','daily_target':'10'}).json()
    a=account('Ama Savings','0247771111');b=account('Other Savings','0247772222')
    base='/api/susu-public/'+a['public_token']
    ref=client.post(base+'/checkout',json={'amount':'20','mode':'simulation','idempotency_key':'pdf-savings-key-001'}).json()['reference']
    pid=client.post(base+'/simulate/'+ref).json()['payment_id']
    assert 'SIMULATION' in pdf_text(client.get(base+'/receipts/'+pid+'.pdf'))
    assert client.get('/api/susu-public/'+b['public_token']+'/receipts/'+pid+'.pdf').status_code==404

def test_reversed_payment_pdf_never_looks_like_valid_repayment(client,debt):
    p=pay(client,debt)
    client.post('/api/payments/'+p['payment_id']+'/reverse',json={'reason':'Wrong entry reversed'})
    text=pdf_text(client.get('/api/payments/'+p['payment_id']+'/receipt.pdf'))
    assert 'no longer reduces the debt' in text and 'GHS 500.00' in text

def test_sqlite_backup_online_and_restore_requires_all_connections_closed(client,debt,tmp_path):
    database=database_path(client.app.state.settings['database_url']);archive=tmp_path/'demo.sikabackup'
    create_backup(database,archive)
    assert inspect_backup(archive)['contains_all_businesses']
    pay(client,debt,'100')
    with pytest.raises(ValueError,match='Stop every'):restore_backup(database,archive,database.name)
    client.app.state.engine.dispose()
    result=restore_backup(database,archive,database.name)
    assert Path(result['safety_backup']).is_file()
    assert client.get('/api/me').status_code==401
    assert client.post('/api/auth/login',json={'login':'raphael','password':'Prototype-Test-2026'}).status_code==200
    assert client.get('/api/debts/'+debt['id']).json()['balance_minor']==50000
    assert inspect_backup(result['safety_backup'])['application']=='Sika Ledger'

def test_backup_corruption_and_wrong_confirmation_leave_database_unchanged(client,debt,tmp_path):
    database=database_path(client.app.state.settings['database_url']);archive=tmp_path/'source.sikabackup'
    create_backup(database,archive)
    corrupt=tmp_path/'corrupt.sikabackup'
    with zipfile.ZipFile(archive) as z,zipfile.ZipFile(corrupt,'w') as out:
        out.writestr('manifest.json',z.read('manifest.json'));out.writestr('database.sqlite',z.read('database.sqlite')+b'corruption')
    with pytest.raises(ValueError,match='checksum'):inspect_backup(corrupt)
    with pytest.raises(ValueError,match='Confirm'):restore_backup(database,archive,'another.db')
    assert client.get('/api/debts/'+debt['id']).json()['balance_minor']==50000
    with pytest.raises(ValueError,match='not overwritten'):create_backup(database,archive)

def test_exclusive_maintenance_lock_blocks_new_app_connections(client,tmp_path):
    database=database_path(client.app.state.settings['database_url'])
    client.app.state.engine.dispose()
    with DatabaseLease(database,exclusive=True):
        with pytest.raises(OSError):client.app.state.engine.connect()
    assert client.get('/api/me').status_code==200

def test_postgres_is_explicitly_rejected_by_sqlite_backup_tool():
    with pytest.raises(ValueError,match='PostgreSQL'):database_path('postgresql+psycopg://user:pass@localhost/sika')

def test_restore_stops_sends_and_verification_jobs_instead_of_replaying_them(client,debt,tmp_path):
    from app.db import outbox,sms_attempts,webhook_inbox
    mid=client.get('/api/me').json()['id'];rid=uid();aid=uid();jid=uid()
    with write(client.app.state.engine) as conn:
        conn.execute(outbox.insert().values(id=rid,merchant_id=mid,debt_id=debt['id'],day_key=today().isoformat(),body='Test message',status='sending',created_at=now()))
        conn.execute(sms_attempts.insert().values(id=aid,merchant_id=mid,outbox_id=rid,mode='arkesel_sandbox',status='sending',started_at=now()))
        conn.execute(webhook_inbox.insert().values(id=jid,merchant_id=mid,event_key='restore-job',reference='restore-reference',status='pending',attempts=0,next_attempt_at=now(),created_at=now()))
    database=database_path(client.app.state.settings['database_url']);archive=tmp_path/'in-flight.sikabackup'
    create_backup(database,archive);client.app.state.engine.dispose()
    restore_backup(database,archive,database.name)
    with client.app.state.engine.connect() as conn:
        assert conn.scalar(select(outbox.c.status).where(outbox.c.id==rid))=='unknown'
        assert conn.scalar(select(sms_attempts.c.status).where(sms_attempts.c.id==aid))=='unknown'
        assert conn.scalar(select(webhook_inbox.c.status).where(webhook_inbox.c.id==jid))=='needs_review'

def test_document_branding_rejects_unreadable_accent(client):
    assert client.put('/api/branding',json={'display_name':'Test Store','accent':'#ffffff'}).status_code==422

def test_owner_cannot_manage_another_business_collector(client,collector,app):
    _,aid=collector
    with TestClient(app) as other:
        other.post('/api/auth/register',json={'name':'Other Business','login':'other-team-owner','password':'Other-Test-2026'})
        assert all(a['id']!=aid for a in other.get('/api/team').json())
        assert other.post('/api/team/'+aid+'/active',json={'active':False,'reason':'Attempt cross-business change'}).status_code==404
        assert other.post('/api/team/'+aid+'/password',json={'password':'Attempt-Change-2026','reason':'Attempt cross-business reset'}).status_code==404

def test_v03_owner_credentials_and_legacy_session_survive_additive_upgrade(tmp_path):
    from sqlalchemy import create_engine
    from app.db import meta,merchants
    from app.domain import password_hash
    from app.main import create_app
    import hashlib
    database='sqlite:///'+str(tmp_path/'legacy.db');old=create_engine(database)
    additions={'staff_accounts','session_actors','audit_actors','contact_preferences','contact_history',
        'repayment_plans','plan_instalments','business_branding'}
    meta.create_all(old,tables=[t for t in meta.sorted_tables if t.name not in additions])
    token='legacy-session-for-migration-test'
    with old.begin() as conn:
        conn.execute(merchants.insert().values(id='legacy-owner',name='Legacy Business',login='legacy-login',
            password_hash=password_hash('Legacy-Password-2026'),created_at=now()))
        conn.execute(sessions.insert().values(token_hash=hashlib.sha256(token.encode()).hexdigest(),merchant_id='legacy-owner',
            expires_at='2099-01-01T00:00:00+00:00'))
    old.dispose()
    app=create_app(database,{'worker_enabled':False,'paystack_key':'','base_url':'http://testserver'})
    with TestClient(app) as c:
        c.cookies.set('sika_session',token)
        assert c.get('/api/me').json()['role']=='owner'
        assert c.post('/api/auth/logout').status_code==200
        assert c.post('/api/auth/login',json={'login':'legacy-login','password':'Legacy-Password-2026'}).json()['role']=='owner'
        assert len(c.get('/api/team').json())==1
