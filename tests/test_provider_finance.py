import asyncio
import hashlib
import hmac
import json

import httpx
from sqlalchemy import select

from app import provider_finance
from app.db import (write, payments, entries, provider_refunds, provider_disputes,
    provider_event_inbox, provider_accounts, provider_settlements, settlement_items)
from app.domain import now, uid, confirm_payment, balance


class FakeResponse:
    def __init__(self,data,status_code=200):self.data=data;self.status_code=status_code
    def raise_for_status(self):
        if self.status_code>=400:raise httpx.HTTPStatusError('provider error',request=None,response=None)
    def json(self):return self.data


def provider_payment(client,debt,amount=10000):
    merchant=client.get('/api/me').json()['id'];reference='sika-'+debt['id']+'-finance-test'
    with write(client.app.state.engine) as conn:
        pid=uid()
        conn.execute(payments.insert().values(id=pid,merchant_id=merchant,debt_id=debt['id'],
            reference=reference,amount_minor=amount,applied_minor=0,unallocated_minor=0,
            source='paystack_test',status='pending',created_at=now()))
        confirm_payment(conn,reference,'Provider test payment')
    client.app.state.settings.update(paystack_key='sk_test_finance',paystack_email='test@example.com')
    return pid,reference,merchant


def signed_event(client,event):
    raw=json.dumps(event,separators=(',',':')).encode()
    signature=hmac.new(client.app.state.settings['paystack_key'].encode(),raw,hashlib.sha512).hexdigest()
    return client.post('/api/webhooks/paystack',content=raw,headers={'x-paystack-signature':signature})


def test_refund_changes_ledger_only_after_processed_webhook(client,debt,monkeypatch):
    pid,reference,_=provider_payment(client,debt)
    state={'status':'pending'}
    class FakeClient:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def request(self,method,url,headers=None,**kwargs):
            data={'id':901,'transaction':{'reference':reference},'domain':'test','currency':'GHS',
                'amount':4000,'status':state['status'],'reason':state['status']}
            return FakeResponse({'status':True,'data':data})
    monkeypatch.setattr(provider_finance.httpx,'AsyncClient',FakeClient)
    created=client.post('/api/payments/'+pid+'/refunds',json={'amount':'40.00',
        'idempotency_key':'refund-case-001','reason':'Customer returned part of the order','confirmed':True})
    assert created.status_code==200,created.text
    with client.app.state.engine.connect() as conn:
        assert balance(conn,debt['id'])==40000
        assert not conn.scalar(select(entries.c.id).where(entries.c.kind=='refund_reversal'))
    state['status']='processed'
    queued=signed_event(client,{'event':'refund.processed','data':{'id':901,'domain':'test',
        'currency':'GHS','transaction':{'reference':reference}}})
    assert queued.status_code==200 and queued.json()['queued']
    result=asyncio.run(provider_finance.process_finance_events(client.app))
    assert result['processed']==1
    with client.app.state.engine.connect() as conn:
        assert balance(conn,debt['id'])==44000
        refund=conn.execute(select(provider_refunds)).mappings().one()
        assert refund['status']=='processed' and refund['ledger_minor']==4000
        assert conn.scalar(select(payments.c.status).where(payments.c.id==pid))=='partially_refunded'
    duplicate=signed_event(client,{'event':'refund.processed','data':{'id':901,'domain':'test',
        'currency':'GHS','transaction':{'reference':reference}}})
    assert duplicate.json()['duplicate']
    assert asyncio.run(provider_finance.process_finance_events(client.app))['processed']==0
    with client.app.state.engine.connect() as conn:assert balance(conn,debt['id'])==44000


def test_ambiguous_refund_outcome_is_review_only(client,debt,monkeypatch):
    pid,_,_=provider_payment(client,debt)
    class TimeoutClient:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def request(self,*args,**kwargs):raise httpx.TimeoutException('timeout')
    monkeypatch.setattr(provider_finance.httpx,'AsyncClient',TimeoutClient)
    response=client.post('/api/payments/'+pid+'/refunds',json={'amount':'20.00',
        'idempotency_key':'refund-timeout-01','reason':'Approved customer refund','confirmed':True})
    assert response.status_code==502
    with client.app.state.engine.connect() as conn:
        refund=conn.execute(select(provider_refunds)).mappings().one()
        assert refund['status']=='unknown'
        assert balance(conn,debt['id'])==40000
        assert not conn.scalar(select(entries.c.id).where(entries.c.kind=='refund_reversal'))


def test_refund_consumes_unallocated_credit_before_reopening_debt(client,debt):
    pid,_,merchant=provider_payment(client,debt,60000)
    with write(client.app.state.engine) as conn:
        rid=uid()
        conn.execute(provider_refunds.insert().values(id=rid,merchant_id=merchant,payment_id=pid,
            request_key='refund-overpayment-01',provider_refund_id='9901',amount_minor=4000,
            ledger_minor=0,status='pending',reason='Return unused customer credit',created_at=now(),updated_at=now()))
        payment=conn.execute(select(payments).where(payments.c.id==pid)).mappings().one()
        refund=conn.execute(select(provider_refunds).where(provider_refunds.c.id==rid)).mappings().one()
        ledger=provider_finance._apply_processed_refund(conn,refund,payment,
            {'id':9901,'amount':4000,'currency':'GHS'})
        conn.execute(provider_refunds.update().where(provider_refunds.c.id==rid).values(status='processed',ledger_minor=ledger))
    with client.app.state.engine.connect() as conn:
        assert balance(conn,debt['id'])==0
        payment=conn.execute(select(payments).where(payments.c.id==pid)).mappings().one()
        assert payment['unallocated_minor']==6000 and payment['status']=='partially_refunded'
        assert not conn.scalar(select(entries.c.id).where(entries.c.kind=='refund_reversal'))


def test_provider_dispute_pauses_collection_without_reversing_money(client,debt,monkeypatch):
    _,reference,_=provider_payment(client,debt)
    state={'status':'pending','resolution':None}
    class FakeClient:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def request(self,*args,**kwargs):
            return FakeResponse({'status':True,'data':{'id':77,'domain':'test','status':state['status'],
                'resolution':state['resolution'],'refund_amount':4000,'transaction':{'reference':reference}}})
    monkeypatch.setattr(provider_finance.httpx,'AsyncClient',FakeClient)
    event={'event':'charge.dispute.create','data':{'id':77,'domain':'test','currency':'GHS',
        'transaction':{'reference':reference}}}
    assert signed_event(client,event).json()['queued']
    assert asyncio.run(provider_finance.process_finance_events(client.app))['processed']==1
    detail=client.get('/api/debts/'+debt['id']).json()
    assert detail['disputed'] and detail['paused'] and detail['balance_minor']==40000
    state.update(status='resolved',resolution='declined')
    event['event']='charge.dispute.resolve'
    assert signed_event(client,event).json()['queued']
    assert asyncio.run(provider_finance.process_finance_events(client.app))['processed']==1
    detail=client.get('/api/debts/'+debt['id']).json()
    assert not detail['disputed'] and detail['paused'] and detail['balance_minor']==40000
    with client.app.state.engine.connect() as conn:
        row=conn.execute(select(provider_disputes)).mappings().one()
        assert row['status']=='resolved' and row['resolution']=='declined'


def test_settlement_reconciliation_matches_local_provider_reference(client,debt,monkeypatch):
    pid,reference,merchant=provider_payment(client,debt)
    with write(client.app.state.engine) as conn:
        conn.execute(provider_accounts.insert().values(merchant_id=merchant,provider='paystack',
            subaccount_code='ACCT_finance',provider_account_id='22',domain='test',status='active',
            business_name='Test Store',settlement_bank='MTN',account_name='TEST',account_last4='4567',
            created_at=now(),updated_at=now()))
    class FakeClient:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def request(self,method,url,headers=None,**kwargs):
            if url.endswith('/settlement'):
                return FakeResponse({'status':True,'data':[{'id':501,'domain':'test','status':'success',
                    'currency':'GHS','total_amount':9850,'effective_amount':9850,'total_fees':150,
                    'settlement_date':'2026-10-28T00:00:00.000Z'}]})
            return FakeResponse({'status':True,'data':[{'id':601,'reference':reference,
                'amount':10000,'currency':'GHS','domain':'test','status':'success'}]})
    monkeypatch.setattr(provider_finance.httpx,'AsyncClient',FakeClient)
    synced=client.post('/api/integrations/paystack/settlements/sync')
    assert synced.status_code==200 and synced.json()['synced']==1
    with client.app.state.engine.connect() as conn:
        settlement=conn.execute(select(provider_settlements)).mappings().one()
    reconciled=client.post('/api/integrations/paystack/settlements/'+settlement['id']+'/reconcile')
    assert reconciled.status_code==200,reconciled.text
    assert reconciled.json()=={'status':'reconciled','counts':{'matched':1,'amount_mismatch':0,'unknown':0}}
    with client.app.state.engine.connect() as conn:
        item=conn.execute(select(settlement_items)).mappings().one()
        assert item['local_payment_id']==pid and item['match_status']=='matched'
