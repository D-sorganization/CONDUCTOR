import re
def fallback_render(template: str, kwargs: dict) -> str:
    for k, v in kwargs.items():
        template = re.sub(r'\{\{\s*' + re.escape(k) + r'\s*\}\}', str(v), template)
    return template

print(fallback_render('Scan the repository {{ repo }} for TODOs and FIXMEs. Group them by file and suggest a priority order for addressing them.', {'repo': 'D-sorganization/Maxwell-Daemon'}))
