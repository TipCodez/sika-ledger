"""Durable verification and reminder worker. No live SMS or payments."""
import argparse
import asyncio
import time
from .main import app
from .jobs import run_once as run_jobs

def run_once():
    return asyncio.run(run_jobs(app))

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--loop',action='store_true')
    args=parser.parse_args()
    while True:
        print(run_once(),flush=True)
        if not args.loop:break
        time.sleep(60)
