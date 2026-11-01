import json,re,glob,os,sys
from datetime import datetime
out={}
cov={}
for f in glob.glob('/workspace/agent-tools/*.txt'):
    lines=open(f).read().split('\n')
    hi=[i for i,l in enumerate(lines[:6]) if l.startswith('Transcript of agent')]
    if not hi: continue
    lines=lines[hi[0]:]
    m=re.match(r'Transcript of agent "([^"]*)" \(([0-9a-f-]+)\), positions (\d+)\D+(\d+) of (\d+)',lines[0])
    if not m: continue
    name,aid,a,b,n=m.groups()
    cov.setdefault(name,[]).append((int(a),int(b)))
    ts=None
    for l in lines[1:]:
        l=l.strip()
        if not l.startswith('{'): continue
        try:o=json.loads(l)
        except Exception: continue
        for c in o.get('message',{}).get('content',[]):
            if c.get('type')=='text' and o['role']=='user':
                t=re.search(r'<timestamp>([^<]*)\(UTC',c['text'])
                if t:
                    ts=datetime.strptime(t.group(1).strip(),'%A, %b %d, %Y, %I:%M %p')
            if c.get('type')=='tool_use' and c.get('name')=='SendToUser':
                inp=c['input']
                if isinstance(inp,str):
                    try: inp=json.loads(inp)
                    except Exception: inp={'content':inp}
                body=inp.get('content') or ''
                if ts is None: continue
                key=(aid,ts.isoformat(),hash(body))
                out[key]={'agent':aid,'name':name,'ts':ts.isoformat(),'body':body}
os.makedirs('/workspace/dash-src/transcripts',exist_ok=True)
msgs=sorted(out.values(),key=lambda x:(x['name'],x['ts']))
json.dump(msgs,open('/workspace/dash-src/transcripts/sendtouser.json','w'),indent=1)
for k,v in sorted(cov.items()): print(k, sorted(v)[:1], len(v))
from collections import defaultdict
d=defaultdict(list)
for m in msgs: d[m['name']].append(m['ts'][5:16]+(' *' if re.search(r'^\s*1\.',m['body'],re.M) else ''))
for k,v in d.items(): print(k,len(v),' '.join(v))
