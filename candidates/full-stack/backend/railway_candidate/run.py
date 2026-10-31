"""Start synthetic-only candidate when explicitly deployed; imports do not bind."""
import os
import uvicorn
from railway_candidate.gateway import application

def main():
    value=os.environ.get('PORT','')
    if not value.isascii() or not value.isdecimal() or not 1<=int(value)<=65535:
        raise ValueError('Railway PORT must be an integer in 1..65535')
    app=application()  # validate all configuration before opening the listener
    uvicorn.run(app,host='0.0.0.0',port=int(value),proxy_headers=False,access_log=False,
                server_header=False,date_header=False)

if __name__=='__main__': main()
