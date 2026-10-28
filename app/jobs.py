"""Durable background work. Provider retries are idempotent or review-gated."""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, and_, or_
from fastapi import HTTPException
import httpx
from .db import (write, webhook_inbox, outbox, sms_attempts, merchants,
                 customers, debts, conversations, turns, link_expired)
from .domain import now, today, uid, log, owned_debt, debt_view

logger = logging.getLogger(__name__)

def future(seconds):
    return (datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat()

def within_hours(settings):
    hour = datetime.now(timezone.utc).hour  # Africa/Accra is UTC+0.
    return settings['reminder_start'] <= hour < settings['reminder_end']

def reminder_fact(debt):
    plan=debt.get('plan')
    if plan and plan['status']=='active':
        amount=sum(i['remaining_minor'] for i in plan['instalments'] if i['due_date']<=today().isoformat()) or plan['next_due_minor']
        subject=f"GHS {amount/100:,.2f} on your agreed repayment plan is due on {debt['collection_due_date']} (total outstanding GHS {debt['balance_minor']/100:,.2f})"
    else:subject=f"GHS {debt['balance_minor']/100:,.2f} is due on {debt['due_date']}"
    return subject

def reminder_body(settings, merchant_name, debt):
    return (f"Hello {debt['name']}, a friendly reminder from {merchant_name}: {reminder_fact(debt)}. "
            f"Pay all or part: {settings['base_url']}/pay/{debt['public_token']}. "
            f"Incorrect record? Contact {merchant_name}.")

def eligible(debt):
    return (debt['balance_minor'] > 0 and not link_expired(debt.get('link_expires_at'))
        and not debt.get('plan_review_required',False) and not any(debt[k] for k in
            ('paused','disputed','do_not_contact')))

async def process_webhooks(app, limit=10):
    engine=app.state.engine
    counts={'processed':0,'retry':0,'needs_review':0}
    for _ in range(limit):
        with write(engine) as conn:
            stamp=now()
            row=conn.execute(select(webhook_inbox).where(or_(
                and_(webhook_inbox.c.status.in_(['pending','retry']),
                     webhook_inbox.c.next_attempt_at <= stamp),
                and_(webhook_inbox.c.status=='processing',
                     webhook_inbox.c.locked_until < stamp)))
                .order_by(webhook_inbox.c.created_at)
                .with_for_update(skip_locked=True).limit(1)).mappings().first()
            if not row:break
            job=dict(row)
            attempt=job['attempts']+1
            lease=future(90)
            conn.execute(webhook_inbox.update().where(webhook_inbox.c.id==job['id']).values(
                status='processing',attempts=attempt,locked_until=lease))
        error=None
        try:
            result=await app.state.verify_paystack(job['reference'])
            state='retry' if result.get('balance_unchanged') else 'processed'
            if state=='retry':error='Provider transaction is not yet successful.'
        except HTTPException as exc:
            state='retry' if exc.status_code >= 500 else 'needs_review'
            error=str(exc.detail)[:300]
        except Exception:
            state='retry'
            error='Unexpected verification error; retry scheduled.'
            logger.warning('Webhook verification failed for job %s',job['id'])
        if state=='retry' and attempt>=8:
            state='needs_review'
            error='Automatic verification attempts exhausted. '+(error or '')
        with write(engine) as conn:
            # A reclaimed lease cannot overwrite a newer worker's result.
            changed=conn.execute(webhook_inbox.update().where(
                webhook_inbox.c.id==job['id'],webhook_inbox.c.status=='processing',
                webhook_inbox.c.locked_until==lease).values(status=state,last_error=error,
                    locked_until=None,next_attempt_at=future(min(1800,30*2**min(attempt-1,6))),
                    processed_at=now() if state=='processed' else None))
            if changed.rowcount:
                counts[state]+=1
                log(conn,job['merchant_id'],'webhook.'+state,job['id'],error or '')
    return counts

async def submit_sandbox(settings, number, body):
    if not settings['arkesel_key']:
        return {'status':'failed','error':'Arkesel API key is not configured.'}
    try:
        live=settings.get('sms_mode')=='arkesel_live'
        payload={'sender':settings['sms_sender'],'message':body,
            'recipients':[number],'sandbox':not live}
        if settings.get('arkesel_callbacks') and settings['base_url'].startswith('https://'):
            payload['callback_url']=settings['base_url']+'/api/webhooks/arkesel'
        async with httpx.AsyncClient(timeout=12) as client:
            response=await client.post('https://sms.arkesel.com/api/v2/sms/send',
                headers={'api-key':settings['arkesel_key']},json=payload)
        if response.status_code >= 500:
            return {'status':'unknown','error':'Provider server error. Check provider history before retrying.'}
        if response.status_code >= 400:
            return {'status':'failed','error':f'Provider rejected submission (HTTP {response.status_code}).'}
        data=response.json()
        if data.get('status')!='success':
            return {'status':'failed','error':'Provider did not accept the sandbox submission.'}
        rows=data.get('data')
        rows=rows if isinstance(rows,list) else [rows]
        identifier=next((str(r['id']) for r in rows if isinstance(r,dict) and r.get('id')),None)
        if not identifier:
            return {'status':'unknown','error':'No provider message ID returned; inspect provider history.'}
        return {'status':'live_accepted' if live else 'sandbox_accepted','provider_id':identifier}
    except (httpx.HTTPError,ValueError,TypeError,AttributeError):
        return {'status':'unknown','error':'Submission outcome is unknown. No automatic resend will occur.'}

async def process_sms(app,limit=10):
    settings=app.state.settings
    if not within_hours(settings):return {'processed':0,'skipped':'outside_hours'}
    counts={'processed':0,'live_accepted':0}
    engine=app.state.engine
    for _ in range(limit):
        with write(engine) as conn:
            # A crash after claiming might have followed provider acceptance.
            abandoned=list(conn.execute(select(sms_attempts).where(
                sms_attempts.c.status=='sending',sms_attempts.c.started_at < future(-120))
                .with_for_update()).mappings())
            for attempt in abandoned:
                conn.execute(sms_attempts.update().where(sms_attempts.c.id==attempt['id']).values(
                    status='unknown',error='Worker interrupted. Review provider history; do not automatically resend.',finished_at=now()))
                conn.execute(outbox.update().where(outbox.c.id==attempt['outbox_id'],
                    outbox.c.status=='sending').values(status='unknown'))
            row=conn.execute(select(outbox).where(outbox.c.status=='queued')
                .order_by(outbox.c.created_at).with_for_update(skip_locked=True).limit(1)).mappings().first()
            if not row:break
            job=dict(row)
            aid=uid()
            conn.execute(outbox.update().where(outbox.c.id==job['id']).values(status='sending'))
            conn.execute(sms_attempts.insert().values(id=aid,merchant_id=job['merchant_id'],
                outbox_id=job['id'],mode=settings['sms_mode'],status='sending',started_at=now()))
        # The database lock serializes last-second payments/disputes with this
        # bounded submission. This trades throughput for consistency in a pilot.
        with write(engine) as conn:
            d=owned_debt(conn,job['merchant_id'],job['debt_id'],lock=True)
            conn.execute(select(customers).where(customers.c.id==d['customer_id']).with_for_update()).first()
            d=debt_view(conn,d)
            if job['day_key']!=today().isoformat() or not eligible(d) or (settings['sms_mode']!='dry_run' and not d['sms_consent']):
                result={'status':'cancelled','error':'Record became ineligible before dispatch.'}
            else:
                business=conn.scalar(select(merchants.c.name).where(merchants.c.id==job['merchant_id']))
                body=reminder_body(settings,business,d)
                conn.execute(outbox.update().where(outbox.c.id==job['id']).values(body=body))
                if settings['sms_mode']=='dry_run':
                    result={'status':'dry_run_complete'}
                else:
                    result=await submit_sandbox(settings,d['phone'],body)
            conn.execute(sms_attempts.update().where(sms_attempts.c.id==aid).values(
                status=result['status'],provider_id=result.get('provider_id'),
                error=result.get('error'),finished_at=now()))
            conn.execute(outbox.update().where(outbox.c.id==job['id']).values(status=result['status']))
            log(conn,job['merchant_id'],'sms.'+result['status'],job['id'],result.get('error',''))
            counts['processed']+=1
            if result['status']=='live_accepted':counts['live_accepted']+=1
    return counts

async def run_once(app):
    verification=await process_webhooks(app)
    from .provider_finance import process_finance_events
    finance=await process_finance_events(app)
    from .sms_callbacks import process_callbacks
    delivery_reports=await process_callbacks(app)
    created=0
    if within_hours(app.state.settings):
        with write(app.state.engine) as conn:
            for mid in conn.scalars(select(merchants.c.id)):
                created+=app.state.make_previews(conn,mid)
            if app.state.settings['automatic_reminders']:
                conn.execute(outbox.update().where(outbox.c.status=='preview',
                    outbox.c.day_key==today().isoformat()).values(status='queued'))
    # Isolate the bounded network submission/transaction from FastAPI's event
    # loop; synchronous DB lock waits elsewhere must not stall the HTTP client.
    sms=await asyncio.to_thread(lambda: asyncio.run(process_sms(app)))
    with write(app.state.engine) as conn:
        expired=select(conversations.c.id).where(conversations.c.expires_at < now(),
            or_(conversations.c.busy_until.is_(None),conversations.c.busy_until < now()))
        ids=list(conn.scalars(expired))
        if ids:
            conn.execute(turns.delete().where(turns.c.conversation_id.in_(ids)))
            conn.execute(conversations.delete().where(conversations.c.id.in_(ids)))
    return {'webhooks':verification,'provider_finance':finance,'sms_callbacks':delivery_reports,'previews_created':created,
        'sms':sms,'real_messages_sent':sms.get('live_accepted',0)}

async def worker_loop(app):
    while True:
        try:await run_once(app)
        except Exception:logger.exception('Background worker pass failed')
        await asyncio.sleep(10)
