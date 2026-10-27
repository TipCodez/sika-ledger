"""Customer profiles, explicit contact permission, owner tools and agreed plans."""
import json
import secrets
from typing import Literal
from fastapi import Depends,HTTPException
from pydantic import BaseModel,ConfigDict,Field,field_validator
from sqlalchemy import select
from .db import (write,customers,debts,entries,payments,disputes,susu,contributions,
    staff,sessions,session_actors,contact_preferences,contact_history,susu_links,branding,plans,
    new_link_expiry)
from .domain import uid,now,phone,password_hash,log,owned_debt,debt_view,invalidate_previews
from . import savings
from . import plans as schedules

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')
    @field_validator('*',mode='before')
    @classmethod
    def trim(cls,value,info):
        return value.strip() if isinstance(value,str) and info.field_name!='password' else value
class Explained(Strict):
    reason:str=Field(min_length=5,max_length=500)
class Contact(Explained):
    expected_phone:str=Field(max_length=30)
    phone:str=Field(max_length=30)
    sms_consent:bool
    evidence:str=Field(default='',max_length=500)
class Instalment(Strict):
    due_date:str=Field(min_length=10,max_length=10)
    amount:str=Field(max_length=20)
class Plan(Explained):
    agreement_confirmed:Literal[True]
    expected_balance_minor:int=Field(gt=0,strict=True)
    request_key:str=Field(min_length=8,max_length=100,pattern=r'^[a-zA-Z0-9_-]+$')
    instalments:list[Instalment]=Field(min_length=1,max_length=60)
class StaffIn(Strict):
    name:str=Field(min_length=2,max_length=100)
    login:str=Field(min_length=3,max_length=100,pattern=r'^[A-Za-z0-9_.-]+$')
    password:str=Field(min_length=10,max_length=128)
class StaffActive(Explained):
    active:bool
class StaffPassword(Explained):
    password:str=Field(min_length=10,max_length=128)
class Brand(Strict):
    display_name:str=Field(min_length=2,max_length=100)
    address:str=Field(default='',max_length=200)
    contact:str=Field(default='',max_length=100)
    footer:str=Field(default='Thank you for keeping clear records.',max_length=200)
    accent:str=Field(default='#087f5b',pattern=r'^#[0-9a-fA-F]{6}$')
    @field_validator('accent')
    @classmethod
    def readable_colour(cls,value):
        channels=[int(value[i:i+2],16)/255 for i in (1,3,5)]
        linear=[c/12.92 if c<=0.04045 else ((c+0.055)/1.055)**2.4 for c in channels]
        luminance=sum(c*w for c,w in zip(linear,(0.2126,0.7152,0.0722)))
        if 1.05/(luminance+0.05)<3:raise ValueError('Choose a darker accent colour so document headings remain readable on white.')
        return value.lower()

def revoke_sessions(conn,aid):
    tokens=list(conn.scalars(select(session_actors.c.token_hash).where(session_actors.c.actor_id==aid)))
    if tokens:
        conn.execute(session_actors.delete().where(session_actors.c.token_hash.in_(tokens)))
        conn.execute(sessions.delete().where(sessions.c.token_hash.in_(tokens)))

def own_customer(conn,mid,cid,lock=False):
    q=select(customers).where(customers.c.id==cid,customers.c.merchant_id==mid)
    if lock:q=q.with_for_update()
    c=conn.execute(q).mappings().first()
    if not c:raise HTTPException(404,'Customer not found.')
    return dict(c)

def preference(conn,c):
    p=conn.execute(select(contact_preferences).where(contact_preferences.c.customer_id==c['id'])).mappings().first()
    return {'sms_consent':bool(p and p['sms_consent'] and p['verified_phone']==c['phone'] and not c['do_not_contact']),
        'evidence':p['evidence'] if p else '', 'updated_at':p['updated_at'] if p else None}

def register_workspace(app,engine,current,all_debts):
    @app.get('/api/customers')
    def customer_list(m=Depends(current)):
        with engine.connect() as conn:
            result=[]
            for c in conn.execute(select(customers).where(customers.c.merchant_id==m['id']).order_by(customers.c.name)).mappings():
                ds=[debt_view(conn,d) for d in conn.execute(select(debts).where(debts.c.customer_id==c['id'])).mappings()]
                accounts=list(conn.execute(select(susu).where(susu.c.customer_id==c['id'])).mappings())
                result.append({**dict(c),'sms_consent':preference(conn,c)['sms_consent'],
                    'debt_count':len(ds),'outstanding_minor':sum(d['balance_minor'] for d in ds),
                    'savings_minor':sum(savings.savings_balance(conn,a['id']) for a in accounts)})
            return result

    @app.get('/api/customers/{cid}')
    def customer_profile(cid:str,m=Depends(current)):
        with engine.connect() as conn:
            c=own_customer(conn,m['id'],cid)
            ds=[debt_view(conn,d) for d in conn.execute(select(debts).where(debts.c.customer_id==cid)).mappings()]
            ids=[d['id'] for d in ds];accounts=[];timeline=[]
            ps=[dict(p) for p in conn.execute(select(payments).where(payments.c.debt_id.in_(ids))).mappings()]
            for a in conn.execute(select(susu).where(susu.c.customer_id==cid)).mappings():
                ledger=[dict(e) for e in conn.execute(select(contributions).where(contributions.c.account_id==a['id'])).mappings()]
                accounts.append({**dict(a),'balance_minor':sum(e['amount_minor'] for e in ledger),'entries':ledger})
                timeline.extend({'id':e['id'],'created_at':e['created_at'],'kind':'savings_'+e['kind'],
                    'amount_minor':e['amount_minor'],'note':e['note'],'account_id':a['id']} for e in ledger)
            timeline.extend({'id':e['id'],'created_at':e['created_at'],'kind':'debt_'+e['kind'],
                'amount_minor':e['amount_minor'],'note':e['note'],'debt_id':e['debt_id']} for e in conn.execute(
                    select(entries).where(entries.c.debt_id.in_(ids))).mappings())
            dsputes=[dict(d) for d in conn.execute(select(disputes).where(disputes.c.debt_id.in_(ids))).mappings()]
            return {'customer':{**c,**preference(conn,c)},'debts':ds,'payments':ps,'savings':accounts,
                'disputes':dsputes,'timeline':sorted(timeline,key=lambda x:(x['created_at'],x['id']),reverse=True),
                'outstanding_minor':sum(d['balance_minor'] for d in ds),'savings_minor':sum(a['balance_minor'] for a in accounts)}

    @app.post('/api/customers/{cid}/contact')
    def change_contact(cid:str,data:Contact,m=Depends(current)):
        number=phone(data.phone)
        if data.sms_consent and len(data.evidence)<5:raise HTTPException(422,'Record how the customer verified this number and granted SMS permission.')
        with write(engine) as conn:
            own_customer(conn,m['id'],cid)
            ds=list(conn.execute(select(debts).where(debts.c.customer_id==cid).order_by(debts.c.id).with_for_update()).mappings())
            c=own_customer(conn,m['id'],cid,True)
            if c['phone']!=phone(data.expected_phone):raise HTTPException(409,'Contact changed. Reload the customer profile.')
            clash=conn.scalar(select(customers.c.id).where(customers.c.merchant_id==m['id'],customers.c.phone==number,customers.c.id!=cid))
            if clash:raise HTTPException(409,'This number already belongs to another customer. Records were not merged.')
            before=preference(conn,c);changed=number!=c['phone']
            conn.execute(customers.update().where(customers.c.id==cid).values(phone=number,do_not_contact=int(not data.sms_consent)))
            values=dict(sms_consent=int(data.sms_consent),verified_phone=number,evidence=data.evidence,updated_at=now())
            exists=conn.scalar(select(contact_preferences.c.customer_id).where(contact_preferences.c.customer_id==cid))
            if exists:conn.execute(contact_preferences.update().where(contact_preferences.c.customer_id==cid).values(**values))
            else:conn.execute(contact_preferences.insert().values(customer_id=cid,**values))
            for d in ds:
                fields={'paused':1} if changed or not data.sms_consent else {}
                if changed:
                    fields['public_token']=secrets.token_urlsafe(32)
                    fields['public_token_expires_at']=new_link_expiry()
                if fields:conn.execute(debts.update().where(debts.c.id==d['id']).values(**fields))
                invalidate_previews(conn,d['id'])
            if changed:
                for sid in conn.scalars(select(susu.c.id).where(susu.c.customer_id==cid)):
                    savings.owned_account(conn,m['id'],sid,True)
                    savings.ensure_link(conn,sid)
                    conn.execute(susu_links.update().where(susu_links.c.account_id==sid).values(
                        public_token=secrets.token_urlsafe(32),expires_at=new_link_expiry()))
            conn.execute(contact_history.insert().values(id=uid(),merchant_id=m['id'],customer_id=cid,
                payload=json.dumps({'before':{'phone':c['phone'],**before},'after':{'phone':number,**values}}),reason=data.reason,created_at=now()))
            log(conn,m['id'],'customer.contact_reviewed',cid,data.reason)
        return {'updated':True,'links_rotated':changed,'note':'Existing disputes and paused debts require separate review before reminders resume.'}

    @app.get('/api/customers/{cid}/contact-history')
    def contacts(cid:str,m=Depends(current)):
        with engine.connect() as conn:
            own_customer(conn,m['id'],cid)
            return [dict(r) for r in conn.execute(select(contact_history).where(contact_history.c.customer_id==cid).order_by(contact_history.c.created_at.desc())).mappings()]

    @app.get('/api/debts/{did}/plans')
    def debt_plans(did:str,m=Depends(current)):
        with engine.connect() as conn:
            d=owned_debt(conn,m['id'],did)
            return {'current':schedules.view(conn,did,debt_view(conn,d)['balance_minor']),
                'history':[dict(r) for r in conn.execute(select(plans).where(plans.c.debt_id==did).order_by(plans.c.created_at.desc())).mappings()]}

    @app.post('/api/debts/{did}/plans')
    def agree(did:str,data:Plan,m=Depends(current)):
        with write(engine) as conn:
            d=owned_debt(conn,m['id'],did,True)
            if d['disputed']:raise HTTPException(409,'Resolve this dispute before agreeing a repayment plan.')
            return schedules.save(conn,m['id'],d,data)

    @app.post('/api/debts/{did}/plans/{pid}/cancel')
    def cancel_plan(did:str,pid:str,data:Explained,m=Depends(current)):
        with write(engine) as conn:
            owned_debt(conn,m['id'],did,True)
            row=conn.execute(select(plans).where(plans.c.id==pid,plans.c.debt_id==did,plans.c.merchant_id==m['id'])).mappings().first()
            if not row:raise HTTPException(404,'Plan not found.')
            if row['status'] not in ('active','needs_review'):raise HTTPException(409,'This plan is already inactive.')
            conn.execute(plans.update().where(plans.c.id==pid).values(status='cancelled'))
            invalidate_previews(conn,did);log(conn,m['id'],'plan.cancelled',pid,data.reason)
        return {'cancelled':True,'balance_unchanged':True}

    @app.get('/api/team')
    def team(m=Depends(current)):
        with engine.connect() as conn:
            return [dict(r) for r in conn.execute(select(staff.c.id,staff.c.name,staff.c.login,staff.c.role,staff.c.active,staff.c.created_at)
                .where(staff.c.merchant_id==m['id']).order_by(staff.c.created_at)).mappings()]

    @app.post('/api/team',status_code=201)
    def add_collector(data:StaffIn,m=Depends(current)):
        aid=uid();hashed=password_hash(data.password)
        with write(engine) as conn:
            conn.execute(staff.insert().values(id=aid,merchant_id=m['id'],name=data.name,login=data.login.lower(),
                password_hash=hashed,role='collector',active=1,created_at=now()))
            log(conn,m['id'],'staff.created',aid,data.name)
        return {'id':aid,'role':'collector'}

    def own_collector(conn,mid,aid):
        a=conn.execute(select(staff).where(staff.c.id==aid,staff.c.merchant_id==mid).with_for_update()).mappings().first()
        if not a:raise HTTPException(404,'Staff account not found.')
        if a['role']!='collector':raise HTTPException(409,'Owner accounts cannot be changed through staff management.')
        return a

    @app.post('/api/team/{aid}/active')
    def activate(aid:str,data:StaffActive,m=Depends(current)):
        with write(engine) as conn:
            own_collector(conn,m['id'],aid)
            conn.execute(staff.update().where(staff.c.id==aid).values(active=int(data.active)))
            revoke_sessions(conn,aid);log(conn,m['id'],'staff.access_changed',aid,data.reason)
        return {'active':data.active,'sessions_revoked':True}

    @app.post('/api/team/{aid}/password')
    def reset(aid:str,data:StaffPassword,m=Depends(current)):
        hashed=password_hash(data.password)
        with write(engine) as conn:
            own_collector(conn,m['id'],aid)
            conn.execute(staff.update().where(staff.c.id==aid).values(password_hash=hashed))
            revoke_sessions(conn,aid);log(conn,m['id'],'staff.password_reset',aid,data.reason)
        return {'reset':True,'sessions_revoked':True}

    @app.get('/api/branding')
    def get_brand(m=Depends(current)):
        with engine.connect() as conn:
            row=conn.execute(select(branding).where(branding.c.merchant_id==m['id'])).mappings().first()
            return dict(row) if row else Brand(display_name=m['name']).model_dump()

    @app.put('/api/branding')
    def set_brand(data:Brand,m=Depends(current)):
        with write(engine) as conn:
            exists=conn.scalar(select(branding.c.merchant_id).where(branding.c.merchant_id==m['id']))
            values={**data.model_dump(),'updated_at':now()}
            if exists:conn.execute(branding.update().where(branding.c.merchant_id==m['id']).values(**values))
            else:conn.execute(branding.insert().values(merchant_id=m['id'],**values))
            log(conn,m['id'],'business.branding_updated',m['id'])
        return {'saved':True}

    @app.get('/api/backup-info')
    def backup_info(m=Depends(current)):
        return {'engine':engine.dialect.name,'method':'offline-operator-cli','archive_format':1,
            'note':'Full-instance backups contain every business. Only the server operator can create or restore them using app.backup; no database download is exposed to tenants.'}
