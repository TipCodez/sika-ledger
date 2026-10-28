"""Durable Paystack refunds, provider disputes and settlement reconciliation."""
import json
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_, select, func

from .db import (write, payments, debts, disputes, entries, provider_accounts,
    provider_refunds, provider_disputes, provider_event_inbox,
    provider_settlements, settlement_items)
from .domain import now, uid, log, owned_debt, invalidate_previews

REFUND_EVENTS={'refund.pending','refund.processing','refund.processed','refund.failed'}
DISPUTE_EVENTS={'charge.dispute.create','charge.dispute.remind','charge.dispute.resolve'}
FINANCE_EVENTS=REFUND_EVENTS|DISPUTE_EVENTS

class RefundIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    amount:str=Field(max_length=20)
    idempotency_key:str=Field(min_length=8,max_length=100,pattern=r'^[a-zA-Z0-9_-]+$')
    reason:str=Field(min_length=5,max_length=300)
    confirmed:Literal[True]

class ReviewIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    reason:str=Field(min_length=5,max_length=300)

def owner(m):
    if m.get('role')!='owner':raise HTTPException(403,'Only an owner can manage provider finance operations.')

def _provider_id(data):
    value=data.get('id')
    return str(value) if isinstance(value,(str,int)) and str(value) else ''

def _transaction_reference(data):
    transaction=data.get('transaction')
    if isinstance(transaction,dict):
        value=transaction.get('reference')
        if isinstance(value,str):return value
    for name in ('transaction_reference','reference'):
        value=data.get(name)
        if isinstance(value,str) and value.startswith('sika-'):return value
    return ''

def queue_finance_event(conn,event):
    """Store only routing metadata; authoritative details are fetched by the worker."""
    event_type=event.get('event')
    if event_type not in FINANCE_EVENTS:return {'received':True,'ignored':True}
    data=event.get('data')
    if not isinstance(data,dict):raise HTTPException(400,'Invalid provider event data.')
    object_id=_provider_id(data)
    reference=_transaction_reference(data)
    local_refund=None
    if event_type in REFUND_EVENTS and object_id:
        local_refund=conn.execute(select(provider_refunds).where(
            provider_refunds.c.provider_refund_id==object_id)).mappings().first()
        if local_refund and not reference:
            reference=conn.scalar(select(payments.c.reference).where(payments.c.id==local_refund['payment_id'])) or ''
    if not object_id or not reference or len(reference)>200:return {'received':True,'ignored':True}
    payment=conn.execute(select(payments).where(payments.c.reference==reference,
        payments.c.source.in_(['paystack_test','paystack_live']))).mappings().first()
    if not payment:return {'received':True,'ignored':True}
    domain='live' if payment['source']=='paystack_live' else 'test'
    if data.get('domain') not in (None,domain) or data.get('currency') not in (None,'GHS'):
        raise HTTPException(409,'Provider event does not match the payment environment.')
    key=f'{event_type}:{object_id}'
    existing=conn.execute(select(provider_event_inbox).where(
        provider_event_inbox.c.event_key==key)).mappings().first()
    if existing:return {'received':True,'duplicate':True,'job_id':existing['id']}
    jid=uid()
    conn.execute(provider_event_inbox.insert().values(id=jid,merchant_id=payment['merchant_id'],
        event_key=key,event_type=event_type,provider_object_id=object_id,reference=reference,
        status='pending',attempts=0,next_attempt_at=now(),created_at=now()))
    log(conn,payment['merchant_id'],'paystack.event_received',jid,event_type)
    return {'received':True,'queued':True,'job_id':jid}

async def _request(settings,method,path,**kwargs):
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response=await client.request(method,'https://api.paystack.co'+path,
                headers={'Authorization':'Bearer '+settings['paystack_key']},**kwargs)
        response.raise_for_status(); body=response.json()
        if not body.get('status'):raise ValueError()
        return body
    except (httpx.HTTPError,ValueError,KeyError,TypeError):
        raise HTTPException(502,'Paystack finance verification is temporarily unavailable.')

def _refund_status(value):
    value=str(value or '').lower().replace('-','_')
    return value if value in {'pending','processing','processed','failed','needs_attention'} else 'unknown'

def _apply_processed_refund(conn,refund,payment,provider):
    amount=int(provider.get('amount') or refund['amount_minor'])
    if amount!=refund['amount_minor'] or provider.get('currency')!='GHS':
        raise HTTPException(409,'Provider refund amount or currency does not match the request.')
    # Refund unused overpayment credit first. Only the remainder reopens debt.
    unallocated_consumed=min(amount,payment['unallocated_minor'])
    ledger_minor=amount-unallocated_consumed
    already_ledger=conn.scalar(select(func.coalesce(func.sum(provider_refunds.c.ledger_minor),0)).where(
        provider_refunds.c.payment_id==payment['id'],provider_refunds.c.status=='processed',
        provider_refunds.c.id!=refund['id']))
    if ledger_minor>payment['applied_minor']-already_ledger:
        raise HTTPException(409,'Processed refunds exceed the locally allocated payment amount.')
    reference='paystack-refund-'+str(provider['id'])
    if ledger_minor and not conn.scalar(select(entries.c.id).where(entries.c.reference==reference)):
        owned_debt(conn,payment['merchant_id'],payment['debt_id'],lock=True)
        conn.execute(entries.insert().values(id=uid(),merchant_id=payment['merchant_id'],
            debt_id=payment['debt_id'],kind='refund_reversal',amount_minor=ledger_minor,
            reference=reference,note='Paystack-confirmed customer refund.',created_at=now()))
        from .plans import invalidate
        invalidate(conn,payment['debt_id'],'A provider refund changed the recorded balance; review the repayment plan.')
        invalidate_previews(conn,payment['debt_id'])
        conn.execute(debts.update().where(debts.c.id==payment['debt_id']).values(paused=1))
    total_refunded=conn.scalar(select(func.coalesce(func.sum(provider_refunds.c.amount_minor),0)).where(
        provider_refunds.c.payment_id==payment['id'],provider_refunds.c.status=='processed',
        provider_refunds.c.id!=refund['id']))+amount
    payment_status='refunded' if total_refunded>=payment['amount_minor'] else 'partially_refunded'
    conn.execute(payments.update().where(payments.c.id==payment['id']).values(
        status=payment_status,unallocated_minor=payment['unallocated_minor']-unallocated_consumed))
    return ledger_minor

async def process_finance_event(app,job):
    settings=app.state.settings
    with app.state.engine.connect() as conn:
        payment=conn.execute(select(payments).where(payments.c.reference==job['reference'])).mappings().first()
    if not payment:raise HTTPException(404,'Local provider payment no longer exists.')
    expected_domain='live' if payment['source']=='paystack_live' else 'test'
    if job['event_type'] in REFUND_EVENTS:
        body=await _request(settings,'GET','/refund/'+job['provider_object_id'])
        data=body['data']; status=_refund_status(data.get('status'))
        if str(data.get('id'))!=job['provider_object_id'] or data.get('domain')!=expected_domain:
            raise HTTPException(409,'Provider refund identity does not match the local payment.')
        with write(app.state.engine) as conn:
            refund=conn.execute(select(provider_refunds).where(
                provider_refunds.c.provider_refund_id==job['provider_object_id']).with_for_update()).mappings().first()
            if not refund:
                amount=data.get('amount')
                if not isinstance(amount,int) or amount<=0:raise HTTPException(409,'Provider refund amount is invalid.')
                rid=uid();stamp=now()
                conn.execute(provider_refunds.insert().values(id=rid,merchant_id=payment['merchant_id'],
                    payment_id=payment['id'],request_key='provider-'+job['provider_object_id'],
                    provider_refund_id=job['provider_object_id'],amount_minor=amount,ledger_minor=0,
                    status='pending',reason='Initiated outside Sika Ledger',created_at=stamp,updated_at=stamp))
                refund=conn.execute(select(provider_refunds).where(provider_refunds.c.id==rid)).mappings().one()
            ledger_minor=refund['ledger_minor']
            if status=='processed' and refund['status']!='processed':
                ledger_minor=_apply_processed_refund(conn,refund,payment,data)
            conn.execute(provider_refunds.update().where(provider_refunds.c.id==refund['id']).values(
                status=status,ledger_minor=ledger_minor,provider_reason=str(data.get('reason') or '')[:500],updated_at=now()))
            log(conn,payment['merchant_id'],'paystack.refund_'+status,refund['id'],f'ledger={ledger_minor}')
        return {'status':status}
    body=await _request(settings,'GET','/dispute/'+job['provider_object_id'])
    data=body['data'];transaction=data.get('transaction') or {}
    if (str(data.get('id'))!=job['provider_object_id'] or data.get('domain')!=expected_domain
        or not isinstance(transaction,dict) or transaction.get('reference')!=job['reference']):
        raise HTTPException(409,'Provider dispute identity does not match the local payment.')
    provider_status=str(data.get('status') or 'pending')[:50]
    resolved=provider_status in ('resolved','archived')
    with write(app.state.engine) as conn:
        row=conn.execute(select(provider_disputes).where(
            provider_disputes.c.provider_dispute_id==job['provider_object_id']).with_for_update()).mappings().first()
        values=dict(status=provider_status,resolution=str(data.get('resolution') or '')[:100] or None,
            refund_amount_minor=data.get('refund_amount') if isinstance(data.get('refund_amount'),int) else None,
            due_at=str(data.get('due_at') or '')[:100] or None,updated_at=now())
        if row:conn.execute(provider_disputes.update().where(provider_disputes.c.id==row['id']).values(**values));did=row['id']
        else:
            did=uid();conn.execute(provider_disputes.insert().values(id=did,merchant_id=payment['merchant_id'],
                payment_id=payment['id'],provider_dispute_id=job['provider_object_id'],created_at=now(),**values))
        open_customer=conn.scalar(select(disputes.c.id).where(disputes.c.debt_id==payment['debt_id'],disputes.c.status=='open'))
        open_provider=conn.scalar(select(provider_disputes.c.id).where(provider_disputes.c.payment_id==payment['id'],
            provider_disputes.c.status.not_in(['resolved','archived'])))
        conn.execute(debts.update().where(debts.c.id==payment['debt_id']).values(
            disputed=1 if open_customer or open_provider else 0,paused=1))
        invalidate_previews(conn,payment['debt_id'])
        log(conn,payment['merchant_id'],'paystack.dispute_resolved' if resolved else 'paystack.dispute_opened',did,provider_status)
    return {'status':provider_status}

def _future(seconds):return (datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat()

async def process_finance_events(app,limit=10):
    counts={'processed':0,'retry':0,'needs_review':0}
    for _ in range(limit):
        with write(app.state.engine) as conn:
            stamp=now()
            row=conn.execute(select(provider_event_inbox).where(or_(
                and_(provider_event_inbox.c.status.in_(['pending','retry']),provider_event_inbox.c.next_attempt_at<=stamp),
                and_(provider_event_inbox.c.status=='processing',provider_event_inbox.c.locked_until<stamp)))
                .order_by(provider_event_inbox.c.created_at).with_for_update(skip_locked=True).limit(1)).mappings().first()
            if not row:break
            job=dict(row);attempt=job['attempts']+1;lease=_future(90)
            conn.execute(provider_event_inbox.update().where(provider_event_inbox.c.id==job['id']).values(
                status='processing',attempts=attempt,locked_until=lease))
        error=None
        try:await process_finance_event(app,job);state='processed'
        except HTTPException as exc:state='retry' if exc.status_code>=500 else 'needs_review';error=str(exc.detail)[:500]
        except Exception:state='retry';error='Unexpected provider-finance verification error.'
        if state=='retry' and attempt>=8:state='needs_review';error='Automatic verification attempts exhausted. '+(error or '')
        with write(app.state.engine) as conn:
            changed=conn.execute(provider_event_inbox.update().where(provider_event_inbox.c.id==job['id'],
                provider_event_inbox.c.status=='processing',provider_event_inbox.c.locked_until==lease).values(
                status=state,last_error=error,locked_until=None,next_attempt_at=_future(min(1800,30*2**min(attempt-1,6))),
                processed_at=now() if state=='processed' else None))
            if changed.rowcount:counts[state]+=1;log(conn,job['merchant_id'],'paystack.finance_'+state,job['id'],error or '')
    return counts

def register_provider_finance(app,engine,current):
    @app.post('/api/payments/{payment_id}/refunds')
    async def create_refund(payment_id:str,data:RefundIn,m=Depends(current)):
        owner(m)
        from .domain import money
        amount=money(data.amount);stamp=now()
        with write(engine) as conn:
            prior=conn.execute(select(provider_refunds).where(provider_refunds.c.merchant_id==m['id'],
                provider_refunds.c.request_key==data.idempotency_key)).mappings().first()
            if prior:return {'refund':dict(prior),'duplicate':True}
            payment=conn.execute(select(payments).where(payments.c.id==payment_id,
                payments.c.merchant_id==m['id']).with_for_update()).mappings().first()
            if not payment:raise HTTPException(404,'Payment not found.')
            if payment['source'] not in ('paystack_test','paystack_live') or payment['status'] not in ('confirmed','partially_refunded'):
                raise HTTPException(409,'Only a confirmed Paystack payment can be refunded.')
            reserved=conn.scalar(select(func.coalesce(func.sum(provider_refunds.c.amount_minor),0)).where(
                provider_refunds.c.payment_id==payment_id,provider_refunds.c.status.not_in(['failed'])))
            if amount>payment['amount_minor']-reserved:raise HTTPException(409,'Refund exceeds the remaining refundable amount.')
            rid=uid();conn.execute(provider_refunds.insert().values(id=rid,merchant_id=m['id'],payment_id=payment_id,
                request_key=data.idempotency_key,amount_minor=amount,ledger_minor=0,status='initiating',
                reason=data.reason,created_at=stamp,updated_at=stamp))
        try:
            body=await _request(app.state.settings,'POST','/refund',json={'transaction':payment['reference'],'amount':amount,
                'currency':'GHS','customer_note':data.reason,'merchant_note':'Sika Ledger refund '+data.idempotency_key})
            result=body['data'];provider_id=str(result.get('id') or '')
            expected_domain='live' if payment['source']=='paystack_live' else 'test'
            if (not provider_id or result.get('domain')!=expected_domain or result.get('currency')!='GHS'
                or result.get('amount')!=amount):raise HTTPException(409,'Paystack returned refund details that do not match the request.')
            status=_refund_status(result.get('status'))
        except HTTPException as exc:
            with write(engine) as conn:
                conn.execute(provider_refunds.update().where(provider_refunds.c.id==rid).values(
                    status='unknown',provider_reason=str(exc.detail)[:500],updated_at=now()))
                log(conn,m['id'],'paystack.refund_unknown',rid,'Provider outcome requires review.')
            raise
        with write(engine) as conn:
            conn.execute(provider_refunds.update().where(provider_refunds.c.id==rid).values(
                provider_refund_id=provider_id,status=status,provider_reason=str(result.get('reason') or '')[:500],updated_at=now()))
            log(conn,m['id'],'paystack.refund_requested',rid,f'{amount};{status}')
            row=dict(conn.execute(select(provider_refunds).where(provider_refunds.c.id==rid)).mappings().one())
        return {'refund':row,'note':'The debt ledger changes only after Paystack confirms the refund was processed.'}

    @app.get('/api/integrations/paystack/refunds')
    def list_refunds(m=Depends(current)):
        owner(m)
        with engine.connect() as conn:return [dict(x) for x in conn.execute(select(provider_refunds).where(
            provider_refunds.c.merchant_id==m['id']).order_by(provider_refunds.c.created_at.desc())).mappings()]

    @app.get('/api/integrations/paystack/disputes')
    def list_provider_disputes(m=Depends(current)):
        owner(m)
        with engine.connect() as conn:return [dict(x) for x in conn.execute(select(provider_disputes).where(
            provider_disputes.c.merchant_id==m['id']).order_by(provider_disputes.c.created_at.desc())).mappings()]

    @app.get('/api/integrations/paystack/events')
    def list_finance_events(m=Depends(current)):
        owner(m)
        with engine.connect() as conn:return [dict(x) for x in conn.execute(select(provider_event_inbox).where(
            provider_event_inbox.c.merchant_id==m['id']).order_by(provider_event_inbox.c.created_at.desc()).limit(100)).mappings()]

    @app.post('/api/integrations/paystack/events/{event_id}/retry')
    def retry_finance_event(event_id:str,data:ReviewIn,m=Depends(current)):
        owner(m)
        with write(engine) as conn:
            row=conn.execute(select(provider_event_inbox).where(provider_event_inbox.c.id==event_id,
                provider_event_inbox.c.merchant_id==m['id']).with_for_update()).mappings().first()
            if not row:raise HTTPException(404,'Provider finance event not found.')
            if row['status'] not in ('retry','needs_review'):raise HTTPException(409,'This event is not available for retry.')
            conn.execute(provider_event_inbox.update().where(provider_event_inbox.c.id==event_id).values(
                status='pending',attempts=0,next_attempt_at=now(),locked_until=None,last_error=None))
            log(conn,m['id'],'paystack.finance_retry_requested',event_id,data.reason)
        return {'queued':True}

    @app.post('/api/integrations/paystack/settlements/sync')
    async def sync_settlements(m=Depends(current)):
        owner(m)
        with engine.connect() as conn:account=conn.execute(select(provider_accounts).where(
            provider_accounts.c.merchant_id==m['id'],provider_accounts.c.status=='active')).mappings().first()
        if not account:raise HTTPException(409,'An active Paystack subaccount is required.')
        body=await _request(app.state.settings,'GET','/settlement',params={'subaccount':account['subaccount_code'],'perPage':50,'page':1})
        rows=body.get('data')
        if not isinstance(rows,list):raise HTTPException(502,'Paystack returned an invalid settlement list.')
        synced=0
        with write(engine) as conn:
            for item in rows:
                if not isinstance(item,dict) or item.get('domain')!=account['domain'] or item.get('currency')!='GHS':continue
                provider_id=str(item.get('id') or '')
                if not provider_id:continue
                values=dict(status=str(item.get('status') or 'unknown')[:50],currency='GHS',
                    total_amount_minor=int(item.get('total_amount') or 0),effective_amount_minor=int(item.get('effective_amount') or 0),
                    fees_minor=int(item.get('total_fees') or 0),settlement_date=str(item.get('settlement_date') or '')[:100] or None,
                    updated_at=now())
                existing=conn.execute(select(provider_settlements).where(provider_settlements.c.merchant_id==m['id'],
                    provider_settlements.c.provider_settlement_id==provider_id)).mappings().first()
                if existing:conn.execute(provider_settlements.update().where(provider_settlements.c.id==existing['id']).values(**values))
                else:conn.execute(provider_settlements.insert().values(id=uid(),merchant_id=m['id'],provider_settlement_id=provider_id,
                    domain=account['domain'],reconciliation_status='pending',created_at=now(),**values))
                synced+=1
            log(conn,m['id'],'paystack.settlements_synced',account['subaccount_code'],str(synced))
        return {'synced':synced}

    @app.post('/api/integrations/paystack/settlements/{settlement_id}/reconcile')
    async def reconcile_settlement(settlement_id:str,m=Depends(current)):
        owner(m)
        with engine.connect() as conn:row=conn.execute(select(provider_settlements).where(
            provider_settlements.c.id==settlement_id,provider_settlements.c.merchant_id==m['id'])).mappings().first()
        if not row:raise HTTPException(404,'Settlement not found.')
        items=[];page=1
        while True:
            body=await _request(app.state.settings,'GET','/settlement/'+row['provider_settlement_id']+'/transactions',
                params={'perPage':100,'page':page})
            batch=body.get('data')
            if not isinstance(batch,list):raise HTTPException(502,'Paystack returned invalid settlement transactions.')
            items.extend(batch)
            meta=body.get('meta') if isinstance(body.get('meta'),dict) else {}
            page_count=meta.get('pageCount',meta.get('page_count'))
            if (isinstance(page_count,int) and page>=page_count) or (not isinstance(page_count,int) and len(batch)<100):break
            page+=1
            if page>100:raise HTTPException(409,'Settlement is too large for automatic reconciliation; review it manually.')
        counts={'matched':0,'amount_mismatch':0,'unknown':0}
        with write(engine) as conn:
            conn.execute(settlement_items.delete().where(settlement_items.c.settlement_id==settlement_id))
            for item in items:
                if not isinstance(item,dict):continue
                reference=str(item.get('reference') or '')[:200];amount=int(item.get('amount') or 0)
                payment=conn.execute(select(payments).where(payments.c.merchant_id==m['id'],
                    payments.c.reference==reference,payments.c.source.in_(['paystack_test','paystack_live']))).mappings().first()
                match='unknown'
                environment_ok=item.get('domain') in (None,row['domain']) and item.get('currency') in (None,'GHS')
                if payment and environment_ok:match='matched' if payment['amount_minor']==amount and payment['status'] in ('confirmed','partially_refunded','refunded') else 'amount_mismatch'
                counts[match]+=1
                conn.execute(settlement_items.insert().values(id=uid(),merchant_id=m['id'],settlement_id=settlement_id,
                    provider_transaction_id=str(item.get('id') or reference or uid()),reference=reference,amount_minor=amount,
                    local_payment_id=payment['id'] if payment else None,match_status=match,created_at=now()))
            state='reconciled' if items and not counts['amount_mismatch'] and not counts['unknown'] else 'needs_review'
            conn.execute(provider_settlements.update().where(provider_settlements.c.id==settlement_id).values(
                reconciliation_status=state,updated_at=now()))
            log(conn,m['id'],'paystack.settlement_'+state,settlement_id,json.dumps(counts,sort_keys=True))
        return {'status':state,'counts':counts}

    @app.get('/api/integrations/paystack/settlements')
    def list_settlements(m=Depends(current)):
        owner(m)
        with engine.connect() as conn:return [dict(x) for x in conn.execute(select(provider_settlements).where(
            provider_settlements.c.merchant_id==m['id']).order_by(provider_settlements.c.settlement_date.desc())).mappings()]
