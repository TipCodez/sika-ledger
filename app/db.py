"""Append-only ledger. SQLite for a laptop; PostgreSQL via DATABASE_URL.

All writes take a SQLite immediate transaction or PostgreSQL row lock where
balances are consumed. Money is always integer pesewas, never floating point.
"""
from contextlib import contextmanager
from sqlalchemy import (MetaData, Table, Column, String, Integer, Text,
                        ForeignKey, UniqueConstraint, CheckConstraint, create_engine)
from sqlalchemy import event

meta = MetaData()
merchants = Table('merchants', meta,
    Column('id', String, primary_key=True), Column('name', String, nullable=False),
    Column('login', String, nullable=False, unique=True),
    Column('password_hash', String, nullable=False), Column('created_at', String, nullable=False))
sessions = Table('sessions', meta,
    Column('token_hash', String, primary_key=True),
    Column('merchant_id', ForeignKey('merchants.id'), nullable=False),
    Column('expires_at', String, nullable=False))
customers = Table('customers', meta,
    Column('id', String, primary_key=True),
    Column('merchant_id', ForeignKey('merchants.id'), nullable=False),
    Column('name', String, nullable=False), Column('phone', String, nullable=False),
    Column('do_not_contact', Integer, nullable=False, default=0),
    UniqueConstraint('merchant_id', 'phone'))
debts = Table('debts', meta,
    Column('id', String, primary_key=True),
    Column('merchant_id', ForeignKey('merchants.id'), nullable=False),
    Column('customer_id', ForeignKey('customers.id'), nullable=False),
    Column('principal_minor', Integer, nullable=False),
    Column('due_date', String, nullable=False), Column('description', String, nullable=False),
    Column('public_token', String, nullable=False, unique=True),
    Column('paused', Integer, nullable=False, default=0),
    Column('disputed', Integer, nullable=False, default=0),
    Column('created_at', String, nullable=False), CheckConstraint('principal_minor > 0'))
entries = Table('ledger', meta,
    Column('id', String, primary_key=True),
    Column('merchant_id', ForeignKey('merchants.id'), nullable=False),
    Column('debt_id', ForeignKey('debts.id'), nullable=False),
    Column('kind', String, nullable=False), Column('amount_minor', Integer, nullable=False),
    Column('reference', String, nullable=False), Column('note', String, nullable=False),
    Column('created_at', String, nullable=False),
    UniqueConstraint('merchant_id', 'reference'), CheckConstraint('amount_minor != 0'))
payments = Table('payments', meta,
    Column('id', String, primary_key=True),
    Column('merchant_id', ForeignKey('merchants.id'), nullable=False),
    Column('debt_id', ForeignKey('debts.id'), nullable=False),
    Column('reference', String, nullable=False, unique=True),
    Column('amount_minor', Integer, nullable=False),
    Column('applied_minor', Integer, nullable=False, default=0),
    Column('unallocated_minor', Integer, nullable=False, default=0),
    Column('source', String, nullable=False), Column('status', String, nullable=False),
    Column('created_at', String, nullable=False), Column('confirmed_at', String),
    Column('checkout_url', String),
    CheckConstraint('amount_minor > 0'))
disputes = Table('disputes', meta,
    Column('id', String, primary_key=True),
    Column('merchant_id', ForeignKey('merchants.id'), nullable=False),
    Column('debt_id', ForeignKey('debts.id'), nullable=False),
    Column('reason', String, nullable=False), Column('message', String, nullable=False),
    Column('status', String, nullable=False), Column('resolution', String),
    Column('created_at', String, nullable=False))
outbox = Table('outbox', meta,
    Column('id', String, primary_key=True),
    Column('merchant_id', ForeignKey('merchants.id'), nullable=False),
    Column('debt_id', ForeignKey('debts.id'), nullable=False),
    Column('day_key', String, nullable=False), Column('body', Text, nullable=False),
    Column('status', String, nullable=False), Column('created_at', String, nullable=False),
    UniqueConstraint('debt_id', 'day_key'))
audit = Table('audit', meta,
    Column('id', String, primary_key=True),
    Column('merchant_id', ForeignKey('merchants.id'), nullable=False),
    Column('action', String, nullable=False), Column('entity_id', String, nullable=False),
    Column('detail', Text, nullable=False), Column('created_at', String, nullable=False))
susu = Table('susu_accounts', meta,
    Column('id', String, primary_key=True),
    Column('merchant_id', ForeignKey('merchants.id'), nullable=False),
    Column('customer_id', ForeignKey('customers.id'), nullable=False),
    Column('label', String, nullable=False), Column('daily_target_minor', Integer, nullable=False),
    Column('created_at', String, nullable=False), CheckConstraint('daily_target_minor > 0'))
contributions = Table('susu_ledger', meta,
    Column('id', String, primary_key=True),
    Column('merchant_id', ForeignKey('merchants.id'), nullable=False),
    Column('account_id', ForeignKey('susu_accounts.id'), nullable=False),
    Column('amount_minor', Integer, nullable=False), Column('kind', String, nullable=False),
    Column('reference', String, nullable=False), Column('note', String, nullable=False),
    Column('created_at', String, nullable=False),
    UniqueConstraint('merchant_id', 'reference'), CheckConstraint('amount_minor != 0'))
proposals = Table('agent_proposals', meta,
    Column('id', String, primary_key=True),
    Column('merchant_id', ForeignKey('merchants.id'), nullable=False),
    Column('payload', Text, nullable=False), Column('status', String, nullable=False),
    Column('expires_at', String, nullable=False), Column('created_at', String, nullable=False))
webhook_inbox = Table('webhook_inbox', meta,
    Column('id', String, primary_key=True),
    Column('merchant_id', ForeignKey('merchants.id'), nullable=False),
    Column('event_key', String, unique=True, nullable=False),
    Column('reference', String, nullable=False),
    Column('status', String, nullable=False),
    Column('attempts', Integer, nullable=False, default=0),
    Column('next_attempt_at', String, nullable=False), Column('locked_until', String),
    Column('last_error', String), Column('created_at', String, nullable=False),
    Column('processed_at', String))
sms_attempts = Table('sms_attempts', meta,
    Column('id', String, primary_key=True),
    Column('merchant_id', ForeignKey('merchants.id'), nullable=False),
    Column('outbox_id', ForeignKey('outbox.id'), nullable=False),
    Column('mode', String, nullable=False), Column('status', String, nullable=False),
    Column('provider_id', String), Column('error', String),
    Column('started_at', String, nullable=False), Column('finished_at', String))
conversations = Table('agent_conversations', meta,
    Column('id', String, primary_key=True),
    Column('merchant_id', ForeignKey('merchants.id'), nullable=False),
    Column('created_at', String, nullable=False), Column('expires_at', String, nullable=False),
    Column('busy_token', String), Column('busy_until', String))
turns = Table('agent_turns', meta,
    Column('id', String, primary_key=True),
    Column('conversation_id', ForeignKey('agent_conversations.id'), nullable=False),
    Column('merchant_id', ForeignKey('merchants.id'), nullable=False),
    Column('request_id', String, nullable=False), Column('user_message', Text, nullable=False),
    Column('result', Text, nullable=False), Column('created_at', String, nullable=False),
    UniqueConstraint('conversation_id','request_id'))
susu_links = Table('susu_links', meta,
    Column('account_id', ForeignKey('susu_accounts.id'), primary_key=True),
    Column('public_token', String, nullable=False, unique=True),
    Column('created_at', String, nullable=False))
susu_payments = Table('susu_payments', meta,
    Column('id', String, primary_key=True),
    Column('merchant_id', ForeignKey('merchants.id'), nullable=False),
    Column('account_id', ForeignKey('susu_accounts.id'), nullable=False),
    Column('reference', String, nullable=False, unique=True),
    Column('amount_minor', Integer, nullable=False),
    Column('source', String, nullable=False), Column('status', String, nullable=False),
    Column('created_at', String, nullable=False), Column('confirmed_at', String),
    Column('checkout_url', String), CheckConstraint('amount_minor > 0'))
sms_callback_inbox = Table('sms_callback_inbox', meta,
    Column('id', String, primary_key=True),
    Column('merchant_id', ForeignKey('merchants.id'), nullable=False),
    Column('attempt_id', ForeignKey('sms_attempts.id'), nullable=False),
    Column('provider_id', String, nullable=False),
    Column('claimed_status', String, nullable=False),
    Column('status', String, nullable=False), Column('attempts', Integer, nullable=False,default=0),
    Column('next_attempt_at', String, nullable=False), Column('locked_until', String),
    Column('last_error', String), Column('created_at', String, nullable=False),
    Column('processed_at', String), UniqueConstraint('attempt_id','claimed_status'))
sms_reports = Table('sms_delivery_reports', meta,
    Column('attempt_id', ForeignKey('sms_attempts.id'), primary_key=True),
    Column('merchant_id', ForeignKey('merchants.id'), nullable=False),
    Column('provider_id', String, nullable=False),
    Column('status', String, nullable=False), Column('source', String, nullable=False),
    Column('checked_at', String, nullable=False))
debt_changes = Table('debt_changes', meta,
    Column('id', String, primary_key=True),
    Column('merchant_id', ForeignKey('merchants.id'), nullable=False),
    Column('debt_id', ForeignKey('debts.id'), nullable=False),
    Column('request_key', String, nullable=False), Column('payload',Text,nullable=False),
    Column('kind',String,nullable=False),Column('reason',String,nullable=False),
    Column('created_at',String,nullable=False),UniqueConstraint('merchant_id','request_key'))
debt_flags = Table('debt_flags',meta,
    Column('debt_id',ForeignKey('debts.id'),primary_key=True),
    Column('cancelled_at',String,nullable=False))
credit_reviews = Table('credit_reviews',meta,
    Column('payment_id',ForeignKey('payments.id'),primary_key=True),
    Column('merchant_id',ForeignKey('merchants.id'),nullable=False),
    Column('note',Text,nullable=False),Column('created_at',String,nullable=False))

staff = Table('staff_accounts',meta,
    Column('id',String,primary_key=True),Column('merchant_id',ForeignKey('merchants.id'),nullable=False),
    Column('name',String,nullable=False),Column('login',String,nullable=False,unique=True),
    Column('password_hash',String,nullable=False),Column('role',String,nullable=False),
    Column('active',Integer,nullable=False,default=1),Column('created_at',String,nullable=False),
    CheckConstraint("role IN ('owner','collector')"))
session_actors = Table('session_actors',meta,
    Column('token_hash',ForeignKey('sessions.token_hash'),primary_key=True),
    Column('actor_id',ForeignKey('staff_accounts.id'),nullable=False))
audit_actors = Table('audit_actors',meta,
    Column('audit_id',ForeignKey('audit.id'),primary_key=True),
    Column('actor_id',ForeignKey('staff_accounts.id'),nullable=False),
    Column('name',String,nullable=False),Column('role',String,nullable=False))
contact_preferences = Table('contact_preferences',meta,
    Column('customer_id',ForeignKey('customers.id'),primary_key=True),
    Column('sms_consent',Integer,nullable=False),Column('verified_phone',String,nullable=False),
    Column('evidence',Text,nullable=False),Column('updated_at',String,nullable=False))
contact_history = Table('contact_history',meta,
    Column('id',String,primary_key=True),Column('merchant_id',ForeignKey('merchants.id'),nullable=False),
    Column('customer_id',ForeignKey('customers.id'),nullable=False),Column('payload',Text,nullable=False),
    Column('reason',Text,nullable=False),Column('created_at',String,nullable=False))
plans = Table('repayment_plans',meta,
    Column('id',String,primary_key=True),Column('merchant_id',ForeignKey('merchants.id'),nullable=False),
    Column('debt_id',ForeignKey('debts.id'),nullable=False),Column('baseline_minor',Integer,nullable=False),
    Column('status',String,nullable=False),Column('reason',Text,nullable=False),
    Column('request_key',String,nullable=False),Column('payload',Text,nullable=False),
    Column('created_at',String,nullable=False),UniqueConstraint('merchant_id','request_key'))
instalments = Table('plan_instalments',meta,
    Column('id',String,primary_key=True),Column('plan_id',ForeignKey('repayment_plans.id'),nullable=False),
    Column('sequence',Integer,nullable=False),Column('due_date',String,nullable=False),
    Column('amount_minor',Integer,nullable=False),UniqueConstraint('plan_id','sequence'),
    CheckConstraint('amount_minor > 0'))
branding = Table('business_branding',meta,
    Column('merchant_id',ForeignKey('merchants.id'),primary_key=True),
    Column('display_name',String,nullable=False),Column('address',String,nullable=False),
    Column('contact',String,nullable=False),Column('footer',String,nullable=False),
    Column('accent',String,nullable=False),Column('updated_at',String,nullable=False))

def make_engine(url):
    engine = create_engine(url, connect_args={'check_same_thread': False, 'timeout': 20}
                           if url.startswith('sqlite') else {}, pool_pre_ping=True)
    if url.startswith('sqlite'):
        @event.listens_for(engine, 'connect')
        def configure_sqlite(connection, record):
            if engine.url.database and engine.url.database!=':memory:':
                from .maintenance_lock import DatabaseLease
                record.info['sika_lease']=DatabaseLease(engine.url.database)
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA journal_mode=WAL')
        @event.listens_for(engine, 'close')
        def release_sqlite(connection,record):
            lease=record.info.pop('sika_lease',None)
            if lease:lease.close()
    meta.create_all(engine)
    return engine

@contextmanager
def write(engine):
    with engine.connect() as conn:
        if engine.dialect.name == 'sqlite':
            conn.exec_driver_sql('BEGIN IMMEDIATE')
        else:
            conn.begin()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
