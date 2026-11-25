lines=open('web/index.html',encoding='utf-8').read().splitlines()
for i,l in enumerate(lines,1):
    if '.sidebar' in l and '{' in l:
        print(i,l.strip()[:120])
