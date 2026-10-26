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

def make_engine(url):
    engine = create_engine(url, connect_args={'check_same_thread': False, 'timeout': 20}
                           if url.startswith('sqlite') else {}, pool_pre_ping=True)
    if url.startswith('sqlite'):
        @event.listens_for(engine, 'connect')
        def configure_sqlite(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA journal_mode=WAL')
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
