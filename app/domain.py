import hashlib
import hmac
import re
import secrets
from datetime import datetime, timezone, date, timedelta
from decimal import Decimal, InvalidOperation
from fastapi import HTTPException
from sqlalchemy import select, func
from .db import (debts, entries, payments, customers, audit, outbox, debt_flags,
    audit_actors, contact_preferences, new_link_expiry)

def uid():
    return secrets.token_hex(16)

def now():
    return datetime.now(timezone.utc).isoformat()

def today():
    return datetime.now(timezone.utc).date()

def money(value, allow_zero=False):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or (amount == 0 and not allow_zero) or amount > Decimal('1000000'):
            raise ValueError()
        scaled = amount * 100
        if scaled != scaled.to_integral_value():
            raise ValueError()
        return int(scaled)
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(422, 'Use a positive GHS amount with at most two decimal places (maximum 1,000,000).')

def phone(value):
    value = re.sub(r'[\s()-]', '', value)
    if re.fullmatch(r'0\d{9}', value):
        value = '+233' + value[1:]
    elif re.fullmatch(r'233\d{9}', value):
        value = '+' + value
    if not re.fullmatch(r'\+233\d{9}', value):
        raise HTTPException(422, 'Enter a Ghana phone number, e.g. 0241234567.')
    return value

def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return salt + ':' + digest

def password_ok(password, hashed):
    try:
        return hmac.compare_digest(password_hash(password, hashed.split(':')[0]), hashed)
    except (ValueError, IndexError):
        return False

def log(conn, merchant, action, entity, detail=''):
    aid=uid()
    conn.execute(audit.insert().values(id=aid, merchant_id=merchant,
        action=action, entity_id=entity, detail=detail, created_at=now()))
    from .access import actor_context
    actor=actor_context.get()
    if actor and actor['id']==merchant:
        conn.execute(audit_actors.insert().values(audit_id=aid,actor_id=actor['actor_id'],name=actor['actor_name'],role=actor['role']))

def balance(conn, debt_id):
    return conn.scalar(select(func.coalesce(func.sum(entries.c.amount_minor), 0))
                       .where(entries.c.debt_id == debt_id))

def owned_debt(conn, merchant, debt_id, lock=False):
    q = select(debts).where(debts.c.id == debt_id, debts.c.merchant_id == merchant)
    if lock:
        q = q.with_for_update()
    row = conn.execute(q).mappings().first()
    if not row:
        raise HTTPException(404, 'Debt not found.')
    return dict(row)

def customer_get_or_create(conn, merchant, name, number):
    number = phone(number)
    row = conn.execute(select(customers).where(customers.c.merchant_id == merchant,
                                              customers.c.phone == number)).mappings().first()
    if row:
        if row['name'].casefold() != name.strip().casefold():
            raise HTTPException(409, 'This phone number belongs to another saved customer name. Check the record.')
        return row['id']
    cid = uid()
    conn.execute(customers.insert().values(id=cid, merchant_id=merchant,
        name=name.strip(), phone=number, do_not_contact=0))
    return cid

def create_debt(conn, merchant, data):
    amount = money(data['amount'])
    initial = money(data.get('initial_paid','0'),allow_zero=True)
    if initial > amount:
        raise HTTPException(422, 'Initial payment exceeds the sale amount.')
    try:
        due = date.fromisoformat(data['due_date']).isoformat()
    except (ValueError, TypeError):
        raise HTTPException(422, 'Use an ISO due date: YYYY-MM-DD.')
    cid = customer_get_or_create(conn, merchant, data['name'], data['phone'])
    did = uid()
    conn.execute(debts.insert().values(id=did, merchant_id=merchant, customer_id=cid,
        principal_minor=amount, due_date=due, description=data.get('description','Credit sale'),
        public_token=secrets.token_urlsafe(32),public_token_expires_at=new_link_expiry(),
        paused=0, disputed=0, created_at=now()))
    conn.execute(entries.insert().values(id=uid(), merchant_id=merchant, debt_id=did,
        kind='opening', amount_minor=amount, reference='opening-'+did,
        note=data.get('description','Credit sale'), created_at=now()))
    log(conn, merchant, 'debt.created', did)
    if initial:
        ref = 'initial-'+did
        conn.execute(payments.insert().values(id=uid(), merchant_id=merchant, debt_id=did,
            reference=ref, amount_minor=initial, applied_minor=0, unallocated_minor=0,
            source='manual_initial', status='pending', created_at=now()))
        confirm_payment(conn, ref, 'Initial payment reported by trader; not provider verified.')
    return did

def invalidate_previews(conn, debt_id):
    conn.execute(outbox.update().where(outbox.c.debt_id==debt_id,
        outbox.c.status.in_(['preview','queued'])).values(status='stale'))

def confirm_payment(conn, reference, note='Provider verified'):
    # Lock debt first consistently, then reread payment after acquiring that lock.
    p = conn.execute(select(payments).where(payments.c.reference==reference)).mappings().first()
    if not p:
        raise HTTPException(404, 'Payment reference not found.')
    d = owned_debt(conn, p['merchant_id'], p['debt_id'], lock=True)
    p = dict(conn.execute(select(payments).where(payments.c.reference==reference)
                         .with_for_update()).mappings().one())
    if p['status'] in ('confirmed','reversed','partially_refunded','refunded'):
        return {'duplicate':True, 'payment_id':p['id'], 'balance_minor':balance(conn,d['id'])}
    if p['status'] != 'pending':
        raise HTTPException(409, 'This payment cannot be confirmed.')
    remaining = balance(conn, d['id'])
    applied = min(remaining, p['amount_minor'])
    excess = p['amount_minor'] - applied
    if applied:
        conn.execute(entries.insert().values(id=uid(), merchant_id=d['merchant_id'],
            debt_id=d['id'], kind='repayment', amount_minor=-applied,
            reference='payment-'+reference, note=note, created_at=now()))
    conn.execute(payments.update().where(payments.c.id==p['id']).values(
        status='confirmed', applied_minor=applied, unallocated_minor=excess, confirmed_at=now()))
    invalidate_previews(conn,d['id'])
    log(conn,d['merchant_id'],'payment.confirmed',p['id'],f'applied={applied};unallocated={excess}')
    return {'duplicate':False,'payment_id':p['id'],'balance_minor':remaining-applied,
            'unallocated_minor':excess}

def reverse_payment(conn, merchant, payment_id, reason):
    p = conn.execute(select(payments).where(payments.c.id==payment_id,
                                           payments.c.merchant_id==merchant)).mappings().first()
    if not p:
        raise HTTPException(404,'Payment not found.')
    owned_debt(conn,merchant,p['debt_id'],lock=True)
    p = conn.execute(select(payments).where(payments.c.id==payment_id).with_for_update()).mappings().one()
    if p['status']=='reversed':
        return
    if p['status']!='confirmed':
        raise HTTPException(409,'Only confirmed payments can be reversed.')
    if p['source'] in ('paystack_test','paystack_live'):
        raise HTTPException(409,'Provider refunds need provider-side verification; manual reversal is disabled for Paystack transactions.')
    if p['applied_minor']:
        conn.execute(entries.insert().values(id=uid(), merchant_id=merchant,
            debt_id=p['debt_id'], kind='reversal', amount_minor=p['applied_minor'],
            reference='reversal-'+p['reference'], note=reason, created_at=now()))
    conn.execute(payments.update().where(payments.c.id==payment_id).values(status='reversed',unallocated_minor=0))
    from .plans import invalidate
    invalidate(conn,p['debt_id'],'Repayment reversed; review the agreed schedule.')
    invalidate_previews(conn,p['debt_id'])
    log(conn,merchant,'payment.reversed',payment_id,reason)

def debt_view(conn, d, public=False):
    d = dict(d)
    c = conn.execute(select(customers).where(customers.c.id==d['customer_id'])).mappings().one()
    b = balance(conn,d['id'])
    cancelled=bool(conn.scalar(select(debt_flags.c.debt_id).where(debt_flags.c.debt_id==d['id'])))
    charged=conn.scalar(select(func.coalesce(func.sum(entries.c.amount_minor),0)).where(
        entries.c.debt_id==d['id'],entries.c.kind.in_(['opening','adjustment'])))
    from .plans import view as plan_view
    plan=plan_view(conn,d['id'],b,public)
    collection_due=plan['next_due_date'] if plan and plan['status']=='active' else d['due_date']
    consent=conn.execute(select(contact_preferences).where(contact_preferences.c.customer_id==c['id'])).mappings().first()
    status = 'cancelled' if cancelled else ('paid' if b==0 else ('disputed' if d['disputed'] else ('overdue' if collection_due<today().isoformat() else 'open')))
    result = {'id':d['id'],'name':c['name'],'principal_minor':d['principal_minor'],
        'balance_minor':b,'paid_minor':0 if cancelled else charged-b,'due_date':d['due_date'],
        'overdue_minor':(plan['overdue_minor'] if plan and plan['status']=='active' else b) if status=='overdue' else 0,
        'adjusted_total_minor':charged,'adjustments_minor':charged-d['principal_minor'],'cancelled':cancelled,
        'description':d['description'],'status':status,'paused':bool(d['paused']),
        'disputed':bool(d['disputed']),'do_not_contact':bool(c['do_not_contact']),
        'plan':plan,'collection_due_date':collection_due,'plan_review_required':bool(plan and plan['status']=='needs_review'),
        'sms_consent':bool(consent and consent['sms_consent'] and consent['verified_phone']==c['phone'] and not c['do_not_contact'])}
    result['link_expires_at']=d['public_token_expires_at']
    if not public:
        result.update(phone=c['phone'],public_token=d['public_token'],customer_id=d['customer_id'])
    return result
