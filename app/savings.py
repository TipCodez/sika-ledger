"""Account-free contribution checkout, isolated from receivables accounting."""
import secrets
from fastapi import HTTPException
from sqlalchemy import select, func
from .db import (susu, susu_links, susu_payments, contributions, customers, merchants, branding,
    new_link_expiry, link_expired)
from .domain import uid, now, log

def savings_balance(conn,sid):
    return conn.scalar(select(func.coalesce(func.sum(contributions.c.amount_minor),0))
        .where(contributions.c.account_id==sid))

def owned_account(conn,merchant,sid,lock=False):
    q=select(susu).where(susu.c.id==sid,susu.c.merchant_id==merchant)
    if lock:q=q.with_for_update()
    row=conn.execute(q).mappings().first()
    if not row:raise HTTPException(404,'Savings account not found.')
    return dict(row)

def ensure_link(conn,sid):
    token=conn.scalar(select(susu_links.c.public_token).where(susu_links.c.account_id==sid))
    if not token:
        conn.execute(select(susu.c.id).where(susu.c.id==sid).with_for_update()).scalar_one()
        token=conn.scalar(select(susu_links.c.public_token).where(susu_links.c.account_id==sid))
        if token:return token
        token=secrets.token_urlsafe(32)
        conn.execute(susu_links.insert().values(account_id=sid,public_token=token,
            created_at=now(),expires_at=new_link_expiry()))
    return token

def public_account(conn,token,lock=False):
    # Lock only the parent row, consistently with manual payout/contribution flows.
    link=conn.execute(select(susu_links).where(susu_links.c.public_token==token)).mappings().first()
    if not link:raise HTTPException(404,'Contribution link not found.')
    if link_expired(link['expires_at']):
        raise HTTPException(410,'This private contribution link has expired. Ask the business for a new link.')
    sid=link['account_id']
    q=select(susu).where(susu.c.id==sid)
    if lock:q=q.with_for_update()
    row=conn.execute(q).mappings().first()
    if not row:raise HTTPException(404,'Contribution link not found.')
    result=dict(row);result['link_expires_at']=link['expires_at']
    return result

def public_view(conn,a):
    customer=conn.scalar(select(customers.c.name).where(customers.c.id==a['customer_id']))
    business=conn.scalar(select(merchants.c.name).where(merchants.c.id==a['merchant_id']))
    public_brand=conn.execute(select(branding.c.display_name,branding.c.contact)
        .where(branding.c.merchant_id==a['merchant_id'])).mappings().first()
    return {'name':customer,
        'business_name':public_brand['display_name'] if public_brand else business,
        'business_contact':(public_brand['contact'].strip()
            if public_brand and public_brand['contact'].strip() else None),'label':a['label'],
        'daily_target_minor':a['daily_target_minor'],'balance_minor':savings_balance(conn,a['id']),
        'link_expires_at':a.get('link_expires_at'),
        'receipts':[{'id':p['id'],'amount_minor':p['amount_minor'],'source':p['source'],
            'status':p['status'],'date':p['confirmed_at']} for p in conn.execute(
                select(susu_payments).where(susu_payments.c.account_id==a['id'],
                    susu_payments.c.status=='confirmed').order_by(susu_payments.c.created_at.desc())).mappings()]}

def confirm_contribution(conn,reference):
    p=conn.execute(select(susu_payments).where(susu_payments.c.reference==reference)).mappings().first()
    if not p:raise HTTPException(404,'Contribution payment not found.')
    account=owned_account(conn,p['merchant_id'],p['account_id'],True)
    p=conn.execute(select(susu_payments).where(susu_payments.c.reference==reference)
        .with_for_update()).mappings().one()
    if p['status']=='confirmed':
        return {'duplicate':True,'payment_id':p['id'],'savings_balance_minor':savings_balance(conn,account['id'])}
    if p['status']!='pending':raise HTTPException(409,'Contribution cannot be confirmed.')
    note=('SIMULATED contribution — no funds transferred.' if p['source']=='simulation'
          else 'Paystack server-verified LIVE contribution.' if p['source']=='paystack_live'
          else 'Paystack server-verified TEST contribution — no real funds transferred.')
    conn.execute(contributions.insert().values(id=uid(),merchant_id=p['merchant_id'],
        account_id=account['id'],amount_minor=p['amount_minor'],kind='contribution',
        reference='susu-payment-'+reference,note=note,created_at=now()))
    conn.execute(susu_payments.update().where(susu_payments.c.id==p['id']).values(
        status='confirmed',confirmed_at=now()))
    log(conn,p['merchant_id'],'susu.payment_confirmed',p['id'],str(p['amount_minor']))
    return {'duplicate':False,'payment_id':p['id'],'savings_balance_minor':savings_balance(conn,account['id'])}
