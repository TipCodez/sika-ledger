"""Local entrypoint that respects the configured browser origin and port."""
import argparse
import os
from urllib.parse import urlparse
from dotenv import load_dotenv
import uvicorn

if __name__=='__main__':
    load_dotenv()
    origin=urlparse(os.getenv('PUBLIC_BASE_URL','http://127.0.0.1:8000'))
    local_host=origin.hostname if origin.hostname in ('127.0.0.1','localhost','::1') else '127.0.0.1'
    local_port=origin.port or (443 if origin.scheme=='https' else 80)
    parser=argparse.ArgumentParser()
    parser.add_argument('--host',default=local_host)
    parser.add_argument('--port',type=int,default=int(os.getenv('PORT',str(local_port))))
    parser.add_argument('--reload',action='store_true')
    args,_=parser.parse_known_args()
    print(f'Configured browser origin: {origin.geturl()}',flush=True)
    uvicorn.run('app.main:app',host=args.host,port=args.port,reload=args.reload)
