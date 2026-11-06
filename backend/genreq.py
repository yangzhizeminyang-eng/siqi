import ast, os, sys
STD=set(sys.stdlib_module_names) | {'app','tests','alembic'}
def top(n): return n.split('.')[0]
third=set()
def walk(root):
    for dp,_,fs in os.walk(root):
        if any(x in dp for x in ('__pycache__',)): continue
        for f in fs:
            if not f.endswith('.py'): continue
            try: tree=ast.parse(open(os.path.join(dp,f),encoding='utf-8',errors='ignore').read())
            except Exception: continue
            for nd in ast.walk(tree):
                if isinstance(nd,ast.Import):
                    for a in nd.names:
                        t=top(a.name)
                        if t not in STD: third.add(t)
                elif isinstance(nd,ast.ImportFrom) and nd.module and nd.level==0:
                    t=top(nd.module)
                    if t not in STD: third.add(t)
for r in ('app','tests'): walk(r)
# 映射到 dist-info 版本(用 importlib.metadata, 兼容 _ 与 -)
import importlib.metadata as md
def version_for(t):
    for cand in (t, t.replace('_','-'), t.lower(), t.lower().replace('_','-')):
        try: return md.version(cand)
        except Exception: pass
    return None
lines=[]
for t in sorted(third):
    v=version_for(t)
    lines.append(f"{t}=={v}" if v else t)
open('requirements.txt','w',encoding='utf-8').write("\n".join(lines)+"\n")
print("\n".join(lines))
print("---count:", len(lines))
