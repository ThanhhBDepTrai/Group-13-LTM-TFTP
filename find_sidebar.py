lines=open('web/index.html',encoding='utf-8').read().splitlines()
for i,l in enumerate(lines,1):
    if 'sidebar' in l.lower() and ('id=' in l or 'toggleSidebar' in l or 'function' in l):
        print(i,l.strip()[:160])
