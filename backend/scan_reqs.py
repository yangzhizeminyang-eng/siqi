import ast, os, sys, importlib.util
# 顶层第三方 root 名 → 过滤 stdlib + 本地
def top(name):
    return name.split('.')[0]
def stdlib_top() -> set:
    import pkgutil
    return {m.name for m in pkgutil.iter_modules() if not m.ispkg}
STD=stdlib_top()
third: set = set()
def scan_dir(root):
    for dp,_,fs in os.walk(root):
        if any(x in dp for x in ('__pycache__','.venv','node_modules','.data')): continue
        for f in fs:
            if not f.endswith('.py'): continue
            p=os.path.join(dp,f)
            try:
                tree=ast.parse(open(p,encoding='utf-8').read())
            except Exception: continue
            for node in ast.walk(tree):
                if isinstance(node,ast.Import):
                    for a in node.names: third.add(opts(a.name))
                elif isinstance(node,ast.ImportFrom) and node.module and node.level==0:
                    third.add(opts(node.module))
def opts(n):
    t=top(n)
    return t if (t not in STD and t not in ('app','tests','alembic')) else None
for root in ('app','tests'):
    scan_dir(root)
third.discard(None); third.discard('')
print("第三方顶层包:", sorted(third))
print("count:", len(third))
