import sqlite3,sys
c=sqlite3.connect(sys.argv[2] if len(sys.argv)>2 else 'data/briefs.db')
for r in c.execute(sys.argv[1]): print(' | '.join(str(x) for x in r))
